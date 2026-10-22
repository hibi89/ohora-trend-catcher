"""Synthetic-only serial lane fallback across quota-blocked catch-up APIs.

There is no Meta request, credential read, or production database access.
These regressions preserve the frozen 294-account roster and cursor proofs.
"""
from pathlib import Path
import sys
import tempfile
import unittest
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "raw_v2"))

from test_three_api_daily_orchestrator import DAY, NOW, accounts, envelope
from three_api_daily_gap import DailyGapLedger
from three_api_daily_orchestrator import OfflineDailyOrchestrator


class SerialCatchupTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        root = Path(temporary.name)
        source = root / "source.sqlite"
        ledger_path = root / "gap.sqlite"
        mirror = root / "three_api_raw_mirror.sqlite"
        baseline = DailyGapLedger(ledger_path)
        baseline.seed_previous_head("KR_001", "100", day="2026-10-18")
        baseline.close()
        self.runner = OfflineDailyOrchestrator(
            source, ledger_path, mirror, accounts(), DAY,
            {}, {("KR_001", DAY, "p4"): envelope([103, 102, 101, 100])},
        )
        self.addCleanup(self.runner.close)
        for cursor, ids, following in (
            (None, [115, 114, 113, 112], "p2"),
            ("p2", [111, 110, 109, 108], "p3"),
            ("p3", [107, 106, 105, 104], "p4"),
        ):
            self.runner.ledger.record_page(
                "KR_001", DAY, phase="front",
                requested_cursor=cursor,
                post_ids=[str(i) for i in ids], next_cursor=following,
            )
        for account in accounts()[1:]:
            self.runner.ledger.record_page(
                account["key"], DAY, phase="front",
                post_ids=[str(20000 + account["ordinal"])],
            )
        self.assertEqual(self.runner.coverage()["front_incomplete"], 0)
        self.assertEqual(self.runner.coverage()["unresolved_gap_windows"], 1)

    def test_serial_catchup_uses_b_if_a_quota_blocked(self):
        self.runner.coordinator.observe_usage("A", 85, now=NOW)
        result = self.runner.step_serial(now=NOW)
        self.assertEqual((result["status"], result["selected_lane"]),
                         ("applied", "B"))
        self.assertEqual(self.runner.mirror.summary()["raw_posts"], 4)
        self.assertEqual(self.runner.coverage()["unresolved_gap_windows"], 0)
        self.assertEqual(self.runner.coordinator.db.execute(
            "SELECT halted FROM lanes WHERE lane='A'").fetchone()[0], 1)

    def test_serial_catchup_uses_c_if_a_and_b_quota_blocked(self):
        for lane in ("A", "B"):
            self.runner.coordinator.observe_usage(lane, 85, now=NOW)
        result = self.runner.step_serial(now=NOW)
        self.assertEqual((result["status"], result["selected_lane"]),
                         ("applied", "C"))
        self.assertEqual(self.runner.mirror.summary()["raw_posts"], 4)
        self.assertEqual(self.runner.ledger.db.execute(
            "SELECT COUNT(*) FROM page_proof WHERE phase='catchup'"
        ).fetchone()[0], 1)

    def test_serial_catchup_fallback_reconciles_roster_only_once(self):
        self.runner.coordinator.observe_usage("A", 85, now=NOW)
        self.runner.coordinator.observe_usage("B", 85, now=NOW)
        with mock.patch.object(
            self.runner, "reconcile", wraps=self.runner.reconcile
        ) as reconcile:
            result = self.runner.step_serial(now=NOW)
        self.assertEqual(result["selected_lane"], "C")
        self.assertEqual(result["status"], "applied")
        self.assertEqual(reconcile.call_count, 1)
        self.assertEqual(self.runner.mirror.summary()["raw_posts"], 4)

    def test_serial_recovery_failure_stops_before_any_lane_dispatch(self):
        with mock.patch.object(
            self.runner, "reconcile", side_effect=RuntimeError("synthetic corrupt evidence")
        ) as reconcile:
            result = self.runner.step_serial(now=NOW)
        self.assertEqual(result["status"], "recovery_blocked")
        self.assertEqual(result["selected_lane"], "A")
        self.assertEqual(reconcile.call_count, 1)
        self.assertEqual(self.runner.mirror.summary()["raw_posts"], 0)
        self.assertEqual(self.runner.ledger.db.execute(
            "SELECT COUNT(*) FROM page_proof WHERE phase='catchup'"
        ).fetchone()[0], 0)

    def test_serial_catchup_holds_when_all_three_quota_blocked(self):
        for lane in ("A", "B", "C"):
            self.runner.coordinator.observe_usage(lane, 85, now=NOW)
        result = self.runner.step_serial(now=NOW)
        self.assertEqual(result,
                         {"status": "all_lanes_unavailable",
                          "selected_lane": None})
        self.assertEqual(self.runner.mirror.summary()["raw_posts"], 0)
        self.assertEqual(self.runner.ledger.db.execute(
            "SELECT COUNT(*) FROM page_proof WHERE phase='catchup'"
        ).fetchone()[0], 0)
        self.assertEqual(self.runner.coverage()["unresolved_gap_windows"], 1)


if __name__ == "__main__":
    unittest.main()
