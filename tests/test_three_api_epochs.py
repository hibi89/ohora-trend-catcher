"""P3 synthetic-only KST day epochs and history cursor isolation tests."""
from datetime import date,datetime,timedelta,timezone
from pathlib import Path
import sqlite3
import sys
import unittest

sys.path.insert(0,str(Path(__file__).resolve().parents[1]/"raw_v2"))
from three_api_epochs import ThreeApiEpochStore,EpochError
from three_api_request_spec import build_request_spec

NOW=datetime(2026,10,20,2,tzinfo=timezone.utc)
DAY=date(2026,10,20)


def official_shape():
    rows=[]
    for country,count in (("KR",100),("US",101),("JP",93)):
        for number in range(1,count+1):
            ordinal=len(rows)+1
            rows.append({"ordinal":ordinal,"key":f"{country}_{number:03d}",
                         "username":f"nail_{ordinal:03d}"})
    return rows


class P3EpochTests(unittest.TestCase):
    def setUp(self):
        self.db=sqlite3.connect(":memory:",isolation_level=None)
        self.db.row_factory=sqlite3.Row
        self.store=ThreeApiEpochStore(self.db)
        self.roster=official_shape()
        self.digest=self.store.register_roster(self.roster,recorded_at=NOW)
        self.daily=self.store.seed_daily(DAY,self.digest,anchor_utc=NOW)

    def tearDown(self):
        self.db.close()

    def job(self,ordinal,page,epoch=None):
        row=self.db.execute("""SELECT job_id FROM jobs_v2
            WHERE epoch_id=? AND account_ordinal=? AND page_seq=?""",
            (epoch or self.daily,ordinal,page)).fetchone()
        return row[0]

    def proof(self,job,request_id,*,ids=None,nxt="opaque_next",terminal="continued",at=NOW):
        return self.store.commit_daily_page(job,request_id,observed_at=at,
            ordered_ids=["100","101","102","103"] if ids is None else ids,
            next_cursor=nxt,terminal_basis=terminal,
            source_evidence_id="response-"+request_id,
            raw_receipt_id="rawv2:cap-"+request_id,
            source_digest="a"*64)

    def test_frozen_294_x_three_pages_two_rounds(self):
        self.assertEqual(self.store.summary(self.daily)["jobs"],882)
        self.assertEqual(self.store.active_round(self.daily),1)
        result=dict(((row["round_no"],row["lane"]),row["n"]) for row in
            self.db.execute("""SELECT round_no,lane,COUNT(*) n FROM jobs_v2
                WHERE epoch_id=? GROUP BY round_no,lane""",(self.daily,)))
        self.assertEqual(result,{
            (1,"A"):150,(1,"B"):144,(1,"C"):150,
            (2,"A"):144,(2,"B"):150,(2,"C"):144})
        self.assertEqual(len(self.store.ready_daily(self.daily,"A")),150)
        self.assertEqual(len(self.store.ready_daily(self.daily,"B")),144)
        self.assertEqual(len(self.store.ready_daily(self.daily,"C")),0)
        self.assertEqual(self.store.daily_assignment(1,3),("B",2))
        self.assertEqual(self.store.daily_assignment(294,3),("C",2))

    def test_roster_and_daily_epoch_are_immutable_idempotent(self):
        self.assertEqual(self.store.register_roster(self.roster,recorded_at=NOW),self.digest)
        self.assertEqual(self.store.seed_daily(DAY,self.digest,anchor_utc=NOW),self.daily)
        self.assertEqual(self.db.execute("SELECT COUNT(*) FROM jobs_v2").fetchone()[0],882)
        modified=official_shape()
        modified[0]["username"]="altered_nail_artist"
        self.assertNotEqual(self.store.register_roster(modified,recorded_at=NOW),self.digest)
        with self.assertRaises(EpochError):
            self.store.register_roster(modified[:-1],recorded_at=NOW)

    def test_page2_depends_on_real_page1_cursor_and_receipt(self):
        first=self.job(1,1)
        second=self.job(1,2)
        specA=build_request_spec("A","nail_001")
        self.store.bind_daily(first,"req-page-1",specA,at=NOW)
        self.assertEqual(self.store.ready_daily(self.daily,"C"),[])
        with self.assertRaises(EpochError):
            self.store.bind_daily(second,"req-page-2",
                build_request_spec("C","nail_001",after="guessed"),at=NOW)
        self.proof(first,"req-page-1",nxt="opaque_a")
        ready=self.store.ready_daily(self.daily,"C")
        self.assertEqual(len(ready),1)
        self.assertEqual(ready[0]["page_seq"],2)
        with self.assertRaises(EpochError):
            self.store.bind_daily(second,"req-wrong",
                build_request_spec("C","nail_001",after="opaque_wrong"),at=NOW)
        self.store.bind_daily(second,"req-page-2",
            build_request_spec("C","nail_001",after="opaque_a"),at=NOW)
        self.assertEqual(self.proof(second,"req-page-2",nxt="opaque_b")["status"],"committed")
        self.assertEqual(self.store.active_round(self.daily),1)
        self.assertEqual(self.db.execute("SELECT COUNT(*) FROM page_evidence_v2").fetchone()[0],2)
        self.assertEqual(self.db.execute("PRAGMA foreign_key_check").fetchall(),[])

    def test_terminal_short_page_skips_children_without_fake_success(self):
        first=self.job(1,1)
        self.store.bind_daily(first,"req-short",build_request_spec("A","nail_001"),at=NOW)
        self.proof(first,"req-short",ids=["100","101"],nxt=None,terminal="verified_terminal")
        states=[r[0] for r in self.db.execute("""SELECT state FROM jobs_v2
             WHERE epoch_id=? AND account_ordinal=1 ORDER BY page_seq""",(self.daily,))]
        self.assertEqual(states,["done","not_required","not_required"])
        self.assertEqual(self.db.execute("SELECT COUNT(*) FROM page_evidence_v2").fetchone()[0],1)
        self.assertEqual(len(self.store.ready_daily(self.daily,"C")),0)

    def test_full_page_cannot_infer_terminal_without_cursor(self):
        first=self.job(1,1)
        self.store.bind_daily(first,"req-ambiguous",build_request_spec("A","nail_001"),at=NOW)
        with self.assertRaises(EpochError):
            self.proof(first,"req-ambiguous",nxt=None,terminal="verified_terminal")
        self.assertEqual(self.db.execute("SELECT COUNT(*) FROM page_evidence_v2").fetchone()[0],0)
        self.assertEqual(self.db.execute("SELECT state FROM jobs_v2 WHERE job_id=?",
                                        (first,)).fetchone()[0],"bound")

    def test_history_checkpoint_not_reset_on_new_kst_day(self):
        first=self.job(1,1)
        self.store.bind_daily(first,"req-seed",build_request_spec("A","nail_001"),at=NOW)
        self.proof(first,"req-seed",nxt="history_cursor_one")
        hist=self.store.create_history_epoch(self.digest,anchor_utc=NOW,
                                              cutoff_utc=NOW-timedelta(days=365))
        saved=self.store.initialize_history_from_verified_daily(
            hist,self.daily,"KR_001",at=NOW)
        self.assertEqual(saved["cursor"],"history_cursor_one")
        second=self.store.seed_daily(DAY+timedelta(days=1),self.digest,
                                     anchor_utc=NOW+timedelta(days=1))
        self.assertNotEqual(second,self.daily)
        self.assertEqual(self.store.summary(second)["jobs"],882)
        self.assertEqual(self.store.summary(self.daily)["jobs"],882)
        self.assertEqual(self.store.initialize_history_from_verified_daily(
            hist,second,"KR_001",at=NOW+timedelta(days=1))["status"],
            "already_initialized")
        self.assertEqual(self.store.history_checkpoint(hist,"KR_001")["cursor"],
                         "history_cursor_one")
        self.assertEqual(self.db.execute("PRAGMA foreign_key_check").fetchall(),[])

    def test_bound_job_cannot_be_discarded_as_account_exception(self):
        first=self.job(1,1)
        self.store.bind_daily(first,"req-bound",build_request_spec("A","nail_001"),at=NOW)
        with self.assertRaises(EpochError):
            self.store.block_daily_account(self.daily,"KR_001",reason="permission_denied")
        self.assertEqual(self.db.execute("SELECT state FROM jobs_v2 WHERE job_id=?",
                                        (first,)).fetchone()[0],"bound")


if __name__=="__main__":
    unittest.main()
