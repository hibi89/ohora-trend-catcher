"""Re-anchor evidence remains offline and independent of Meta API/RAW DB."""
from datetime import datetime, timezone
from pathlib import Path
import sys
import tempfile
import unittest

sys.path.insert(0,str(Path(__file__).resolve().parents[1]/"raw_v2"))
from three_api_daily_gap import DailyGapLedger,GapEvidenceError
from three_api_reanchor import ReanchorEvidenceStore


class ReanchorTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.path=Path(self.tmp.name)/"gap.sqlite"
        self.ledger=DailyGapLedger(self.path)
        self.addCleanup(self.ledger.close)
        self.reanchor=ReanchorEvidenceStore(self.ledger)
        self.ledger.seed_previous_head("KR_001","100")
        self.ledger.begin_day("KR_001","2026-10-19")
        for idx,ids,next_cursor,requested in [
            (1,["115","114","113","112"],"p2",None),
            (2,["111","110","109","108"],"p3","p2"),
            (3,["107","106","105","104"],"p4","p3"),
        ]:
            self.ledger.record_page("KR_001","2026-10-19",
                phase="front",requested_cursor=requested,
                post_ids=ids,next_cursor=next_cursor)
        self.assertEqual(self.ledger.status("KR_001","2026-10-19")["status"],
                         "catchup_pending")

    def test_expired_cursor_replays_from_current_head_to_original_anchor(self):
        self.ledger.begin_day("KR_001","2026-10-20")
        self.ledger.record_page("KR_001","2026-10-20",
             phase="front",post_ids=["117","116","115"],next_cursor="new_head_p2")
        self.assertEqual(self.ledger.status("KR_001","2026-10-20")["status"],"verified")
        self.reanchor.begin("KR_001","2026-10-19","2026-10-20")
        action=self.ledger.next_actions(["KR_001"],"2026-10-20")[0]
        self.assertEqual(action["phase"],"reanchor")
        self.assertEqual(action["gap_day"],"2026-10-19")
        old_count=self.ledger.db.execute("SELECT COUNT(*) FROM page_proof").fetchone()[0]
        for requested,posts,nxt in [
            (None,["117","116","115","114"],"new2"),
            ("new2",["113","112","111","110"],"new3"),
            ("new3",["109","108","107","106"],"new4"),
            ("new4",["105","104","103","102"],"new5"),
            ("new5",["101","100"],None),
        ]:
            state=self.reanchor.record_page(
                "KR_001","2026-10-19","2026-10-20",
                requested_cursor=requested,post_ids=posts,next_cursor=nxt)
        self.assertEqual(state["status"],"verified")
        self.assertEqual(self.ledger.status("KR_001","2026-10-19")["status"],"verified")
        self.assertEqual(self.ledger.db.execute("SELECT COUNT(*) FROM page_proof").fetchone()[0],old_count)
        self.assertEqual(self.ledger.next_actions(["KR_001"],"2026-10-20")[0]["phase"],"history")
        self.assertEqual(self.ledger.db.execute("SELECT COUNT(*) FROM reanchor_page_proof").fetchone()[0],5)

    def test_missing_original_anchor_does_not_clear_gap(self):
        self.reanchor.begin("KR_001","2026-10-19","2026-10-20")
        result=self.reanchor.record_page(
            "KR_001","2026-10-19","2026-10-20",
            requested_cursor=None,post_ids=["117","116"])
        self.assertEqual(result["status"],"needs_review")
        self.assertEqual(self.ledger.status("KR_001","2026-10-19")["status"],"catchup_pending")
        self.ledger.begin_day("KR_001","2026-10-20")
        self.ledger.record_page("KR_001","2026-10-20",phase="front",
             post_ids=["117","116","115"])
        self.assertEqual(self.ledger.next_actions(["KR_001"],"2026-10-20")[0]["phase"],"needs_review")

    def test_failed_reanchor_can_reopen_on_later_day_only(self):
        self.reanchor.begin("KR_001","2026-10-19","2026-10-20")
        self.reanchor.record_page("KR_001","2026-10-19","2026-10-20",
            post_ids=["117"],next_cursor=None)
        with self.assertRaises(GapEvidenceError):
            self.reanchor.begin("KR_001","2026-10-19","2026-10-20")
        self.assertEqual(self.reanchor.begin("KR_001","2026-10-19","2026-10-21")["status"],"active")

    def test_cannot_open_duplicate_active_or_backwards_attempt(self):
        self.reanchor.begin("KR_001","2026-10-19","2026-10-20")
        self.assertEqual(self.reanchor.begin("KR_001","2026-10-19","2026-10-20")["status"],"active")
        with self.assertRaises(GapEvidenceError):
            self.reanchor.begin("KR_001","2026-10-19","2026-10-21")
        with self.assertRaises(GapEvidenceError):
            self.reanchor.begin("KR_001","2026-10-19","2026-10-18")

    def test_wrong_cursor_does_not_change_replay_checkpoint(self):
        self.reanchor.begin("KR_001","2026-10-19","2026-10-20")
        self.reanchor.record_page("KR_001","2026-10-19","2026-10-20",
            post_ids=["117","116"],next_cursor="new2")
        with self.assertRaises(GapEvidenceError):
            self.reanchor.record_page("KR_001","2026-10-19","2026-10-20",
                requested_cursor="stale_page4",post_ids=["115","114"],next_cursor="new3")
        self.assertEqual(self.reanchor.state("KR_001","2026-10-19","2026-10-20")["page_count"],1)

    def test_replay_duplicate_media_and_cycle_rejected(self):
        self.reanchor.begin("KR_001","2026-10-19","2026-10-20")
        self.reanchor.record_page("KR_001","2026-10-19","2026-10-20",
            post_ids=["117","116"],next_cursor="new2")
        with self.assertRaises(GapEvidenceError):
            self.reanchor.record_page("KR_001","2026-10-19","2026-10-20",
                requested_cursor="new2",post_ids=["117"],next_cursor="new3")
        with self.assertRaises(GapEvidenceError):
            self.reanchor.record_page("KR_001","2026-10-19","2026-10-20",
                requested_cursor="new2",post_ids=["115"],next_cursor="new2")

    def test_cannot_start_after_gap_verified(self):
        self.ledger.record_page("KR_001","2026-10-19",phase="catchup",
            requested_cursor="p4",post_ids=["103","102","101","100"])
        with self.assertRaises(GapEvidenceError):
            self.reanchor.begin("KR_001","2026-10-19","2026-10-20")

    def test_persists_across_process_restart(self):
        self.reanchor.begin("KR_001","2026-10-19","2026-10-20")
        self.reanchor.record_page("KR_001","2026-10-19","2026-10-20",
            post_ids=["117","116"],next_cursor="new2")
        self.ledger.close()
        self.ledger=DailyGapLedger(self.path)
        self.reanchor=ReanchorEvidenceStore(self.ledger)
        result=self.reanchor.record_page("KR_001","2026-10-19","2026-10-20",
            requested_cursor="new2",post_ids=["100"])
        self.assertEqual(result["status"],"verified")

    def test_reanchor_does_not_erase_original_evidence(self):
        before=self.ledger.db.execute(
            "SELECT COUNT(*) FROM page_proof").fetchone()[0]
        self.reanchor.begin("KR_001","2026-10-19","2026-10-20")
        self.reanchor.record_page("KR_001","2026-10-19","2026-10-20",post_ids=["100"])
        after=self.ledger.db.execute("SELECT COUNT(*) FROM page_proof").fetchone()[0]
        self.assertEqual(before,after)

    def test_invalid_cursor_fails_without_echoing_tokens(self):
        self.reanchor.begin("KR_001","2026-10-19","2026-10-20")
        try:
            self.reanchor.record_page("KR_001","2026-10-19","2026-10-20",
                post_ids=["117"],next_cursor="xyz&example=marker")
        except GapEvidenceError as e:
            self.assertNotIn("marker",str(e))
        else:
            self.fail("Unsafe cursor accepted")

if __name__ == "__main__":
    unittest.main()
