import json
from pathlib import Path
import shutil
import subprocess
import unittest


ROOT = Path(__file__).parents[1]


class P50N8nExportTests(unittest.TestCase):
    def test_workflow_is_inactive_bounded_and_uses_p50_runner(self):
        workflow = json.loads((ROOT / 'kq3_294_collection' /
                               'n8n_p50_daily_collection.json').read_text(encoding='utf-8'))
        self.assertFalse(workflow['active'])
        self.assertEqual(workflow['id'], 'p50-daily-collection-v1')
        self.assertEqual(workflow['settings']['timezone'], 'Asia/Seoul')
        nodes = {node['name']: node for node in workflow['nodes']}
        self.assertIn('n8n-nodes-base.scheduleTrigger',
                      {node['type'] for node in workflow['nodes']})
        command = nodes['Call isolated P50 runner']['parameters']['command']
        self.assertIn('p50_n8n_tick.ps1', command)
        self.assertNotIn('p6_n8n_tick.ps1', command)
        self.assertTrue(command.isascii())
        launcher = (ROOT / 'raw_v2' / 'p50_n8n_tick.ps1').read_text(encoding='utf-8')
        self.assertIn('three_api_p50_n8n_tick', launcher)
        self.assertIn('OHORA_P50_ENGINE_ROOT', launcher)
        self.assertIn('$expectedEngineCommit', launcher)
        self.assertIn('10ebaf895e228cd1ed09cc349d9fcb5ddcd139fb', launcher)
        self.assertIn('OHORA_P50_LIVE_APPROVED', launcher)
        self.assertIn("-ceq 'YES'", launcher)
        self.assertNotIn('OHORA_P6_PILOT', launcher)
        self.assertNotIn('OHORA_P6_LIVE_APPROVED', launcher)

    def test_result_classifier_does_not_expose_raw_stdout(self):
        workflow = json.loads((ROOT / 'kq3_294_collection' /
                               'n8n_p50_daily_collection.json').read_text(encoding='utf-8'))
        node = next(n for n in workflow['nodes']
                    if n['name'] == 'Validate P50 runner result')
        code = node['parameters']['jsCode']
        self.assertIn('JSON.parse', code)
        self.assertIn('committed_pages', code)
        self.assertIn('account_failures', code)
        self.assertIn('live_meta_called', code)
        self.assertIn('storage_verification', code)
        self.assertIn("r.action==='MANUAL_REVIEW'", code)
        self.assertIn("r.action==='WAIT_EXPLICIT_LIVE_APPROVAL'", code)
        self.assertIn("typeof r.live_meta_called==='boolean'?r.live_meta_called:null", code)
        self.assertIn("alert_required:alertRequired", code)
        self.assertIn("alert_code:alertCode", code)
        self.assertNotIn('stdout:', code)

    def test_review_states_fail_the_n8n_execution_with_redacted_context(self):
        workflow = json.loads((ROOT / 'kq3_294_collection' /
                               'n8n_p50_daily_collection.json').read_text(encoding='utf-8'))
        nodes = {node['name']: node for node in workflow['nodes']}
        gate = nodes['Operator review required?']
        self.assertEqual(gate['type'], 'n8n-nodes-base.if')
        self.assertEqual(
            gate['parameters']['conditions']['conditions'][0]['leftValue'],
            '={{ $json.alert_required }}')
        stop = nodes['Stop P50 run for operator review']
        self.assertEqual(stop['type'], 'n8n-nodes-base.stopAndError')
        self.assertNotIn('={{', stop['parameters']['errorMessage'])
        self.assertNotIn('stdout', stop['parameters']['errorMessage'])
        connections = workflow['connections']
        self.assertEqual(
            connections['Validate P50 runner result']['main'][0][0]['node'],
            'Operator review required?')
        routes = connections['Operator review required?']['main']
        self.assertEqual(routes[0][0]['node'], 'Stop P50 run for operator review')
        self.assertEqual(routes[1][0]['node'], 'Keep result in n8n execution')

    def test_recovery_only_result_is_progress_without_claiming_a_meta_call(self):
        node = shutil.which('node')
        if node is None:
            self.skipTest('Node.js is required to execute the n8n Code node fixture')
        workflow = json.loads((ROOT / 'kq3_294_collection' /
                               'n8n_p50_daily_collection.json').read_text(encoding='utf-8'))
        code = next(n for n in workflow['nodes']
                    if n['name'] == 'Validate P50 runner result')['parameters']['jsCode']
        runner = (
            "const fs=require('fs');"
            "const p=JSON.parse(fs.readFileSync(0,'utf8'));"
            "const out=new Function('$input',p.code)({all:()=>[{json:{stdout:JSON.stringify(p.result)}}]});"
            "process.stdout.write(JSON.stringify(out[0].json));"
        )
        cases = [
            ('recovered-only', {
                'action': 'LIVE_TICK_COMPLETE', 'mode': 'P50_EXPLICIT_SERIAL',
                'status': 'recovered', 'committed_pages': 0, 'recovered_pages': 1,
                'account_failures': 0, 'attempted_steps': 1, 'live_meta_called': False,
                'storage_verification': {'status': 'verified', 'receipt_delta': 1},
            }, 'collection_progress', False),
            ('invalid-recovery-count', {
                'action': 'LIVE_TICK_COMPLETE', 'mode': 'P50_EXPLICIT_SERIAL',
                'status': 'recovered', 'committed_pages': 0, 'recovered_pages': -1,
                'account_failures': 0, 'attempted_steps': 1, 'live_meta_called': False,
                'storage_verification': {'status': 'verified', 'receipt_delta': 1},
            }, 'manual_review', False),
            ('new-page', {
                'action': 'LIVE_TICK_COMPLETE', 'mode': 'P50_EXPLICIT_SERIAL',
                'status': 'page_committed', 'committed_pages': 1, 'recovered_pages': 0,
                'account_failures': 0, 'attempted_steps': 1, 'live_meta_called': True,
                'storage_verification': {'status': 'verified', 'receipt_delta': 1},
            }, 'collection_progress', True),
            ('serial-wait', {
                'action': 'LIVE_TICK_COMPLETE', 'mode': 'P50_EXPLICIT_SERIAL',
                'status': 'serial_busy', 'committed_pages': 0, 'recovered_pages': 0,
                'account_failures': 0, 'attempted_steps': 1, 'live_meta_called': False,
                'storage_verification': {'status': 'verified', 'receipt_delta': 0},
            }, 'waiting', False),
        ]
        for name, result, expected_state, expected_call in cases:
            with self.subTest(case=name):
                completed = subprocess.run(
                    [node, '-e', runner], input=json.dumps({'code': code, 'result': result}),
                    text=True, capture_output=True, check=True)
                actual = json.loads(completed.stdout)
                self.assertEqual(actual['state'], expected_state)
                self.assertEqual(actual['live_meta_called'], expected_call)
                if name == 'recovered-only':
                    self.assertFalse(actual['alert_required'])


if __name__ == '__main__':
    unittest.main()
