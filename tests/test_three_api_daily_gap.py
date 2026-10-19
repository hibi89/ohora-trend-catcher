"""No-network daily gap / evidence / resumption tests."""
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
import sys
import tempfile
import unittest

sys.path.insert(0,str(Path(__file__).resolve().parents[1]/"raw_v2"))
from three_api_daily_gap import DailyGapLedger, GapEvidenceError, day_kst


class DailyGapTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.path = Path(self.tmp.name)/"three_api_daily_gap.sqlite"
        self.l = DailyGapLedger(self.path)
        self.addCleanup(self.l.close)

    def seed(self, account="KR_001"):
        self.l.seed_previous_head(account,"100")
        self.l.begin_day(account,"2026-10-19")

    def front(self, account="KR_001",day="2026-10-19",new_start=115):
        # 15 new posts newer than the saved anchor 100.
        self.l.record_page(account,day,phase="front",post_ids=[str(x) for x in range(new_start,new_start-4,-1)],next_cursor="p2")
        self.l.record_page(account,day,phase="front",requested_cursor="p2",post_ids=[str(x) for x in range(new_start-4,new_start-8,-1)],next_cursor="p3")
        return self.l.record_page(account,day,phase="front",requested_cursor="p3",post_ids=[str(x) for x in range(new_start-8,new_start-12,-1)],next_cursor="p4")

    def test_fifteen_new_front_twelve_then_catchup_three_and_anchor(self):
        self.seed()
        status=self.front()
        self.assertEqual(status["front_pages"],3)
        self.assertEqual(status["status"],"catchup_pending")
        self.assertEqual(self.l.next_actions(["KR_001"],"2026-10-19")[0]["phase"],"catchup")
        status=self.l.record_page("KR_001","2026-10-19",phase="catchup",requested_cursor="p4",post_ids=["103","102","101","100"],next_cursor="p5")
        self.assertEqual(status["status"],"verified")
        self.assertEqual(status["catchup_pages"],1)
        self.assertEqual(self.l.next_actions(["KR_001"],"2026-10-19")[0]["phase"],"history")
        self.assertEqual(self.l.db.execute("SELECT COUNT(*) FROM page_proof").fetchone()[0],4)

    def test_catchup_persists_across_restart(self):
        self.seed();self.front()
        self.l.close()
        self.l=DailyGapLedger(self.path)
        status=self.l.record_page("KR_001","2026-10-19",phase="catchup",requested_cursor="p4",post_ids=["103","102","101","100"])
        self.assertEqual(status["status"],"verified")

    def test_new_day_front_does_not_erase_old_gap(self):
        self.seed();self.front()
        self.l.begin_day("KR_001","2026-10-20")
        self.l.record_page("KR_001","2026-10-20",phase="front",post_ids=["117","116","115"],next_cursor="day2")
        # Day 2 reached previous head (115), but day 1 still needs gap to 100.
        self.assertEqual(self.l.status("KR_001","2026-10-20")["status"],"verified")
        self.assertEqual(self.l.status("KR_001","2026-10-19")["status"],"catchup_pending")
        next=self.l.next_actions(["KR_001"],"2026-10-20")
        self.assertEqual(next[0]["phase"],"catchup")
        self.assertEqual(next[0]["gap_day"],"2026-10-19")

    def test_front_is_first_for_all_accounts(self):
        self.seed();self.front()
        self.l.seed_previous_head("US_151","200")
        self.l.begin_day("US_151","2026-10-19")
        queue=self.l.next_actions(["KR_001","US_151"],"2026-10-19")
        self.assertEqual([i["phase"] for i in queue],["front"])

    def test_empty_next_page_before_anchor_is_not_verified(self):
        self.seed();self.front()
        status=self.l.record_page("KR_001","2026-10-19",phase="catchup",requested_cursor="p4",post_ids=[])
        self.assertEqual(status["status"],"needs_review")
        self.assertEqual(self.l.next_actions(["KR_001"],"2026-10-19")[0]["phase"],"needs_review")

    def test_no_cursor_without_anchor_is_not_verified(self):
        self.seed()
        self.l.record_page("KR_001","2026-10-19",phase="front",post_ids=["115","114"])
        self.assertEqual(self.l.status("KR_001","2026-10-19")["status"],"needs_review")

    def test_cursor_mismatch_rolls_back_page(self):
        self.seed();self.front()
        before=self.l.db.execute("SELECT COUNT(*) FROM page_proof").fetchone()[0]
        with self.assertRaises(GapEvidenceError):
            self.l.record_page("KR_001","2026-10-19",phase="catchup",requested_cursor="bad",post_ids=["103","102","101","100"])
        self.assertEqual(self.l.db.execute("SELECT COUNT(*) FROM page_proof").fetchone()[0],before)

    def test_duplicate_replayed_page_rejected(self):
        self.seed()
        self.l.record_page("KR_001","2026-10-19",phase="front",post_ids=["115","114","113","112"],next_cursor="p2")
        with self.assertRaises(GapEvidenceError):
            self.l.record_page("KR_001","2026-10-19",phase="front",requested_cursor="p2",post_ids=["115","111"],next_cursor="p3")
        self.assertEqual(self.l.status("KR_001","2026-10-19")["front_pages"],1)

    def test_cursor_cycle_rejected(self):
        self.seed()
        self.l.record_page("KR_001","2026-10-19",phase="front",post_ids=["115","114"],next_cursor="p2")
        with self.assertRaises(GapEvidenceError):
            self.l.record_page("KR_001","2026-10-19",phase="front",requested_cursor="p2",post_ids=["113","112"],next_cursor="p2")
        with self.assertRaises(GapEvidenceError):
            self.l.record_page("KR_001","2026-10-19",phase="front",requested_cursor="bad",post_ids=["113","112"],next_cursor="p3")

    def test_only_three_front_pages_then_additional_catchup(self):
        self.seed();self.front()
        with self.assertRaises(GapEvidenceError):
            self.l.record_page("KR_001","2026-10-19",phase="front",requested_cursor="p4",post_ids=["103","102","101","100"])

    def test_bootstrap_not_claimed_continuity_verified(self):
        self.l.begin_day("KR_001","2026-10-19")
        self.l.record_page("KR_001","2026-10-19",phase="front",post_ids=["115","114","113","112"],next_cursor="p2")
        self.l.record_page("KR_001","2026-10-19",phase="front",requested_cursor="p2",post_ids=["111","110","109","108"],next_cursor="p3")
        status=self.l.record_page("KR_001","2026-10-19",phase="front",requested_cursor="p3",post_ids=["107","106","105","104"],next_cursor="p4")
        self.assertEqual(status["status"],"bootstrap_history")
        self.assertEqual(self.l.next_actions(["KR_001"],"2026-10-19")[0]["phase"],"history_unverified")

    def test_rejects_media_ids_cursor_and_pages_outside_contract(self):
        self.seed()
        with self.assertRaises(GapEvidenceError):
            self.l.record_page("KR_001","2026-10-19",phase="front",post_ids=["1","2","3","4","5"])
        with self.assertRaises(GapEvidenceError):
            self.l.record_page("KR_001","2026-10-19",phase="front",post_ids=["foo"])
        with self.assertRaises(GapEvidenceError):
            self.l.record_page("KR_001","2026-10-19",phase="front",post_ids=["1"],next_cursor="https://x/?access_token=xx")

    def test_kst_boundary(self):
        self.assertEqual(day_kst(datetime(2026,10,19,14,59,tzinfo=timezone.utc)),"2026-10-19")
        self.assertEqual(day_kst(datetime(2026,10,19,15,0,tzinfo=timezone.utc)),"2026-10-20")
        with self.assertRaises(GapEvidenceError):
            day_kst(datetime(2026,10,19))

    def test_history_global_barrier_and_review(self):
        self.seed();self.front()
        self.l.seed_previous_head("US_151","200")
        self.l.begin_day("US_151","2026-10-19")
        self.l.record_page("US_151","2026-10-19",phase="front",post_ids=["201","200"])
        queue=self.l.next_actions(["KR_001","US_151"],"2026-10-19")
        self.assertEqual([x["phase"] for x in queue],["catchup"])
        self.l.record_page("KR_001","2026-10-19",phase="catchup",requested_cursor="p4",post_ids=["103","102","101","100"])
        self.assertEqual([x["phase"] for x in self.l.next_actions(["KR_001","US_151"],"2026-10-19")],["history","history"])

    def test_front_with_missing_predecessor_requires_manual_review(self):
        self.seed()
        self.l.record_page("KR_001","2026-10-19",phase="front",post_ids=["115","114","113","112"],next_cursor="p2")
        self.l.record_page("KR_001","2026-10-19",phase="front",requested_cursor="p2",post_ids=["111","110","109","108"],next_cursor="p3")
        self.l.record_page("KR_001","2026-10-19",phase="front",requested_cursor="p3",post_ids=["107","106","105","104"],next_cursor="p4")
        self.l.record_page("KR_001","2026-10-19",phase="catchup",requested_cursor="p4",post_ids=["103","102"],next_cursor="p5")
        self.assertEqual(self.l.status("KR_001","2026-10-19")["status"],"catchup_pending")
        self.l.record_page("KR_001","2026-10-19",phase="catchup",requested_cursor="p5",post_ids=["101"])
        self.assertEqual(self.l.status("KR_001","2026-10-19")["status"],"needs_review")

    def test_production_basename_refused(self):
        with self.assertRaises(GapEvidenceError):
            DailyGapLedger(Path(self.tmp.name)/"collection.sqlite")

    def test_replay_after_verified_refused(self):
        self.seed();self.front()
        self.l.record_page("KR_001","2026-10-19",phase="catchup",requested_cursor="p4",post_ids=["103","102","101","100"])
        with self.assertRaises(GapEvidenceError):
            self.l.record_page("KR_001","2026-10-19",phase="catchup",requested_cursor="p5",post_ids=["99"])

    def test_previous_day_partial_front_blocks_history_on_next_day(self):
        self.seed()
        self.l.record_page("KR_001","2026-10-19",phase="front",
                           post_ids=["115","114"],next_cursor="p2")
        self.l.begin_day("KR_001","2026-10-20")
        self.l.record_page("KR_001","2026-10-20",phase="front",
                           post_ids=["117","115"])
        actions=self.l.next_actions(["KR_001"],"2026-10-20")
        self.assertEqual(actions[0]["phase"],"needs_review")
        self.assertEqual(actions[0]["gap_day"],"2026-10-19")

    def test_existing_earlier_daily_window_can_resume_after_new_head(self):
        self.seed()
        self.l.record_page("KR_001","2026-10-19",phase="front",
                           post_ids=["115","114"],next_cursor="p2")
        self.l.begin_day("KR_001","2026-10-20")
        self.l.record_page("KR_001","2026-10-20",phase="front",
                           post_ids=["117","115"])
        state=self.l.begin_day("KR_001","2026-10-19")
        self.assertEqual(state["front_pages"],1)
        self.l.record_page("KR_001","2026-10-19",phase="front",
                           requested_cursor="p2",post_ids=["113","112"],
                           next_cursor="p3")
        self.assertEqual(self.l.status("KR_001","2026-10-19")["front_pages"],2)

if __name__ == "__main__":
    unittest.main()
