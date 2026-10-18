"""Offline regressions for RAW completeness blockers. No real Meta API."""
import json
import sqlite3
from pathlib import Path
from tempfile import TemporaryDirectory
from argparse import Namespace
import sys
import unittest

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'raw_v2'))
import collector as col
import audit as aud
from test_daily_scheduler import create_roster,make_page


class CompletenessTests(unittest.TestCase):
    def setUp(self):
        self.tmp=TemporaryDirectory();self.addCleanup(self.tmp.cleanup)
        self.base=Path(self.tmp.name)
        self.source=self.base/'account_list.csv'
        create_roster(self.source)
        self.roster,self.digest=col.load_accounts(self.source)
        self.out=self.base/'store'
        self.db=col.connect(self.out/'collection.sqlite',self.roster,self.digest)
        self.addCleanup(self.db.close)

    def account(self,key='KR_001'):
        return dict(self.db.execute('SELECT * FROM accounts WHERE account_key=?',(key,)).fetchone())

    def test_ancient_front_post_must_not_end_history(self):
        p=make_page([1,2,3,4],after='MORE_HISTORY')
        p['business_discovery']['media']['data'][-1]['timestamp']='2020-01-01T00:00:00+00:00'
        col.insert_success(self.db,self.account(),'front',p,'R1','r1',col.now(),365,limit=4)
        row=self.account()
        self.assertEqual(row['backfill_done'],0)
        self.assertEqual(row['backfill_cursor'],'MORE_HISTORY')

    def test_history_evidence_crosses_cutoff_only_on_actual_history(self):
        p=make_page([1,2,3,4],after='NEXT')
        col.insert_success(self.db,self.account(),'front',p,'R1','r1',col.now(),365,limit=4)
        old=make_page([5,6,7,8],after='OLDER')
        old['business_discovery']['media']['data'][-1]['timestamp']='2020-01-01T00:00:00+00:00'
        col.insert_success(self.db,self.account(),'history',old,'R2','r2',col.now(),365,limit=4)
        row=self.db.execute('SELECT completion_basis FROM history_page_evidence WHERE request_id=?',('r2',)).fetchone()
        self.assertEqual(row[0],'cutoff_crossed')
        self.assertEqual(self.account()['backfill_done'],1)

    def test_history_unsorted_rejected_without_mutation(self):
        p=make_page([1,2,3,4],after='OLDER')
        items=p['business_discovery']['media']['data']
        items[0]['timestamp']='2025-01-01T00:00:00+00:00'
        items[1]['timestamp']='2026-01-01T00:00:00+00:00'
        with self.assertRaisesRegex(ValueError,'not descending'):
            col.insert_success(self.db,self.account(),'history',p,'R','bad',col.now(),365,limit=4)
        self.assertEqual(self.db.execute('SELECT COUNT(*) FROM posts').fetchone()[0],0)

    def test_nonadjacent_history_cursor_cycle_refused(self):
        col.insert_success(self.db,self.account(),'front',make_page([1],after='CUR1'),
                           'R1','r1',col.now(),365,limit=4)
        col.insert_success(self.db,self.account(),'history',make_page([2],after='CUR2'),
                           'R2','r2',col.now(),365,limit=4)
        with self.assertRaisesRegex(ValueError,'cursor cycle'):
            col.insert_success(self.db,self.account(),'history',make_page([3],after='CUR1'),
                               'R3','r3',col.now(),365,limit=4)
        self.assertEqual(self.account()['backfill_cursor'],'CUR2')

    def test_cross_page_history_newer_timestamp_refused(self):
        col.insert_success(self.db,self.account(),'front',make_page([1],after='CUR1'),
                           'R1','r1',col.now(),365,limit=4)
        first=make_page([2],after='CUR2')
        first['business_discovery']['media']['data'][0]['timestamp']='2025-12-01T00:00:00+00:00'
        col.insert_success(self.db,self.account(),'history',first,'R2','r2',col.now(),365,limit=4)
        newer=make_page([3],after='CUR3')
        newer['business_discovery']['media']['data'][0]['timestamp']='2026-10-01T00:00:00+00:00'
        with self.assertRaisesRegex(ValueError,'chronology unverified'):
            col.insert_success(self.db,self.account(),'history',newer,'R3','r3',col.now(),365,limit=4)
        self.assertEqual(self.account()['backfill_cursor'],'CUR2')

    def test_missing_empty_children_partial_and_later_repair(self):
        p=make_page([1],after=None)
        entry=p['business_discovery']['media']['data'][0]
        entry['media_type']='CAROUSEL_ALBUM'
        col.insert_success(self.db,self.account(),'front',p,'R1','r1',col.now(),365,limit=4)
        self.assertEqual(tuple(self.db.execute('SELECT child_rows,coverage FROM carousel_status').fetchone()),(0,'partial'))
        p2=make_page([1],after=None)
        q=p2['business_discovery']['media']['data'][0]
        q['media_type']='CAROUSEL_ALBUM'
        q['children']={'data':[{'id':str(10**18+9),'media_type':'IMAGE'}]}
        col.insert_success(self.db,self.account(),'front',p2,'R2','r2',col.now(),365,limit=4)
        self.assertEqual(tuple(self.db.execute('SELECT child_rows,coverage FROM carousel_status').fetchone()),(1,'complete'))
        self.assertEqual(self.db.execute('SELECT COUNT(*) FROM post_media').fetchone()[0],1)

    def test_account_mismatch_blocks_all_mutations(self):
        wrong=make_page([1],after=None)
        wrong['business_discovery']['username']='different_account'
        with self.assertRaisesRegex(ValueError,'identity mismatch'):
            col.insert_success(self.db,self.account(),'front',wrong,'R','bad',col.now(),365,limit=4)
        self.assertEqual(self.db.execute('SELECT COUNT(*) FROM posts').fetchone()[0],0)
        col.insert_success(self.db,self.account(),'front',make_page([1],after=None),'R1','good',col.now(),365,limit=4)
        mismatched=make_page([1],after=None)
        mismatched['business_discovery']['id']='123987'
        with self.assertRaisesRegex(ValueError,'identity mismatch'):
            col.insert_success(self.db,self.account(),'front',mismatched,'R2','bad',col.now(),365,limit=4)

    def test_readonly_audit_warns_on_unverified_completion(self):
        with self.db:
            self.db.execute("UPDATE accounts SET backfill_done=1,backfill_cursor='OLDER' WHERE account_key='KR_001'")
        report_dir=self.base/'audit_report'
        result=aud.audit(self.out,report_dir)
        self.assertEqual(result['status'],'WARN')
        self.assertFalse(result['ready_for_unattended_collection'])
        self.assertTrue((report_dir/'summary.json').exists())
        report=(report_dir/'coverage_findings.csv').read_text(encoding='utf-8-sig')
        self.assertIn('UNVERIFIED_LEGACY_COMPLETION',report)
        self.assertFalse((self.out/'collection.sqlite').stat().st_size==0)

    def test_batch_journal_prevents_unobserved_retry(self):
        self.db.close()
        env=self.base/'env.local'
        env.write_text('META_ACCESS_TOKEN=SECRET\nMETA_IG_USER_ID=12345',encoding='utf-8')
        args=Namespace(source=self.source,env_file=env,output_dir=self.out,
                       live=True,mode='front',batch_size=1,max_batches=1,
                       limit=4,window_days=7,history_days=365,stop_usage=85,
                       extended_fields=False,cycle_minutes=65)
        def network_timeout(*_):
            raise TimeoutError('unknown outcome')
        self.assertEqual(col.run(args,network_timeout),2)
        with self.assertRaisesRegex(ValueError,'unresolved prior batch'):
            col.run(args,network_timeout)
        report=aud.audit(self.out)
        self.assertEqual(report['status'],'FAIL')
        self.assertEqual(report['unresolved_batches'],1)


if __name__=='__main__':
    unittest.main()
