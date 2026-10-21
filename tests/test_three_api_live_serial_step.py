"""Offline safety checks for the opt-in serial runner."""
import unittest
from datetime import datetime, timezone

from three_api_live_serial_step import LiveSerialStep, LiveSerialStepError


class LiveSerialStepGateTests(unittest.TestCase):
    def setUp(self):
        # Bypass construction solely to prove input gates reject before any
        # coordinator, transport, journal, or database access.
        self.runner = object.__new__(LiveSerialStep)
        self.kwargs = dict(
            run_id="offline", roster_digest="x", day_kst=None,
            requested_at=datetime.now(timezone.utc), api_version="v26.0",
            caller_ids={"A": "1", "B": "2", "C": "3"}, approved=True,
        )

    def test_no_approval_does_not_touch_coordinator(self):
        with self.assertRaisesRegex(LiveSerialStepError, "Explicit live approval"):
            self.runner.step([], **{**self.kwargs, "approved": False})

    def test_non_boolean_approval_denied(self):
        with self.assertRaisesRegex(LiveSerialStepError, "Explicit live approval"):
            self.runner.step([], **{**self.kwargs, "approved": 1})

    def test_naive_clock_denied(self):
        with self.assertRaisesRegex(LiveSerialStepError, "Timezone-aware"):
            self.runner.step([], **{
                **self.kwargs, "requested_at": datetime(2026, 10, 21)})

    def test_partial_roster_denied(self):
        with self.assertRaisesRegex(LiveSerialStepError, "294-account"):
            self.runner.step([{}], **self.kwargs)

    def test_partial_lane_identities_denied(self):
        with self.assertRaisesRegex(LiveSerialStepError, "A/B/C"):
            self.runner.step([{} for _ in range(294)], **{
                **self.kwargs, "caller_ids": {"A": "1"}})

    def test_real_sqlite_quota_halt_blocks_dispatch_but_allows_bound_recovery(self):
        """Exercise persisted 85% gate, not a mocked coordinator."""
        import tempfile
        from pathlib import Path
        from three_api_coordinator import Coordinator
        from three_api_job_binding import JobRequestBindingStore

        with tempfile.TemporaryDirectory() as directory:
            coordinator = Coordinator(Path(directory) / "offline-quota.sqlite")
            try:
                coordinator.seed()
                JobRequestBindingStore(coordinator)
                now = self.kwargs["requested_at"]
                self.assertTrue(coordinator.observe_usage("A", 85, now=now))
                lane = coordinator.db.execute(
                    "SELECT halted,reason FROM lanes WHERE lane='A'"
                ).fetchone()
                self.assertEqual(lane["halted"], 1)
                self.assertEqual(lane["reason"], "usage_limit")
                self.assertIsNone(coordinator.lease_for_dispatch("A", now=now))
                first = coordinator.db.execute(
                    "SELECT id FROM jobs WHERE lane='A' AND page=1 "
                    "ORDER BY account_ordinal LIMIT 1"
                ).fetchone()
                coordinator.db.execute(
                    "INSERT INTO job_request_bindings "
                    "(job_id,request_id,request_digest,reserved_at) "
                    "VALUES (?,?,?,?)",
                    (first["id"], "req-" + "a" * 64, "offline-digest",
                     now.isoformat()),
                )
                report = coordinator.recovery_ready_diagnostics(now=now)
                a_lane = next(row for row in report["lanes"] if row["lane"] == "A")
                self.assertEqual(a_lane["eligible_jobs"], 1)
                self.assertEqual(a_lane["recoverable_now"], 1)
                self.assertTrue(a_lane["lane_ready"])
                recovery = coordinator.lease_for_recovery("A", now=now)
                self.assertIsNotNone(recovery)
                self.assertEqual(recovery["id"], first["id"])
                self.assertIsNone(coordinator.lease_for_dispatch("A", now=now))
                self.assertIsNotNone(coordinator.lease_for_dispatch("B", now=now))
            finally:
                coordinator.db.close()

    def test_blocked_lane_recovery_does_not_dispatch(self):
        """A quota-halted lane must never cause a fresh HTTP dispatch."""
        from unittest.mock import Mock, patch
        runner = object.__new__(LiveSerialStep)
        runner.coordinator = Mock()
        runner.coordinator.path = "isolated-test.sqlite"
        runner.coordinator.lease_for_recovery.return_value = None
        runner.coordinator.lease_for_dispatch.return_value = None
        runner.bindings = Mock()
        runner.evidence = Mock()
        runner.executor = Mock()
        runner.executor.transport._lanes = {
            lane: Mock(_caller_ig_id=caller)
            for lane, caller in self.kwargs["caller_ids"].items()
        }
        with patch("three_api_live_serial_step.serial_tick_lock") as lock:
            lock.return_value.__enter__.return_value = None
            result = runner.step([{} for _ in range(294)], **self.kwargs)
        self.assertEqual(result["status"], "no_ready_work")
        runner.bindings.dispatch_once.assert_not_called()
        self.assertEqual(runner.coordinator.lease_for_recovery.call_count, 3)


if __name__ == "__main__":
    unittest.main()
