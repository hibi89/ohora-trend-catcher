"""P3 synthetic-only KST day epochs and history cursor isolation tests."""
from datetime import date,datetime,timedelta,timezone
from pathlib import Path
import sqlite3
import sys
import tempfile
import unittest
from unittest import mock

sys.path.insert(0,str(Path(__file__).resolve().parents[1]/"raw_v2"))
from three_api_epochs import ThreeApiEpochStore,EpochError
from three_api_request_spec import build_request_spec
from three_api_raw_mirror import OfflineRawMirror
from three_api_history_offline_bridge import OfflineHistoryRawBridge,OfflineHistoryBridgeError

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

    def test_daily_replay_rejects_tampered_checkpoint_without_db_changes(self):
        first=self.job(1,1)
        self.store.bind_daily(first,"req-daily-checkpoint",
            build_request_spec("A","nail_001"),at=NOW)
        args=dict(observed_at=NOW,ordered_ids=["101","102"],
            next_cursor="daily_verified_cursor",terminal_basis="continued",
            source_evidence_id="daily-safe",raw_receipt_id="daily-raw",
            source_digest="a"*64)
        self.assertEqual(self.store.commit_daily_page(
            first,"req-daily-checkpoint",**args)["status"],"committed")
        self.assertEqual(self.store.commit_daily_page(
            first,"req-daily-checkpoint",**args)["status"],"already_committed")
        self.db.execute("""UPDATE cursor_checkpoints SET cursor=?
            WHERE epoch_id=? AND account_key='KR_001' AND stream='daily'""",
            ("forged_cursor",self.daily))
        before=self.db.total_changes
        with self.assertRaisesRegex(EpochError,"checkpoint differs"):
            self.store.commit_daily_page(first,"req-daily-checkpoint",**args)
        self.assertEqual(self.db.total_changes,before)

    def test_daily_replay_rejects_tampered_immutable_observation_time(self):
        first=self.job(1,1)
        self.store.bind_daily(first,"req-daily-observed",
            build_request_spec("A","nail_001"),at=NOW)
        args=dict(observed_at=NOW,ordered_ids=["101","102"],
            next_cursor="daily_observed_cursor",terminal_basis="continued",
            source_evidence_id="daily-observed",raw_receipt_id="daily-raw-observed",
            source_digest="a"*64)
        self.store.commit_daily_page(first,"req-daily-observed",**args)
        self.db.execute("""UPDATE page_evidence_v2 SET observed_at=?
            WHERE job_id=?""",("forged_timestamp",first))
        before=self.db.total_changes
        with self.assertRaisesRegex(EpochError,"immutable daily page replay"):
            self.store.commit_daily_page(first,"req-daily-observed",**args)
        self.assertEqual(self.db.total_changes,before)

    def test_daily_replay_integrity_matrix(self):
        """Independent synthetic mutations must not be accepted as idempotent."""
        fields=[
            ("cursor_in","forged-cursor","immutable daily page replay"),
            ("cursor_out","forged-out","immutable daily page replay"),
            ("observed_at","forged-observed","immutable daily page replay"),
            ("ordered_ids_json",'["999"]',"immutable daily page replay"),
            ("ordered_ids_json","{not-json","immutable daily page replay"),
            ("ordered_ids_json","null","immutable daily page replay"),
            ("ordered_ids_json","{}","immutable daily page replay"),
            ("ordered_ids_json",'["101","101"]',"immutable daily page replay"),
            ("ordered_ids_json",'[101]',"immutable daily page replay"),
            ("ordered_ids_json",'["101","102","103","104","105"]',"immutable daily page replay"),
            ("terminal_basis","verified_terminal","immutable daily page replay"),
            ("source_evidence_id","forged-source","immutable daily page replay"),
            ("raw_receipt_id","forged-receipt","immutable daily page replay"),
            ("source_digest","b"*64,"immutable daily page replay"),
            ("chain_hash","c"*64,"immutable daily page replay"),
        ]
        for field,payload,message in fields:
            with self.subTest(field=field):
                # Use a fresh isolated epoch store for every mutation so a
                # failed subcase cannot mask a later successful replay.
                db=sqlite3.connect(":memory:",isolation_level=None)
                db.row_factory=sqlite3.Row
                try:
                    store=ThreeApiEpochStore(db)
                    digest=store.register_roster(official_shape(),recorded_at=NOW)
                    daily=store.seed_daily(DAY,digest,anchor_utc=NOW)
                    job=db.execute("""SELECT job_id FROM jobs_v2
                        WHERE epoch_id=? AND account_ordinal=1 AND page_seq=1""",
                        (daily,)).fetchone()[0]
                    request="req-matrix"
                    store.bind_daily(job,request,build_request_spec("A","nail_001"),at=NOW)
                    args=dict(observed_at=NOW,ordered_ids=["101","102"],
                        next_cursor="matrix_next",terminal_basis="continued",
                        source_evidence_id="matrix-source",raw_receipt_id="matrix-raw",
                        source_digest="a"*64)
                    store.commit_daily_page(job,request,**args)
                    db.execute("UPDATE page_evidence_v2 SET "+field+"=? WHERE job_id=?",
                               (payload,job))
                    before=db.total_changes
                    expected_message=("Daily replay media evidence invalid"
                        if field=="ordered_ids_json" and payload!='["999"]'
                        else message)
                    with self.assertRaisesRegex(EpochError,expected_message):
                        store.commit_daily_page(job,request,**args)
                    self.assertEqual(db.total_changes,before)
                finally:
                    db.close()

    def test_daily_replay_checkpoint_integrity_matrix(self):
        """Seven checkpoint attributes must remain tied to last daily proof."""
        mutations=[
            ("cursor","forged"),
            ("anchor_post_id","999"),
            ("last_receipt","forged"),
            ("chain_hash","f"*64),
            ("updated_at","forged"),
            ("page_seq",2),
            ("status","terminal"),
        ]
        for field,payload in mutations:
            with self.subTest(field=field):
                db=sqlite3.connect(":memory:",isolation_level=None)
                db.row_factory=sqlite3.Row
                try:
                    store=ThreeApiEpochStore(db)
                    digest=store.register_roster(official_shape(),recorded_at=NOW)
                    daily=store.seed_daily(DAY,digest,anchor_utc=NOW)
                    job=db.execute("""SELECT job_id FROM jobs_v2
                        WHERE epoch_id=? AND account_ordinal=1 AND page_seq=1""",
                        (daily,)).fetchone()[0]
                    request="req-checkpoint-matrix"
                    store.bind_daily(job,request,build_request_spec("A","nail_001"),at=NOW)
                    args=dict(observed_at=NOW,ordered_ids=["101","102"],
                        next_cursor="checkpoint_next",terminal_basis="continued",
                        source_evidence_id="checkpoint-source",
                        raw_receipt_id="checkpoint-raw",source_digest="a"*64)
                    store.commit_daily_page(job,request,**args)
                    db.execute("""UPDATE cursor_checkpoints SET """+field+"""=?
                        WHERE epoch_id=? AND account_key='KR_001'
                          AND stream='daily'""",(payload,daily))
                    before=db.total_changes
                    with self.assertRaisesRegex(EpochError,"checkpoint differs"):
                        store.commit_daily_page(job,request,**args)
                    self.assertEqual(db.total_changes,before)
                finally:
                    db.close()

    def test_daily_invalid_receipt_input_matrix(self):
        """Reject malformed synthetic evidence before any receipt is stored."""
        cases=[
            ("ordered_ids",None),
            ("ordered_ids","101"),
            ("ordered_ids",["1","2","3","4","5"]),
            ("ordered_ids",["1","1"]),
            ("ordered_ids",["not-numeric"]),
            ("ordered_ids",[123]),
            ("ordered_ids",["1"*65]),
            ("next_cursor","bad cursor"),
            ("next_cursor",123),
            ("terminal_basis","unknown"),
            ("source_evidence_id",""),
            ("raw_receipt_id",""),
            ("source_digest",""),
            ("source_digest","x"*64),
            ("source_digest","a"*63),
        ]
        for field,payload in cases:
            with self.subTest(field=field,payload=repr(payload)):
                db=sqlite3.connect(":memory:",isolation_level=None)
                db.row_factory=sqlite3.Row
                try:
                    store=ThreeApiEpochStore(db)
                    digest=store.register_roster(official_shape(),recorded_at=NOW)
                    daily=store.seed_daily(DAY,digest,anchor_utc=NOW)
                    job=db.execute("""SELECT job_id FROM jobs_v2
                        WHERE epoch_id=? AND account_ordinal=1 AND page_seq=1""",
                        (daily,)).fetchone()[0]
                    store.bind_daily(job,"req-invalid-matrix",
                        build_request_spec("A","nail_001"),at=NOW)
                    args=dict(observed_at=NOW,ordered_ids=["101"],
                        next_cursor="valid_cursor",terminal_basis="continued",
                        source_evidence_id="safe-source",raw_receipt_id="safe-raw",
                        source_digest="a"*64)
                    args[field]=payload
                    before=db.total_changes
                    with self.assertRaises((EpochError,TypeError)):
                        store.commit_daily_page(job,"req-invalid-matrix",**args)
                    self.assertEqual(db.total_changes,before)
                    self.assertEqual(db.execute("""SELECT COUNT(*) FROM page_evidence_v2
                        WHERE job_id=?""",(job,)).fetchone()[0],0)
                finally:
                    db.close()

    def test_daily_replay_rejects_latest_terminal_proof_corruption(self):
        """An older valid replay must not mask an invalid latest page terminal."""
        for terminal,cursor in (("continued",None),
                                ("verified_terminal","forged_cursor")):
            with self.subTest(terminal=terminal):
                db=sqlite3.connect(":memory:",isolation_level=None)
                db.row_factory=sqlite3.Row
                try:
                    store=ThreeApiEpochStore(db)
                    digest=store.register_roster(official_shape(),recorded_at=NOW)
                    daily=store.seed_daily(DAY,digest,anchor_utc=NOW)
                    jobs=[db.execute("""SELECT job_id FROM jobs_v2
                        WHERE epoch_id=? AND account_ordinal=1 AND page_seq=?""",
                        (daily,seq)).fetchone()[0] for seq in (1,2)]
                    first_args=dict(observed_at=NOW,ordered_ids=["101"],
                        next_cursor="terminal_next",terminal_basis="continued",
                        source_evidence_id="terminal-first",
                        raw_receipt_id="terminal-first-raw",source_digest="a"*64)
                    store.bind_daily(jobs[0],"terminal-first-request",
                        build_request_spec("A","nail_001"),at=NOW)
                    store.commit_daily_page(jobs[0],"terminal-first-request",**first_args)
                    store.bind_daily(jobs[1],"terminal-second-request",
                        build_request_spec("C","nail_001",after="terminal_next"),at=NOW)
                    store.commit_daily_page(jobs[1],"terminal-second-request",
                        observed_at=NOW,ordered_ids=["102"],
                        next_cursor="terminal_third",terminal_basis="continued",
                        source_evidence_id="terminal-second",
                        raw_receipt_id="terminal-second-raw",source_digest="b"*64)
                    db.execute("""UPDATE page_evidence_v2 SET terminal_basis=?,
                        cursor_out=? WHERE job_id=?""",(terminal,cursor,jobs[1]))
                    db.execute("""UPDATE cursor_checkpoints SET cursor=?,
                        status=? WHERE epoch_id=? AND account_key='KR_001'
                        AND stream='daily'""",
                        (cursor,"terminal" if terminal=="verified_terminal" else "open",
                         daily))
                    before=db.total_changes
                    with self.assertRaisesRegex(EpochError,"terminal proof invalid"):
                        store.commit_daily_page(jobs[0],"terminal-first-request",**first_args)
                    self.assertEqual(db.total_changes,before)
                finally:
                    db.close()

    def test_history_checkpoint_not_reset_on_new_kst_day(self):
        first=self.job(1,1)
        self.store.bind_daily(first,"req-seed",build_request_spec("A","nail_001"),at=NOW)
        self.proof(first,"req-seed",ids=["100"],nxt=None,
                   terminal="verified_terminal")
        hist=self.store.create_history_epoch(self.digest,anchor_utc=NOW,
                                              cutoff_utc=NOW-timedelta(days=365))
        saved=self.store.initialize_history_from_verified_daily(
            hist,self.daily,"KR_001",at=NOW)
        self.assertIsNone(saved["cursor"])
        self.assertEqual(saved["history_state"],"terminal")
        second=self.store.seed_daily(DAY+timedelta(days=1),self.digest,
                                     anchor_utc=NOW+timedelta(days=1))
        self.assertNotEqual(second,self.daily)
        self.assertEqual(self.store.summary(second)["jobs"],882)
        self.assertEqual(self.store.summary(self.daily)["jobs"],882)
        self.assertEqual(self.store.initialize_history_from_verified_daily(
            hist,second,"KR_001",at=NOW+timedelta(days=1))["status"],
            "already_initialized")
        self.assertIsNone(self.store.history_checkpoint(hist,"KR_001")["cursor"])
        self.assertEqual(self.db.execute("PRAGMA foreign_key_check").fetchall(),[])

    def test_history_seed_rejects_unfinished_front_chain(self):
        first=self.job(1,1)
        self.store.bind_daily(first,"req-incomplete",
            build_request_spec("A","nail_001"),at=NOW)
        self.proof(first,"req-incomplete",nxt="cursor_for_page2")
        hist=self.store.create_history_epoch(self.digest,anchor_utc=NOW,
            cutoff_utc=NOW-timedelta(days=365))
        with self.assertRaises(EpochError):
            self.store.initialize_history_from_verified_daily(
                hist,self.daily,"KR_001",at=NOW)
        self.assertIsNone(self.store.history_checkpoint(hist,"KR_001"))

    def test_anchor_must_correspond_to_explicit_kst_day(self):
        with self.assertRaises(EpochError):
            self.store.seed_daily(DAY+timedelta(days=1),self.digest,anchor_utc=NOW)

    def open_history_fixture(self):
        """Synthetic exception closure, not evidence of 294 successful API reads."""
        self.db.execute("""UPDATE jobs_v2 SET state='blocked',
              blocked_reason='synthetic_fixture_only'
              WHERE epoch_id=? AND account_ordinal>1""",(self.daily,))
        first,second,third=(self.job(1,page) for page in (1,2,3))
        self.store.bind_daily(first,"req-hfront1",
                              build_request_spec("A","nail_001"),at=NOW)
        self.proof(first,"req-hfront1",nxt="history_front_page2")
        self.store.bind_daily(second,"req-hfront2",
             build_request_spec("C","nail_001",after="history_front_page2"),at=NOW)
        self.proof(second,"req-hfront2",nxt="history_front_page3")
        self.store.bind_daily(third,"req-hfront3",
             build_request_spec("B","nail_001",after="history_front_page3"),at=NOW)
        self.proof(third,"req-hfront3",nxt="history_first_cursor")
        self.assertIsNone(self.store.active_round(self.daily))
        history=self.store.create_history_epoch(
            self.digest,anchor_utc=NOW,cutoff_utc=NOW-timedelta(days=365))
        seeded=self.store.initialize_history_from_verified_daily(
            history,self.daily,"KR_001",at=NOW)
        self.assertEqual(seeded["cursor"],"history_first_cursor")
        return history

    def test_history_cursor_survives_two_backfill_pages_and_terminal(self):
        history=self.open_history_fixture()
        page1=self.store.schedule_one_history_page(history,"KR_001","A",at=NOW)
        self.assertEqual(page1["page_seq"],1)
        self.assertEqual(page1["cursor"],"history_first_cursor")
        self.store.bind_history(page1["job_id"],"req-history-1",
            build_request_spec("A","nail_001",after="history_first_cursor"),at=NOW)
        receipt=self.store.commit_history_page(page1["job_id"],"req-history-1",
            observed_at=NOW,ordered_ids=["501","502","503","504"],
            next_cursor="history_second_cursor",terminal_basis="continued",
            source_evidence_id="safe-r1",raw_receipt_id="raw-r1",source_digest="a"*64)
        self.assertEqual(receipt["coverage_verified"],False)
        self.assertEqual(self.store.history_checkpoint(history,"KR_001")["page_seq"],1)
        page2=self.store.schedule_one_history_page(history,"KR_001","B",at=NOW)
        self.assertEqual(page2["page_seq"],2)
        self.store.bind_history(page2["job_id"],"req-history-2",
            build_request_spec("B","nail_001",after="history_second_cursor"),at=NOW)
        args=dict(observed_at=NOW,ordered_ids=["601","602"],next_cursor=None,
             terminal_basis="verified_terminal",source_evidence_id="safe-r2",
             raw_receipt_id="raw-r2",source_digest="b"*64)
        result=self.store.commit_history_page(page2["job_id"],"req-history-2",**args)
        self.assertFalse(result["history_cursor_present"])
        self.assertEqual(self.store.commit_history_page(
            page2["job_id"],"req-history-2",**args)["status"],"already_committed")
        cp=self.store.history_checkpoint(history,"KR_001")
        self.assertEqual(cp["page_seq"],2)
        self.assertEqual(cp["status"],"terminal")
        with self.assertRaises(EpochError):
            self.store.schedule_one_history_page(history,"KR_001","C",at=NOW)
        self.assertEqual(self.db.execute("SELECT status FROM coverage_ledger "
             "WHERE epoch_id=? AND account_key='KR_001'",(history,)).fetchone()[0],
             "partial")
        self.assertEqual(self.db.execute("PRAGMA foreign_key_check").fetchall(),[])

    def test_new_daily_day_preempts_history_but_prior_bound_response_can_finish(self):
        history=self.open_history_fixture()
        page=self.store.schedule_one_history_page(history,"KR_001","C",at=NOW)
        self.store.bind_history(page["job_id"],"req-midnight-history",
            build_request_spec("C","nail_001",after="history_first_cursor"),at=NOW)
        tomorrow=self.store.seed_daily(DAY+timedelta(days=1),self.digest,
            anchor_utc=NOW+timedelta(days=1))
        self.assertEqual(self.store.summary(tomorrow)["jobs"],882)
        with self.assertRaises(EpochError):
            self.store.schedule_one_history_page(history,"KR_001","A",
                at=NOW+timedelta(days=1))
        self.assertEqual(self.store.commit_history_page(
            page["job_id"],"req-midnight-history",
            observed_at=NOW+timedelta(days=1),
            ordered_ids=["701","702","703","704"],
            next_cursor="persisted_after_midnight",terminal_basis="continued",
            source_evidence_id="safe-midnight",raw_receipt_id="raw-midnight",
            source_digest="c"*64)["status"],"committed")
        self.assertEqual(self.store.history_checkpoint(history,"KR_001")["cursor"],
                         "persisted_after_midnight")
        with self.assertRaises(EpochError):
            self.store.schedule_one_history_page(history,"KR_001","A",
                at=NOW+timedelta(days=1))
        self.assertEqual(self.store.summary(tomorrow)["jobs"],882)

    def test_history_cursor_conflict_does_not_advance_checkpoint(self):
        history=self.open_history_fixture()
        page=self.store.schedule_one_history_page(history,"KR_001","A",at=NOW)
        with self.assertRaises(EpochError):
            self.store.bind_history(page["job_id"],"req-wronghistory",
               build_request_spec("A","nail_001",after="guessed_cursor"),at=NOW)
        self.store.bind_history(page["job_id"],"req-validhistory",
            build_request_spec("A","nail_001",after="history_first_cursor"),at=NOW)
        with self.assertRaises(EpochError):
            self.store.commit_history_page(page["job_id"],"req-validhistory",
                observed_at=NOW,ordered_ids=["800","801","802","803"],
                next_cursor="history_first_cursor",terminal_basis="continued",
                source_evidence_id="safe-loop",raw_receipt_id="raw-loop",
                source_digest="d"*64)
        self.assertEqual(self.store.history_checkpoint(history,"KR_001")["page_seq"],0)
        self.assertEqual(self.db.execute("SELECT COUNT(*) FROM page_evidence_v2 "
            "WHERE job_id=?",(page["job_id"],)).fetchone()[0],0)

    def test_unfinished_history_job_cannot_be_scheduled_twice(self):
        """A pending or bound page must be resumed, never duplicated."""
        history=self.open_history_fixture()
        page=self.store.schedule_one_history_page(history,"KR_001","A",at=NOW)
        with self.assertRaises(EpochError):
            self.store.schedule_one_history_page(history,"KR_001","B",at=NOW)
        self.store.bind_history(page["job_id"],"req-history-pending",
            build_request_spec("A","nail_001",
                               after="history_first_cursor"),at=NOW)
        with self.assertRaises(EpochError):
            self.store.schedule_one_history_page(history,"KR_001","C",at=NOW)
        self.assertEqual(self.store.history_checkpoint(history,"KR_001")["page_seq"],0)
        self.assertEqual(self.db.execute(
            "SELECT COUNT(*) FROM jobs_v2 WHERE epoch_id=? AND phase='history'",
            (history,)).fetchone()[0],1)
        self.assertEqual(self.db.execute(
            "SELECT COUNT(*) FROM page_evidence_v2 WHERE job_id=?",
            (page["job_id"],)).fetchone()[0],0)

    def test_history_replay_conflict_cannot_mutate_verified_checkpoint(self):
        """Conflicting duplicate receipts must fail without losing old proof."""
        history=self.open_history_fixture()
        page=self.store.schedule_one_history_page(history,"KR_001","A",at=NOW)
        self.store.bind_history(page["job_id"],"req-history-replay",
            build_request_spec("A","nail_001",
                               after="history_first_cursor"),at=NOW)
        kwargs=dict(observed_at=NOW,ordered_ids=["501","502","503","504"],
                    next_cursor="history_second_cursor",
                    terminal_basis="continued",source_evidence_id="safe-replay",
                    raw_receipt_id="raw-replay",source_digest="a"*64)
        self.assertEqual(self.store.commit_history_page(
            page["job_id"],"req-history-replay",**kwargs)["status"],"committed")
        original=self.store.history_checkpoint(history,"KR_001")
        self.assertEqual(self.store.commit_history_page(
            page["job_id"],"req-history-replay",**kwargs)["status"],
            "already_committed")
        with self.assertRaises(EpochError):
            self.store.commit_history_page(
                page["job_id"],"req-history-replay",
                **{**kwargs,"ordered_ids":["501","502","503","999"]})
        self.assertEqual(self.store.history_checkpoint(history,"KR_001"),original)
        self.assertEqual(self.db.execute(
            "SELECT COUNT(*) FROM page_evidence_v2 WHERE job_id=?",
            (page["job_id"],)).fetchone()[0],1)

    def test_history_seed_is_idempotent_without_cursor_rewind(self):
        """Later daily evidence cannot silently reset a committed history cursor."""
        history=self.open_history_fixture()
        page=self.store.schedule_one_history_page(history,"KR_001","A",at=NOW)
        self.store.bind_history(page["job_id"],"req-history-progress",
            build_request_spec("A","nail_001",
                               after="history_first_cursor"),at=NOW)
        self.store.commit_history_page(
            page["job_id"],"req-history-progress",
            observed_at=NOW,ordered_ids=["501","502","503","504"],
            next_cursor="advanced_history_cursor",terminal_basis="continued",
            source_evidence_id="safe-progress",raw_receipt_id="raw-progress",
            source_digest="a"*64)
        before=self.store.history_checkpoint(history,"KR_001")
        result=self.store.initialize_history_from_verified_daily(
            history,self.daily,"KR_001",at=NOW+timedelta(hours=1))
        self.assertEqual(result["status"],"already_initialized")
        self.assertEqual(self.store.history_checkpoint(history,"KR_001"),before)
        self.assertEqual(before["cursor"],"advanced_history_cursor")
        self.assertEqual(before["page_seq"],1)

    def test_history_cannot_allocate_while_new_daily_front_pending(self):
        """A new KST day takes precedence over existing history cursor."""
        history=self.open_history_fixture()
        before=self.store.history_checkpoint(history,"KR_001")
        self.store.seed_daily(DAY+timedelta(days=1),self.digest,
                              anchor_utc=NOW+timedelta(days=1))
        with self.assertRaises(EpochError):
            self.store.schedule_one_history_page(
                history,"KR_001","A",at=NOW+timedelta(days=1))
        self.assertEqual(self.store.history_checkpoint(history,"KR_001"),before)
        self.assertEqual(self.db.execute(
            "SELECT COUNT(*) FROM jobs_v2 WHERE epoch_id=?",(history,)
        ).fetchone()[0],0)

    def test_history_wrong_lane_or_off_roster_cannot_allocate(self):
        """Invalid API lanes and unknown accounts cannot create history jobs."""
        history=self.open_history_fixture()
        for account,lane in (("KR_001","D"),("KR_999","A"),
                             ("bad account","B")):
            with self.subTest(account=account,lane=lane):
                with self.assertRaises(EpochError):
                    self.store.schedule_one_history_page(
                        history,account,lane,at=NOW)
        self.assertEqual(self.db.execute(
            "SELECT COUNT(*) FROM jobs_v2 WHERE epoch_id=?",(history,)
        ).fetchone()[0],0)
        self.assertEqual(self.store.history_checkpoint(history,"KR_001")["page_seq"],0)

    def test_history_cursor_cycle_does_not_write_second_receipt(self):
        """A later page cannot return to the immutable seed cursor."""
        history=self.open_history_fixture()
        page=self.store.schedule_one_history_page(history,"KR_001","A",at=NOW)
        self.store.bind_history(page["job_id"],"req-history-cycle",
            build_request_spec("A","nail_001",
                               after="history_first_cursor"),at=NOW)
        before=self.store.history_checkpoint(history,"KR_001")
        with self.assertRaises((EpochError,sqlite3.IntegrityError)):
            self.store.commit_history_page(
                page["job_id"],"req-history-cycle",observed_at=NOW,
                ordered_ids=["501","502","503","504"],
                next_cursor="history_first_cursor",terminal_basis="continued",
                source_evidence_id="safe-cycle",raw_receipt_id="raw-cycle",
                source_digest="a"*64)
        self.assertEqual(self.store.history_checkpoint(history,"KR_001"),before)
        self.assertEqual(self.db.execute(
            "SELECT COUNT(*) FROM page_evidence_v2 WHERE job_id=?",
            (page["job_id"],)).fetchone()[0],0)

    def test_unrelated_manual_review_does_not_block_verified_history(self):
        """Review on a different account cannot starve a verified cursor."""
        history=self.open_history_fixture()
        self.db.execute("""INSERT INTO coverage_ledger(
            epoch_id,account_key,verified_oldest,terminal_basis,status)
            VALUES(?, 'US_101', NULL, NULL, 'needs_review')""",(self.daily,))
        page=self.store.schedule_one_history_page(history,"KR_001","A",at=NOW)
        self.assertEqual(page["page_seq"],1)
        self.store.bind_history(page["job_id"],"req-isolated-review",
            build_request_spec("A","nail_001",
                               after="history_first_cursor"),at=NOW)
        self.assertEqual(self.db.execute(
            "SELECT status FROM coverage_ledger WHERE epoch_id=? AND account_key='US_101'",
            (self.daily,)).fetchone()[0],"needs_review")

    def test_own_manual_review_blocks_history_without_erasing_checkpoint(self):
        """An account's unresolved review still forbids new history binding."""
        history=self.open_history_fixture()
        original=self.store.history_checkpoint(history,"KR_001")
        self.db.execute("""INSERT INTO coverage_ledger(
            epoch_id,account_key,verified_oldest,terminal_basis,status)
            VALUES(?, 'KR_001', NULL, NULL, 'needs_review')""",(self.daily,))
        with self.assertRaises(EpochError):
            self.store.schedule_one_history_page(history,"KR_001","A",at=NOW)
        self.assertEqual(self.store.history_checkpoint(history,"KR_001"),original)
        self.assertEqual(self.db.execute(
            "SELECT COUNT(*) FROM jobs_v2 WHERE epoch_id=?",
            (history,)).fetchone()[0],0)

    def test_p4_history_bridge_two_pages_terminal_and_idempotent(self):
        history=self.open_history_fixture()
        with tempfile.TemporaryDirectory() as tmp:
            mirror=OfflineRawMirror(Path(tmp)/"three_api_raw_mirror.sqlite")
            try:
                bridge=OfflineHistoryRawBridge(self.store,mirror)
                first=self.store.schedule_one_history_page(history,"KR_001","A",at=NOW)
                self.store.bind_history(first["job_id"],"req-bridge-one",
                    build_request_spec("A","nail_001",after="history_first_cursor"),at=NOW)
                posts=[
                    {"id":"501","media_type":"CAROUSEL_ALBUM","like_count":0,
                     "children":[{"id":"511","media_type":"IMAGE",
                                  "media_url":"https://cdn.example.invalid/511.jpg"}]},
                    *[{"id":str(i),"media_type":"IMAGE","like_count":0}
                      for i in range(502,505)],
                ]
                args=dict(observed_at=NOW,posts=posts,
                          next_cursor="bridge_cursor_2",terminal_basis="continued")
                self.assertEqual(bridge.stage_capture(first["job_id"],"req-bridge-one",
                    **args)["status"],"staged")
                self.assertEqual(bridge.stage_capture(first["job_id"],"req-bridge-one",
                    **args)["status"],"already_staged")
                self.assertEqual(bridge.reconcile_one(first["job_id"])["status"],"committed")
                self.assertEqual(bridge.reconcile_one(first["job_id"])["status"],
                                 "already_committed")
                self.assertEqual(self.store.history_checkpoint(history,"KR_001")["cursor"],
                                 "bridge_cursor_2")
                second=self.store.schedule_one_history_page(history,"KR_001","B",at=NOW)
                self.store.bind_history(second["job_id"],"req-bridge-terminal",
                    build_request_spec("B","nail_001",after="bridge_cursor_2"),at=NOW)
                bridge.stage_capture(second["job_id"],"req-bridge-terminal",
                    observed_at=NOW,posts=[{"id":"601"},{"id":"602"}],
                    next_cursor=None,terminal_basis="verified_terminal")
                self.assertEqual(bridge.reconcile_one(second["job_id"])["status"],
                                 "committed")
                self.assertEqual(self.store.history_checkpoint(history,"KR_001")["status"],
                                 "terminal")
                self.assertEqual(mirror.summary()["raw_posts"],6)
                self.assertEqual(mirror.summary()["raw_post_observations"],6)
                self.assertEqual(mirror.summary()["raw_post_media"],7)
                child=mirror.db.execute(
                    "SELECT post_id,media_role,item_order,media_url "
                    "FROM raw_post_media WHERE media_id='511'").fetchone()
                self.assertEqual(tuple(child),
                    ("501","child",1,"https://cdn.example.invalid/511.jpg"))
                self.assertEqual(mirror.summary()["pending_pages"],0)
                self.assertEqual(self.db.execute(
                    "SELECT COUNT(*) FROM page_evidence_v2 WHERE job_id IN (?,?)",
                    (first["job_id"],second["job_id"])).fetchone()[0],2)
                self.assertFalse(bridge.stage_capture(first["job_id"],"req-bridge-one",
                    **args)["history_dispatch_enabled"])
                self.assertEqual(self.db.execute("PRAGMA foreign_key_check").fetchall(),[])
            finally:
                mirror.close()

    def test_p4_history_bridge_recovers_raw_first_without_refetch(self):
        history=self.open_history_fixture()
        with tempfile.TemporaryDirectory() as tmp:
            mirror=OfflineRawMirror(Path(tmp)/"three_api_raw_mirror.sqlite")
            try:
                bridge=OfflineHistoryRawBridge(self.store,mirror)
                job=self.store.schedule_one_history_page(history,"KR_001","A",at=NOW)
                self.store.bind_history(job["job_id"],"req-bridge-crash",
                    build_request_spec("A","nail_001",after="history_first_cursor"),at=NOW)
                bridge.stage_capture(job["job_id"],"req-bridge-crash",
                    observed_at=NOW,posts=[{"id":"701","comments_count":0}],
                    next_cursor="bridge_after_crash",terminal_basis="continued")
                with mock.patch.object(self.store,"commit_history_page",
                                       side_effect=EpochError("injected epoch failure")):
                    with self.assertRaises(OfflineHistoryBridgeError):
                        bridge.reconcile_one(job["job_id"])
                self.assertEqual(mirror.summary()["raw_posts"],1)
                self.assertEqual(self.store.history_checkpoint(history,"KR_001")["page_seq"],0)
                self.assertEqual(bridge.reconcile_pending()[0]["status"],"committed")
                self.assertEqual(bridge.reconcile_pending()[0]["status"],"already_committed")
                self.assertEqual(self.store.history_checkpoint(history,"KR_001")["cursor"],
                                 "bridge_after_crash")
                self.assertEqual(mirror.summary()["raw_post_observations"],1)
            finally:
                mirror.close()

    def test_p4_history_bridge_rejects_tampered_durable_source(self):
        history=self.open_history_fixture()
        with tempfile.TemporaryDirectory() as tmp:
            mirror=OfflineRawMirror(Path(tmp)/"three_api_raw_mirror.sqlite")
            try:
                bridge=OfflineHistoryRawBridge(self.store,mirror)
                job=self.store.schedule_one_history_page(history,"KR_001","A",at=NOW)
                self.store.bind_history(job["job_id"],"req-bridge-tamper",
                    build_request_spec("A","nail_001",after="history_first_cursor"),at=NOW)
                bridge.stage_capture(job["job_id"],"req-bridge-tamper",
                    observed_at=NOW,posts=[{"id":"801"}],
                    next_cursor="after_tamper",terminal_basis="continued")
                self.db.execute("UPDATE offline_history_capture SET posts_json='[]' "
                                "WHERE job_id=?",(job["job_id"],))
                with self.assertRaises(OfflineHistoryBridgeError):
                    bridge.reconcile_one(job["job_id"])
                self.assertEqual(mirror.summary()["raw_posts"],0)
                self.assertEqual(self.store.history_checkpoint(history,"KR_001")["page_seq"],0)
                with self.assertRaises(OfflineHistoryBridgeError):
                    bridge.stage_capture(job["job_id"],"req-bridge-tamper",
                        observed_at=NOW,posts=[{"id":"999"}],
                        next_cursor="after_tamper",terminal_basis="continued")
            finally:
                mirror.close()

    def test_p4_history_bridge_restarts_from_durable_source(self):
        history=self.open_history_fixture()
        with tempfile.TemporaryDirectory() as tmp:
            mirror_path=Path(tmp)/"three_api_raw_mirror.sqlite"
            epoch_path=Path(tmp)/"epoch_offline.sqlite"
            mirror=OfflineRawMirror(mirror_path)
            try:
                bridge=OfflineHistoryRawBridge(self.store,mirror)
                job=self.store.schedule_one_history_page(history,"KR_001","A",at=NOW)
                self.store.bind_history(job["job_id"],"req-bridge-restart",
                    build_request_spec("A","nail_001",after="history_first_cursor"),at=NOW)
                bridge.stage_capture(job["job_id"],"req-bridge-restart",
                    observed_at=NOW,posts=[{"id":"901"}],
                    next_cursor="persisted_cursor",terminal_basis="continued")
                disk=sqlite3.connect(epoch_path,isolation_level=None)
                try:
                    self.db.backup(disk)
                    reopened=ThreeApiEpochStore(disk)
                    resumed=OfflineHistoryRawBridge(reopened,mirror)
                    self.assertEqual(resumed.reconcile_pending()[0]["status"],"committed")
                    self.assertEqual(reopened.history_checkpoint(history,"KR_001")["cursor"],
                                     "persisted_cursor")
                    self.assertEqual(mirror.summary()["raw_posts"],1)
                finally:
                    disk.close()
            finally:
                mirror.close()

    def test_p4_history_bridge_rechecks_committed_mirror_observations(self):
        history=self.open_history_fixture()
        with tempfile.TemporaryDirectory() as tmp:
            mirror=OfflineRawMirror(Path(tmp)/"three_api_raw_mirror.sqlite")
            try:
                bridge=OfflineHistoryRawBridge(self.store,mirror)
                page=self.store.schedule_one_history_page(history,"KR_001","A",at=NOW)
                self.store.bind_history(page["job_id"],"req-bridge-observation",
                    build_request_spec("A","nail_001",after="history_first_cursor"),at=NOW)
                bridge.stage_capture(page["job_id"],"req-bridge-observation",
                    observed_at=NOW,posts=[{"id":"1101","like_count":0}],
                    next_cursor="history_after_observation",terminal_basis="continued")
                self.assertEqual(bridge.reconcile_one(page["job_id"])["status"],"committed")
                prior=self.store.history_checkpoint(history,"KR_001")
                mirror.db.execute(
                    "UPDATE raw_post_observations SET like_count=100 WHERE post_id='1101'")
                with self.assertRaises(OfflineHistoryBridgeError):
                    bridge.reconcile_one(page["job_id"])
                self.assertEqual(self.store.history_checkpoint(history,"KR_001"),prior)
            finally:
                mirror.close()

    def test_p4_history_bridge_rejects_conflicting_fixture_replay(self):
        history=self.open_history_fixture()
        with tempfile.TemporaryDirectory() as tmp:
            mirror=OfflineRawMirror(Path(tmp)/"three_api_raw_mirror.sqlite")
            try:
                bridge=OfflineHistoryRawBridge(self.store,mirror)
                page=self.store.schedule_one_history_page(history,"KR_001","A",at=NOW)
                self.store.bind_history(page["job_id"],"req-bridge-immutable",
                    build_request_spec("A","nail_001",after="history_first_cursor"),at=NOW)
                kwargs=dict(observed_at=NOW,posts=[{"id":"1201"}],
                            next_cursor="bridge_next",terminal_basis="continued")
                bridge.stage_capture(page["job_id"],"req-bridge-immutable",**kwargs)
                with self.assertRaises(OfflineHistoryBridgeError):
                    bridge.stage_capture(page["job_id"],"req-bridge-immutable",
                        **{**kwargs,"posts":[{"id":"1202"}]})
                with self.assertRaises(OfflineHistoryBridgeError):
                    bridge.stage_capture(page["job_id"],"req-bridge-immutable",
                        **{**kwargs,"next_cursor":None})
                self.assertEqual(bridge.reconcile_one(page["job_id"])["status"],
                                 "committed")
                self.assertEqual(mirror.summary()["raw_posts"],1)
            finally:
                mirror.close()

    def test_blocked_own_daily_does_not_reenable_history_from_old_cursor(self):
        """A blocked latest-day page must quarantine its own older history."""
        history=self.open_history_fixture()
        prior=self.store.history_checkpoint(history,"KR_001")
        next_day=self.store.seed_daily(
            DAY+timedelta(days=1),self.digest,
            anchor_utc=NOW+timedelta(days=1))
        # The other 293 synthetic accounts are explicit exceptions; this
        # is NOT evidence of successful data collection.
        self.db.execute("""UPDATE jobs_v2 SET state='blocked',
            blocked_reason='synthetic_fixture_only'
            WHERE epoch_id=? AND account_key<>'KR_001'""",(next_day,))
        self.store.block_daily_account(next_day,"KR_001",
                                       reason="permission_denied")
        self.assertIsNone(self.store.active_round(next_day))
        with self.assertRaises(EpochError):
            self.store.schedule_one_history_page(history,"KR_001","A",
                at=NOW+timedelta(days=1))
        self.assertEqual(self.store.history_checkpoint(history,"KR_001"),prior)
        self.assertEqual(self.db.execute(
            "SELECT COUNT(*) FROM jobs_v2 WHERE epoch_id=? AND state='blocked'",
            (next_day,)).fetchone()[0],882)

    def test_other_blocked_account_does_not_starve_proven_history(self):
        history=self.open_history_fixture()
        # 293 synthetic exception accounts are blocked by the fixture,
        # but verified KR_001 still has a real linked synthetic page chain.
        blocked=self.db.execute(
            "SELECT COUNT(*) FROM jobs_v2 WHERE epoch_id=? AND state='blocked'",
            (self.daily,)).fetchone()[0]
        self.assertEqual(blocked,293*3)
        page=self.store.schedule_one_history_page(history,"KR_001","A",at=NOW)
        self.assertEqual(page["cursor"],"history_first_cursor")
        self.assertEqual(page["page_seq"],1)
        self.assertEqual(self.db.execute(
            "SELECT COUNT(*) FROM jobs_v2 WHERE epoch_id=? AND phase='history'",
            (history,)).fetchone()[0],1)

    def test_full_294_by_three_page_two_round_synthetic_epoch_e2e(self):
        """All 882 fixed jobs, predecessor cursors and 365/730 roots, no HTTP.

        Fake receipts test scheduler invariants only, not RAW or live capture.
        Each daily page contains four synthetic, globally unique numeric IDs.
        """
        expected_slots=((1,"A",150),(1,"B",144),(1,"C",150),
                        (2,"A",144),(2,"B",150),(2,"C",144))
        emitted=[]
        for round_no,lane,expected_count in expected_slots:
            self.assertEqual(self.store.active_round(self.daily),round_no)
            ready=self.store.ready_daily(self.daily,lane)
            self.assertEqual(len(ready),expected_count)
            self.assertTrue(all(job["round_no"]==round_no for job in ready))
            for job in ready:
                ordinal,seq=job["account_ordinal"],job["page_seq"]
                precursor=job["predecessor_job_id"]
                cursor=(self.db.execute(
                    "SELECT cursor_out FROM page_evidence_v2 WHERE job_id=?",
                    (precursor,)).fetchone()[0] if precursor is not None else None)
                request_id=f"e2e-{ordinal:03d}-p{seq}"
                self.store.bind_daily(job["job_id"],request_id,
                    build_request_spec(
                        lane,self.roster[ordinal-1]["username"],after=cursor),
                    at=NOW)
                ids=[str(ordinal*1000+seq*10+n) for n in (1,2,3,4)]
                next_cursor=f"cursor_{ordinal:03d}_{seq}"
                proof=self.store.commit_daily_page(
                    job["job_id"],request_id,observed_at=NOW,
                    ordered_ids=ids,next_cursor=next_cursor,
                    terminal_basis="continued",
                    source_evidence_id="synthetic-source-"+request_id,
                    raw_receipt_id="synthetic-raw-"+request_id,
                    source_digest="a"*64)
                self.assertEqual(proof["status"],"committed")
                emitted.append((ordinal,seq,lane,round_no))
        self.assertEqual(len(emitted),294*3)
        self.assertEqual(len(set((ordinal,seq) for ordinal,seq,_,_ in emitted)),882)
        self.assertEqual(self.store.summary(self.daily)["states"],{"done":882})
        self.assertIsNone(self.store.active_round(self.daily))
        self.assertEqual(self.db.execute(
            "SELECT COUNT(*) FROM page_evidence_v2 WHERE job_id IN "
            "(SELECT job_id FROM jobs_v2 WHERE epoch_id=?)",
            (self.daily,)).fetchone()[0],882)
        self.assertEqual(self.db.execute(
            "SELECT COUNT(*) FROM cursor_checkpoints WHERE epoch_id=? "
            "AND stream='daily' AND page_seq=3",(self.daily,)).fetchone()[0],294)
        self.assertEqual(self.db.execute("PRAGMA foreign_key_check").fetchall(),[])
        # Root initialization is isolated by history target epoch; it must
        # never fabricate 365/730-day coverage verification.
        history_365=self.store.create_history_epoch(
            self.digest,anchor_utc=NOW,cutoff_utc=NOW-timedelta(days=365))
        history_730=self.store.create_history_epoch(
            self.digest,anchor_utc=NOW,cutoff_utc=NOW-timedelta(days=730))
        self.assertNotEqual(history_365,history_730)
        for target in (history_365,history_730):
            for account in ("KR_001","JP_093"):
                init=self.store.initialize_history_from_verified_daily(
                    target,self.daily,account,at=NOW)
                self.assertEqual(init["status"],"initialized")
                self.assertEqual(init["daily_page_seq"],3)
                self.assertEqual(init["history_state"],"open")
                self.assertIsNotNone(self.store.history_checkpoint(target,account))
            self.assertEqual(self.db.execute(
                "SELECT COUNT(*) FROM coverage_ledger WHERE epoch_id=? AND "
                "status='verified'",(target,)).fetchone()[0],0)
        self.assertEqual(self.db.execute(
            "SELECT COUNT(*) FROM jobs_v2 WHERE epoch_id=? AND state='done'",
            (self.daily,)).fetchone()[0],882)

    def test_three_day_rollover_pauses_unsent_daily_but_recovers_old_bound(self):
        """A previously bound HTTP result may commit; no old unbound sends.

        Exercise KST midnight, day+2 and a disk-backed store restart.
        Only synthetic receipt proofs are created.
        """
        bound=self.job(1,1)
        old_unsent=self.job(2,1)
        self.store.bind_daily(bound,"req-before-midnight",
            build_request_spec("A","nail_001"),at=NOW)
        midnight=datetime(2026,10,20,15,0,tzinfo=timezone.utc)
        day2=self.store.seed_daily(DAY+timedelta(days=1),self.digest,
                                    anchor_utc=midnight)
        day3=self.store.seed_daily(DAY+timedelta(days=2),self.digest,
                                    anchor_utc=midnight+timedelta(days=1))
        self.assertEqual(self.store.seed_daily(
            DAY+timedelta(days=2),self.digest,
            anchor_utc=midnight+timedelta(days=1)),day3)
        self.assertEqual(len(self.store.ready_daily(day3,"A")),150)
        self.assertEqual(self.store.ready_daily(day2,"A"),[])
        self.assertEqual(self.store.ready_daily(self.daily,"A"),[])
        with self.assertRaises(EpochError):
            self.store.bind_daily(old_unsent,"req-stale-daily",
                build_request_spec("A","nail_002"),
                at=midnight+timedelta(days=1))
        with self.assertRaises(EpochError):
            self.store.bind_daily(self.job(1,1,epoch=day2),
                "req-intermediate-daily",
                build_request_spec("A","nail_001"),
                at=midnight+timedelta(days=1))
        # Delayed bound day1 response is still legally committed to day1.
        self.assertEqual(self.proof(bound,"req-before-midnight",
            ids=["111","112","113","114"],nxt="old_verified_cursor",
            at=midnight+timedelta(days=1))["status"],"committed")
        self.assertEqual(self.db.execute(
            "SELECT state FROM jobs_v2 WHERE job_id=?",
            (old_unsent,)).fetchone()[0],"pending")
        self.assertEqual(self.db.execute(
            "SELECT COUNT(*) FROM page_evidence_v2 WHERE job_id=?",
            (self.job(1,1,epoch=day3),)).fetchone()[0],0)
        with tempfile.TemporaryDirectory() as tmp:
            disk=sqlite3.connect(Path(tmp)/"synthetic_epoch.sqlite",
                                 isolation_level=None)
            disk.row_factory=sqlite3.Row
            try:
                self.db.backup(disk)
                restarted=ThreeApiEpochStore(disk)
                self.assertEqual(restarted.ready_daily(self.daily,"A"),[])
                self.assertEqual(restarted.ready_daily(day2,"A"),[])
                self.assertEqual(len(restarted.ready_daily(day3,"A")),150)
                self.assertEqual(restarted.summary(day3)["jobs"],882)
                self.assertEqual(restarted.history_checkpoint(self.daily,"KR_001"),None)
                pending=self.job(1,1,epoch=day3)
                restarted.bind_daily(pending,"req-new-day",
                    build_request_spec("A","nail_001"),
                    at=midnight+timedelta(days=1))
                self.assertEqual(restarted.commit_daily_page(
                    pending,"req-new-day",
                    observed_at=midnight+timedelta(days=1),
                    ordered_ids=["201","202","203","204"],
                    next_cursor="new_day_cursor",terminal_basis="continued",
                    source_evidence_id="synthetic-new-day",
                    raw_receipt_id="synthetic-new-raw",
                    source_digest="a"*64)["status"],"committed")
                self.assertEqual(restarted.summary(self.daily)["states"]["done"],1)
                self.assertEqual(restarted.summary(day3)["states"]["done"],1)
                self.assertEqual(disk.execute("PRAGMA foreign_key_check").fetchall(),[])
            finally:
                disk.close()

    def test_three_day_rollover_replays_bound_history_without_reset(self):
        """History received after rollover commits, but cannot send a new page."""
        history=self.open_history_fixture()
        page=self.store.schedule_one_history_page(history,"KR_001","A",at=NOW)
        self.store.bind_history(page["job_id"],"req-three-day-history",
            build_request_spec("A","nail_001",after="history_first_cursor"),
            at=NOW)
        before=self.store.history_checkpoint(history,"KR_001")
        with tempfile.TemporaryDirectory() as tmp:
            mirror=OfflineRawMirror(Path(tmp)/"three_api_raw_mirror.sqlite")
            try:
                bridge=OfflineHistoryRawBridge(self.store,mirror)
                bridge.stage_capture(page["job_id"],"req-three-day-history",
                    observed_at=NOW,posts=[{"id":"991","like_count":0}],
                    next_cursor="three_day_resume_cursor",
                    terminal_basis="continued")
                midnight=datetime(2026,10,20,15,0,tzinfo=timezone.utc)
                day2=self.store.seed_daily(DAY+timedelta(days=1),self.digest,
                                            anchor_utc=midnight)
                day3=self.store.seed_daily(DAY+timedelta(days=2),self.digest,
                                            anchor_utc=midnight+timedelta(days=1))
                self.assertEqual(self.store.summary(day2)["jobs"],882)
                self.assertEqual(self.store.summary(day3)["jobs"],882)
                self.assertEqual(self.store.history_checkpoint(history,"KR_001"),before)
                # This is local replay only: never a fresh send while newest
                # KST daily front remains unresolved.
                self.assertEqual(bridge.reconcile_pending()[0]["status"],"committed")
                cp=self.store.history_checkpoint(history,"KR_001")
                self.assertEqual(cp["cursor"],"three_day_resume_cursor")
                self.assertEqual(cp["page_seq"],1)
                self.assertEqual(bridge.reconcile_pending()[0]["status"],
                                 "already_committed")
                with self.assertRaises(EpochError):
                    self.store.schedule_one_history_page(history,"KR_001","B",
                        at=midnight+timedelta(days=1))
                self.assertEqual(mirror.summary()["raw_posts"],1)
                self.assertEqual(mirror.summary()["raw_post_observations"],1)
                self.assertEqual(self.store.history_checkpoint(history,"KR_001"),cp)
            finally:
                mirror.close()

    def test_history_replay_rejects_tampered_anchor_without_db_changes(self):
        history=self.open_history_fixture()
        page=self.store.schedule_one_history_page(history,"KR_001","A",at=NOW)
        self.store.bind_history(page["job_id"],"req-anchor-replay",
            build_request_spec("A","nail_001",after="history_first_cursor"),at=NOW)
        args=dict(observed_at=NOW,ordered_ids=["901","902"],
                  next_cursor="anchor_next_cursor",terminal_basis="continued",
                  source_evidence_id="safe-anchor",
                  raw_receipt_id="raw-anchor",source_digest="a"*64)
        self.assertEqual(self.store.commit_history_page(
            page["job_id"],"req-anchor-replay",**args)["status"],"committed")
        self.db.execute("""UPDATE cursor_checkpoints SET anchor_post_id='forged'
            WHERE epoch_id=? AND account_key='KR_001' AND stream='history'""",
            (history,))
        before=self.db.total_changes
        with self.assertRaises(EpochError):
            self.store.commit_history_page(page["job_id"],"req-anchor-replay",**args)
        self.assertEqual(self.db.total_changes,before)
        self.assertEqual(self.db.execute(
            "SELECT COUNT(*) FROM page_evidence_v2 WHERE job_id=?",
            (page["job_id"],)).fetchone()[0],1)

    def test_history_replay_rejects_corrupt_media_json_without_db_changes(self):
        history=self.open_history_fixture()
        page=self.store.schedule_one_history_page(history,"KR_001","A",at=NOW)
        self.store.bind_history(page["job_id"],"req-json-replay",
            build_request_spec("A","nail_001",after="history_first_cursor"),at=NOW)
        args=dict(observed_at=NOW,ordered_ids=["901","902"],
                  next_cursor="json_next_cursor",terminal_basis="continued",
                  source_evidence_id="safe-json",
                  raw_receipt_id="raw-json",source_digest="a"*64)
        self.store.commit_history_page(page["job_id"],"req-json-replay",**args)
        self.db.execute("UPDATE page_evidence_v2 SET ordered_ids_json=? WHERE job_id=?",
                        ("{corrupt-json",page["job_id"]))
        before=self.db.total_changes
        with self.assertRaisesRegex(EpochError,"media evidence is invalid"):
            self.store.commit_history_page(page["job_id"],"req-json-replay",**args)
        self.assertEqual(self.db.total_changes,before)

    def test_history_replay_rejects_invalid_media_shapes(self):
        """A durable replay cannot trust parseable but invalid JSON lists."""
        history=self.open_history_fixture()
        page=self.store.schedule_one_history_page(history,"KR_001","A",at=NOW)
        request_id="req-shape"
        self.store.bind_history(page["job_id"],request_id,
            build_request_spec("A","nail_001",after="history_first_cursor"),at=NOW)
        args=dict(observed_at=NOW,ordered_ids=["901"],
                  next_cursor="shape_next_cursor",terminal_basis="continued",
                  source_evidence_id="safe-shape",raw_receipt_id="raw-shape",
                  source_digest="a"*64)
        self.store.commit_history_page(page["job_id"],request_id,**args)
        for payload in ('null','{}','"901"','[901]','["901","901"]',
                        '["901","902","903","904","905"]','["not-a-media-id"]'):
            with self.subTest(payload=payload):
                self.db.execute("UPDATE page_evidence_v2 SET ordered_ids_json=? WHERE job_id=?",
                                (payload,page["job_id"]))
                before=self.db.total_changes
                with self.assertRaisesRegex(EpochError,"media evidence is invalid"):
                    self.store.commit_history_page(page["job_id"],request_id,**args)
                self.assertEqual(self.db.total_changes,before)
        self.db.execute("UPDATE page_evidence_v2 SET ordered_ids_json=? WHERE job_id=?",
                        ('["901"]',page["job_id"]))
        self.assertEqual(self.store.commit_history_page(
            page["job_id"],request_id,**args)["status"],"already_committed")

    def test_history_replay_rejects_rewritten_latest_chain_payload(self):
        """Replay of an old page cannot bless a rewritten later-page proof."""
        history=self.open_history_fixture()
        first=self.store.schedule_one_history_page(history,"KR_001","A",at=NOW)
        self.store.bind_history(first["job_id"],"req-chain-one",
            build_request_spec("A","nail_001",after="history_first_cursor"),at=NOW)
        first_args=dict(observed_at=NOW,ordered_ids=["901","902","903","904"],
            next_cursor="chain_page_two",terminal_basis="continued",
            source_evidence_id="chain-one",raw_receipt_id="raw-chain-one",
            source_digest="a"*64)
        self.store.commit_history_page(first["job_id"],"req-chain-one",**first_args)
        second=self.store.schedule_one_history_page(history,"KR_001","B",at=NOW)
        self.store.bind_history(second["job_id"],"req-chain-two",
            build_request_spec("B","nail_001",after="chain_page_two"),at=NOW)
        second_args=dict(observed_at=NOW,ordered_ids=["905","906"],
            next_cursor="chain_page_three",terminal_basis="continued",
            source_evidence_id="chain-two",raw_receipt_id="raw-chain-two",
            source_digest="b"*64)
        self.store.commit_history_page(second["job_id"],"req-chain-two",**second_args)
        self.assertEqual(self.store.commit_history_page(
            first["job_id"],"req-chain-one",**first_args)["status"],"already_committed")
        # Alter evidence not represented by checkpoint fields, leaving the
        # recorded chain hash unchanged; replay must independently recalculate.
        self.db.execute("""UPDATE page_evidence_v2 SET source_evidence_id=?
            WHERE job_id=?""",("forged-chain-two",second["job_id"]))
        before=self.db.total_changes
        with self.assertRaisesRegex(EpochError,"chain digest differs"):
            self.store.commit_history_page(first["job_id"],"req-chain-one",**first_args)
        self.assertEqual(self.db.total_changes,before)

    def test_history_replay_rejects_latest_request_cursor_tampering(self):
        history=self.open_history_fixture()
        first=self.store.schedule_one_history_page(history,"KR_001","A",at=NOW)
        self.store.bind_history(first["job_id"],"req-bound-cursor-one",
            build_request_spec("A","nail_001",after="history_first_cursor"),at=NOW)
        args=dict(observed_at=NOW,ordered_ids=["901"],
            next_cursor="after_bound_cursor",terminal_basis="continued",
            source_evidence_id="cursor-proof",raw_receipt_id="raw-cursor",
            source_digest="a"*64)
        self.store.commit_history_page(first["job_id"],"req-bound-cursor-one",**args)
        second=self.store.schedule_one_history_page(history,"KR_001","B",at=NOW)
        self.store.bind_history(second["job_id"],"req-bound-cursor-two",
            build_request_spec("B","nail_001",after="after_bound_cursor"),at=NOW)
        self.store.commit_history_page(second["job_id"],"req-bound-cursor-two",
            observed_at=NOW,ordered_ids=["902"],next_cursor="third_cursor",
            terminal_basis="continued",source_evidence_id="cursor-proof-two",
            raw_receipt_id="raw-cursor-two",source_digest="b"*64)
        self.db.execute("""UPDATE request_bindings_v2 SET requested_cursor=?
            WHERE job_id=?""",("forged_cursor",second["job_id"]))
        before=self.db.total_changes
        with self.assertRaisesRegex(EpochError,"chain binding differs"):
            self.store.commit_history_page(first["job_id"],"req-bound-cursor-one",**args)
        self.assertEqual(self.db.total_changes,before)

    def test_history_replay_checkpoint_integrity_matrix(self):
        """A committed history replay rejects independent checkpoint mutations."""
        mutations=[
            ("cursor","forged_cursor"),
            ("anchor_post_id","999"),
            ("last_receipt","forged_receipt"),
            ("chain_hash","f"*64),
            ("updated_at","forged_timestamp"),
            ("page_seq",2),
            ("status","terminal"),
        ]
        for field,payload in mutations:
            with self.subTest(field=field):
                db=sqlite3.connect(":memory:",isolation_level=None)
                db.row_factory=sqlite3.Row
                try:
                    store=ThreeApiEpochStore(db)
                    digest=store.register_roster(official_shape(),recorded_at=NOW)
                    daily=store.seed_daily(DAY,digest,anchor_utc=NOW)
                    db.execute("""UPDATE jobs_v2 SET state='blocked',
                        blocked_reason='synthetic_fixture_only'
                        WHERE epoch_id=? AND account_ordinal>1""",(daily,))
                    for seq,lane,req,after,nxt in (
                        (1,"A","matrix-front-1",None,"matrix_front_2"),
                        (2,"C","matrix-front-2","matrix_front_2","matrix_front_3"),
                        (3,"B","matrix-front-3","matrix_front_3","matrix_history_seed")):
                        job=db.execute("""SELECT job_id FROM jobs_v2
                            WHERE epoch_id=? AND account_ordinal=1 AND page_seq=?""",
                            (daily,seq)).fetchone()[0]
                        store.bind_daily(job,req,
                            build_request_spec(lane,"nail_001",after=after),at=NOW)
                        store.commit_daily_page(job,req,observed_at=NOW,
                            ordered_ids=[str(100+seq)],next_cursor=nxt,
                            terminal_basis="continued",source_evidence_id=req,
                            raw_receipt_id="raw-"+req,source_digest="a"*64)
                    history=store.create_history_epoch(digest,anchor_utc=NOW,
                        cutoff_utc=NOW-timedelta(days=365))
                    store.initialize_history_from_verified_daily(history,daily,"KR_001",at=NOW)
                    page=store.schedule_one_history_page(history,"KR_001","A",at=NOW)
                    req="history-checkpoint-matrix"
                    store.bind_history(page["job_id"],req,
                        build_request_spec("A","nail_001",
                            after="matrix_history_seed"),at=NOW)
                    args=dict(observed_at=NOW,ordered_ids=["901","902"],
                        next_cursor="matrix_history_next",terminal_basis="continued",
                        source_evidence_id="history-source",
                        raw_receipt_id="history-raw",source_digest="b"*64)
                    store.commit_history_page(page["job_id"],req,**args)
                    db.execute("UPDATE cursor_checkpoints SET "+field+"""=?
                        WHERE epoch_id=? AND account_key='KR_001'
                          AND stream='history'""",(payload,history))
                    before=db.total_changes
                    with self.assertRaises(EpochError):
                        store.commit_history_page(page["job_id"],req,**args)
                    self.assertEqual(db.total_changes,before)
                finally:
                    db.close()

    def test_history_invalid_receipt_input_matrix(self):
        """Reject twelve malformed history proofs without advancing cursor."""
        history=self.open_history_fixture()
        page=self.store.schedule_one_history_page(history,"KR_001","A",at=NOW)
        request="req-history-invalid-matrix"
        self.store.bind_history(page["job_id"],request,
            build_request_spec("A","nail_001",after="history_first_cursor"),at=NOW)
        args=dict(observed_at=NOW,ordered_ids=["901"],
            next_cursor="history_valid_next",terminal_basis="continued",
            source_evidence_id="safe-history",raw_receipt_id="safe-history-raw",
            source_digest="a"*64)
        cases=[
            ("ordered_ids",None),
            ("ordered_ids","901"),
            ("ordered_ids",["1","2","3","4","5"]),
            ("ordered_ids",["901","901"]),
            ("ordered_ids",["invalid"]),
            ("ordered_ids",[901]),
            ("ordered_ids",["9"*65]),
            ("next_cursor","bad cursor"),
            ("terminal_basis","unsupported"),
            ("source_evidence_id",""),
            ("raw_receipt_id",""),
            ("source_digest","not-a-digest"),
        ]
        before_cp=self.store.history_checkpoint(history,"KR_001")
        for field,payload in cases:
            with self.subTest(field=field,payload=repr(payload)):
                invalid={**args,field:payload}
                before=self.db.total_changes
                with self.assertRaises((EpochError,TypeError)):
                    self.store.commit_history_page(page["job_id"],request,**invalid)
                self.assertEqual(self.db.total_changes,before)
                self.assertEqual(self.store.history_checkpoint(history,"KR_001"),before_cp)
        self.assertEqual(self.db.execute("""SELECT COUNT(*) FROM page_evidence_v2
            WHERE job_id=?""",(page["job_id"],)).fetchone()[0],0)

    def test_history_invalid_request_id_matrix(self):
        history=self.open_history_fixture()
        page=self.store.schedule_one_history_page(history,"KR_001","A",at=NOW)
        request="history-request-id-matrix"
        self.store.bind_history(page["job_id"],request,
            build_request_spec("A","nail_001",after="history_first_cursor"),at=NOW)
        args=dict(observed_at=NOW,ordered_ids=["901"],
            next_cursor="history_id_next",terminal_basis="continued",
            source_evidence_id="history-id-source",raw_receipt_id="history-id-raw",
            source_digest="a"*64)
        for invalid in (None,0,False,[],{},b"bytes","", "different-request"):
            with self.subTest(request=repr(invalid)):
                before=self.db.total_changes
                with self.assertRaises(EpochError):
                    self.store.commit_history_page(page["job_id"],invalid,**args)
                self.assertEqual(self.db.total_changes,before)
        self.assertEqual(self.db.execute("""SELECT COUNT(*) FROM page_evidence_v2
            WHERE job_id=?""",(page["job_id"],)).fetchone()[0],0)

    def test_bound_job_cannot_be_discarded_as_account_exception(self):
        first=self.job(1,1)
        self.store.bind_daily(first,"req-bound",build_request_spec("A","nail_001"),at=NOW)
        with self.assertRaises(EpochError):
            self.store.block_daily_account(self.daily,"KR_001",reason="permission_denied")
        self.assertEqual(self.db.execute("SELECT state FROM jobs_v2 WHERE job_id=?",
                                        (first,)).fetchone()[0],"bound")


if __name__=="__main__":
    unittest.main()
