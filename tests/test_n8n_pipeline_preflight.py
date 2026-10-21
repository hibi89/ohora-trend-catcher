"""Offline contract tests; never use network, Meta, or operational SQLite."""
import contextlib
import csv
import hashlib
import io
import json
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from src import pipeline


class BridgeTest(unittest.TestCase):
    def setUp(self):
        self.tmp = TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.base = Path(self.tmp.name)
        self.roster = self.base / 'account_list.csv'
        with self.roster.open('w', encoding='utf-8', newline='') as file:
            writer = csv.DictWriter(file, fieldnames=['account_key','username','country'])
            writer.writeheader()
            for idx,country in enumerate(pipeline.COUNTRIES, 1):
                writer.writerow({'account_key': f'ROW_{idx:03d}', 'username':f'account_{idx:03d}', 'country':country})
        files = {lane: str(self.base / f'{lane}.env') for lane in 'ABC'}
        for path in files.values():
            Path(path).write_text('SENTINEL_SECRET=private_value\n')
        self.config = self.base / 'ohora.json'
        self.settings = {
            'roster_csv':str(self.roster),
            'roster_sha256':hashlib.sha256(self.roster.read_bytes()).hexdigest(),
            'data_dir': str(self.base),
            'credential_files':files,
        }
        self.save()

    def save(self):
        self.config.write_text(json.dumps(self.settings), encoding='utf-8')

    def invoke(self, mode='--dry-run', pages='50', seconds='240'):
        output = io.StringIO()
        with contextlib.redirect_stdout(output):
            result = pipeline.main([
                '--config',str(self.config), '--tick','--json',
                '--max-pages',str(pages),'--max-seconds',str(seconds),mode])
        value=output.getvalue()
        self.assertNotIn('SENTINEL_SECRET',value)
        self.assertEqual(result,0)
        self.assertEqual(len(value.splitlines()),1)
        return json.loads(value)

    def test_valid_read_only_dry_run(self):
        self.assertEqual(self.invoke(),{
            'schema_version':1,'ok':True,'state':'DRY_RUN','needs_human':False,
            'ready_at':None,'pages_committed':0,'http_requests':0})

    def test_offline_pilot_integration_without_secrets(self):
        # The active P6 pilot preflight must not create a database or make HTTP.
        from unittest.mock import patch
        with patch('urllib.request.urlopen', side_effect=AssertionError('HTTP forbidden')):
            result = self.invoke()
        self.assertEqual(result['state'], 'DRY_RUN')
        self.assertFalse(any(p.suffix == '.sqlite' for p in self.base.rglob('*')))

    def test_live_always_blocked(self):
        self.assertEqual(self.invoke(mode='--live')['state'],'CONFIG_REQUIRED')

    def test_bad_csv_fails_closed(self):
        with self.roster.open('a') as file:
            file.write('EXTRA,a,KR\n')
        self.assertEqual(self.invoke()['state'],'CONFIG_REQUIRED')

    def test_missing_credential_fails_closed(self):
        Path(self.settings['credential_files']['C']).unlink()
        self.assertEqual(self.invoke()['state'],'CONFIG_REQUIRED')

    def test_bound_checks(self):
        for pages,seconds in [('0','240'),('883','240'),('50','0'),('50','241')]:
            self.assertEqual(self.invoke(pages=pages,seconds=seconds)['state'],'CONFIG_REQUIRED')

    def test_no_created_files(self):
        before={str(p) for p in self.base.rglob('*')}
        self.invoke()
        after={str(p) for p in self.base.rglob('*')}
        self.assertEqual(before,after)


if __name__=='__main__':
    unittest.main()
