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

    def test_bound_job_cannot_be_discarded_as_account_exception(self):
        first=self.job(1,1)
        self.store.bind_daily(first,"req-bound",build_request_spec("A","nail_001"),at=NOW)
        with self.assertRaises(EpochError):
            self.store.block_daily_account(self.daily,"KR_001",reason="permission_denied")
        self.assertEqual(self.db.execute("SELECT state FROM jobs_v2 WHERE job_id=?",
                                        (first,)).fetchone()[0],"bound")


if __name__=="__main__":
    unittest.main()
