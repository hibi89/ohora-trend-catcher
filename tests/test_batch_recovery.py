"""No-network archived-batch reconciliation and backup acceptance tests."""
import json
from pathlib import Path
import sqlite3
import sys
from tempfile import TemporaryDirectory
import unittest

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'raw_v2'))
import collector as c
import backup as bak
import recover
from test_daily_scheduler import create_roster,make_page


class RecoveryTests(unittest.TestCase):
    def setUp(self):
        self.tmp=TemporaryDirectory();self.addCleanup(self.tmp.cleanup)
        self.base=Path(self.tmp.name)
        self.src=self.base/'accounts.csv';create_roster(self.src)
        rows,sha=c.load_accounts(self.src)
        self.out=self.base/'store'
        self.db=c.connect(self.out/'collection.sqlite',rows,sha)
        self.addCleanup(self.db.close)
        self.run_id='recovery_run'
        self.slot=c.active_slot(self.db)
        with self.db:
            self.db.execute("""INSERT INTO runs(run_id,started_at,api_version,mode,status)
                VALUES (?,?,?,?,?)""",(self.run_id,c.now(),'v26.0','daily','started'))

    def planned_batch(self, archived=True):
        with self.db:
            self.db.execute("""INSERT INTO batch_attempts
                (run_id,batch_no,slot_id,request_json,started_at,status)
                VALUES (?,?,?,?,?,?)""",
                (self.run_id,0,self.slot,json.dumps([{
                    'account_key':'KR_001','ordinal':1,'phase':'front','requested_cursor':None}]),
                    c.now(),'response_saved' if archived else 'prepared'))
        if archived:
            body=[{'code':200,'body':json.dumps(make_page([1],after='MORE'))}]
            c.persist_response(self.out,self.run_id,'batch_0000',body,'')

    def test_backup_and_replay_with_no_api(self):
        self.planned_batch()
        before=recover.recover(self.out,None,None,False)
        self.assertEqual(before['batches'][0]['resolution'],'REPLAYABLE')
        self.assertEqual(self.db.execute('SELECT COUNT(*) FROM posts').fetchone()[0],0)
        backup=bak.backup_store(self.out,self.base/'backups')
        self.db.close()
        result=recover.recover(self.out,self.src,backup,True)
        self.assertEqual(result['replayed'],1)
        with sqlite3.connect(self.out/'collection.sqlite') as db:
            self.assertEqual(db.execute('SELECT COUNT(*) FROM posts').fetchone()[0],1)
            self.assertEqual(db.execute('SELECT COUNT(*) FROM post_observations').fetchone()[0],1)
            self.assertEqual(db.execute('SELECT COUNT(*) FROM request_log').fetchone()[0],1)
            self.assertEqual(db.execute("SELECT status FROM batch_attempts").fetchone()[0],'complete')
            self.assertEqual(db.execute("SELECT status FROM slots").fetchone()[0],'cooldown')
        self.assertEqual(recover.recover(self.out,None,None,False)['batches'],[])

    def test_unknown_remote_outcome_never_retried(self):
        self.planned_batch(archived=False)
        cases=recover.recover(self.out,None,None,False)['batches']
        self.assertEqual(cases[0]['resolution'],'UNKNOWN_REMOTE_OUTCOME')
        backup=bak.backup_store(self.out,self.base/'backup')
        self.db.close()
        with self.assertRaisesRegex(ValueError,'Unknown remote outcome'):
            recover.recover(self.out,self.src,backup,True)
        with sqlite3.connect(self.out/'collection.sqlite') as db:
            self.assertEqual(db.execute('SELECT COUNT(*) FROM request_log').fetchone()[0],0)


if __name__=='__main__':
    unittest.main()
