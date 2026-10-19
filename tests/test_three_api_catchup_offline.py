"""Offline catch-up contract: no credentials, sockets or production database."""
from datetime import datetime, timezone, timedelta
from pathlib import Path
import sys
import tempfile
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "raw_v2"))
from three_api_coordinator import Coordinator
from three_api_daily_gap import DailyGapLedger
from three_api_catchup_offline import OfflineCatchupRunner, OfflineCatchupError

NOW = datetime(2026, 10, 19, 10, 0, tzinfo=timezone.utc)
TODAY = "2026-10-19"


def page(ids, after=None):
    result = {"data": [{"id": str(i), "caption": "synthetic"} for i in ids]}
    if after:
        result["paging"] = {"next": "https://graph.facebook.com/synthetic",
                            "cursors": {"after": after}}
    return result


class CatchupOfflineTests(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        root = Path(tmp.name)
        self.ledger = DailyGapLedger(root / "gap.sqlite")
        self.addCleanup(self.ledger.close)
        self.coordinator = Coordinator(root / "staging.sqlite")
        self.addCleanup(self.coordinator.db.close)
        self.ledger.seed_previous_head("KR_001", "100", day="2026-10-18")
        self.ledger.begin_day("KR_001", TODAY)
        self.fixtures = {
            ("KR_001", TODAY, "p4"): page([103, 102, 101, 100]),
        }

    def front(self):
        for cursor, ids, next_cursor in [
            (None, [115, 114, 113, 112], "p2"),
            ("p2", [111, 110, 109, 108], "p3"),
            ("p3", [107, 106, 105, 104], "p4"),
        ]:
            self.ledger.record_page("KR_001", TODAY, phase="front",
                requested_cursor=cursor, post_ids=[str(i) for i in ids],
                next_cursor=next_cursor)

    def runner(self, roster=None, fixtures=None):
        return OfflineCatchupRunner(
            self.ledger, self.coordinator,
            ["KR_001"] if roster is None else roster,
            self.fixtures if fixtures is None else fixtures)

    def test_front_priority_no_page4_read(self):
        obj = self.runner()
        self.assertEqual(obj.run_one("A", TODAY, now=NOW)["status"], "front_priority")
        self.assertEqual(self.ledger.db.execute(
            "SELECT COUNT(*) FROM catchup_inbox").fetchone()[0], 0)

    def test_fourth_page_verifies_exact_anchor(self):
        self.front()
        obj = self.runner()
        result = obj.run_one("C", TODAY, now=NOW)
        self.assertEqual(result["status"], "applied")
        self.assertEqual(result["page_no"], 1)
        self.assertEqual(result["gap_status"], "verified")
        self.assertEqual(self.ledger.db.execute(
            "SELECT COUNT(*) FROM catchup_inbox WHERE applied=1").fetchone()[0], 1)
        self.assertEqual(obj.run_one("C", TODAY, now=NOW)["status"], "no_catchup")

    def test_two_pages_preserve_predecessor_cursor(self):
        self.front()
        self.fixtures[("KR_001", TODAY, "p4")] = page([103,102,101,99], "p5")
        self.fixtures[("KR_001", TODAY, "p5")] = page([98,97,100])
        obj = self.runner()
        self.assertEqual(obj.run_one("A", TODAY, now=NOW)["gap_status"], "catchup_pending")
        self.assertEqual(obj.run_one("B", TODAY, now=NOW)["gap_status"], "verified")
        rows = self.ledger.db.execute(
            "SELECT page_no,requested_cursor FROM page_proof "
            "WHERE phase='catchup' ORDER BY page_no").fetchall()
        self.assertEqual([(r["page_no"],r["requested_cursor"]) for r in rows],
                         [(1,"p4"),(2,"p5")])

    def test_stage_crash_reconciles_without_fixture(self):
        self.front()
        obj = self.runner()
        self.assertEqual(obj.run_one("A", TODAY, now=NOW, stage_only=True)["status"],
                         "staged_unapplied")
        self.fixtures.clear()
        self.assertEqual(self.runner().run_one("B", TODAY, now=NOW)["status"], "reconciled")
        self.assertEqual(self.ledger.status("KR_001", TODAY)["status"], "verified")
        self.assertEqual(self.ledger.db.execute(
            "SELECT COUNT(*) FROM page_proof WHERE phase='catchup'").fetchone()[0],1)

    def test_crash_after_proof_before_ack_idempotent(self):
        self.front()
        obj = self.runner()
        obj.run_one("A", TODAY, now=NOW, stage_only=True)
        pending = obj._pending()
        self.ledger.record_page("KR_001", TODAY, phase="catchup",
            requested_cursor=pending["requested_cursor"],
            post_ids=["103","102","101","100"], next_cursor=None)
        self.fixtures.clear()
        self.assertEqual(obj.reconcile_pending()["status"], "applied")
        self.assertIsNone(obj.reconcile_pending())
        self.assertEqual(self.ledger.db.execute(
            "SELECT COUNT(*) FROM page_proof WHERE phase='catchup'").fetchone()[0],1)

    def test_conflicting_proof_refuses_reconciliation(self):
        self.front()
        obj = self.runner()
        obj.run_one("A", TODAY, now=NOW, stage_only=True)
        self.ledger.db.execute("UPDATE catchup_inbox SET ids_json=?", ('["999"]',))
        self.ledger.record_page("KR_001", TODAY, phase="catchup",
            requested_cursor="p4",post_ids=["103","102","101","100"])
        with self.assertRaises(OfflineCatchupError):
            obj.reconcile_pending()

    def test_missing_fixture_preserves_cursor_and_releases_lease(self):
        self.front()
        self.assertEqual(self.runner(fixtures={}).run_one("A", TODAY, now=NOW)["status"],
                         "missing_offline_fixture")
        self.assertEqual(self.ledger.status("KR_001", TODAY)["catchup_pages"],0)
        self.assertEqual(self.ledger.db.execute(
            "SELECT COUNT(*) FROM catchup_lease").fetchone()[0],0)

    def test_invalid_fixture_does_not_advance(self):
        self.front()
        bad={("KR_001", TODAY, "p4"):page([103,103,100])}
        self.assertEqual(self.runner(fixtures=bad).run_one("A", TODAY, now=NOW)["status"],
                         "invalid_offline_fixture")
        self.assertEqual(self.ledger.status("KR_001",TODAY)["catchup_pages"],0)
        self.assertEqual(self.ledger.db.execute(
            "SELECT COUNT(*) FROM catchup_inbox").fetchone()[0],0)
        self.assertEqual(self.ledger.db.execute(
            "SELECT COUNT(*) FROM catchup_lease").fetchone()[0],0)

    def test_halted_lane_cannot_stage(self):
        self.front()
        self.coordinator.observe_usage("A",None,now=NOW)
        self.assertEqual(self.runner().run_one("A",TODAY,now=NOW)["status"],"lane_blocked")
        self.assertEqual(self.runner().run_one("C",TODAY,now=NOW)["status"],"applied")

    def test_lease_blocks_duplicate_until_expiry(self):
        self.front()
        obj=self.runner()
        self.assertIsNotNone(obj._lease("KR_001", TODAY, NOW))
        self.assertEqual(obj.run_one("B",TODAY,now=NOW)["status"],"lease_busy")
        self.assertEqual(obj.run_one("B",TODAY,now=NOW+timedelta(minutes=16))["status"],
                         "applied")

    def test_terminal_without_anchor_requires_review(self):
        self.front()
        fixtures={("KR_001",TODAY,"p4"):page([103,102,101])}
        result=self.runner(fixtures=fixtures).run_one("A",TODAY,now=NOW)
        self.assertEqual(result["gap_status"],"needs_review")
        self.assertEqual(self.runner().run_one("A",TODAY,now=NOW)["status"],"no_catchup")

    def test_other_account_front_blocks_catchup(self):
        self.front()
        self.assertEqual(self.runner(["KR_001","KR_002"]).run_one("A",TODAY,now=NOW)["status"],
                         "front_priority")

    def test_no_network_callable_or_production_db(self):
        with self.assertRaises(OfflineCatchupError):
            self.runner(fixtures=lambda *args:page([100]))
        with self.assertRaises(ValueError):
            DailyGapLedger(Path(self.ledger.path.parent)/"collection.sqlite")

    def test_sensitive_fields_excluded_from_ledger(self):
        self.front()
        malicious={("KR_001",TODAY,"p4"):{
            "data":[{"id":"100","access_token":"VERY_SECRET_TOKEN"}],
            "paging":{"unused":"VERY_SECRET_TOKEN"}}}
        self.assertEqual(self.runner(fixtures=malicious).run_one("A",TODAY,now=NOW)["status"],
                         "applied")
        self.assertNotIn("VERY_SECRET_TOKEN","\n".join(self.ledger.db.iterdump()))


if __name__ == "__main__":
    unittest.main()
