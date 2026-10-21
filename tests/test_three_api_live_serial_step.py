"""Offline safety checks for the opt-in serial runner."""
import unittest
from datetime import datetime, timezone

from three_api_live_serial_step import LiveSerialStep, LiveSerialStepError

def valid_official_roster():
    """Synthetic, structurally valid 294-account fixture; no actual accounts."""
    return [{"ordinal": n, "key": f"KR_{n:03}", "username": f"nail_{n}"}
            for n in range(1, 295)]



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

    def test_quota_release_requires_completed_page(self):
        """Static regression: partial RAW/cursor failure must not clear lane."""
        from pathlib import Path
        source = (Path(__file__).resolve().parents[1] / "raw_v2"
                  / "three_api_live_serial_step.py").read_text(encoding="utf-8")
        self.assertIn("page_committed = False", source)
        self.assertIn("page_committed = True", source)
        self.assertIn("if (page_committed and type(usage) is int", source)

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

    def test_duplicate_official_username_denied_before_transport(self):
        accounts = [{"ordinal": n, "key": f"KR_{n:03}", "username": f"nail_{n}"}
                    for n in range(1, 295)]
        accounts[1]["username"] = "NAIL_1"
        with self.assertRaisesRegex(LiveSerialStepError, "Duplicate"):
            self.runner.step(accounts, **self.kwargs)

    def test_reordered_official_roster_denied_before_transport(self):
        accounts = [{"ordinal": n, "key": f"KR_{n:03}", "username": f"nail_{n}"}
                    for n in range(1, 295)]
        accounts[1]["ordinal"] = 3
        with self.assertRaisesRegex(LiveSerialStepError, "ordinal"):
            self.runner.step(accounts, **self.kwargs)

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

    def test_quota_recovery_matrix_offline(self):
        """Persisted gates cannot turn capture recovery into a fresh dispatch."""
        import tempfile
        from pathlib import Path
        from datetime import timedelta
        from three_api_coordinator import Coordinator
        from three_api_job_binding import JobRequestBindingStore

        for usage in (None, 85, 100):
            with self.subTest(usage=usage), tempfile.TemporaryDirectory() as directory:
                coordinator = Coordinator(Path(directory) / "quota-matrix.sqlite")
                try:
                    coordinator.seed()
                    JobRequestBindingStore(coordinator)
                    now = self.kwargs["requested_at"]
                    first = coordinator.db.execute(
                        "SELECT id FROM jobs WHERE lane='A' AND page=1 "
                        "ORDER BY account_ordinal LIMIT 1"
                    ).fetchone()
                    coordinator.db.execute(
                        "INSERT INTO job_request_bindings "
                        "(job_id,request_id,request_digest,reserved_at) "
                        "VALUES (?,?,?,?)",
                        (first["id"], "req-" + "b" * 64,
                         "offline-digest", now.isoformat()),
                    )
                    self.assertTrue(coordinator.observe_usage("A", usage, now=now))
                    self.assertIsNone(coordinator.lease_for_dispatch("A", now=now))
                    before = coordinator.recovery_ready_diagnostics(now=now)
                    row = next(x for x in before["lanes"] if x["lane"] == "A")
                    self.assertEqual(row["recoverable_now"], 1)
                    lease = coordinator.lease_for_recovery("A", now=now)
                    self.assertEqual(lease["id"], first["id"])
                    # Active lease is not eligible for another worker.
                    self.assertIsNone(coordinator.lease_for_recovery("A", now=now))
                    active = coordinator.recovery_ready_diagnostics(now=now)
                    row = next(x for x in active["lanes"] if x["lane"] == "A")
                    self.assertEqual(row["recoverable_now"], 0)
                    # A stale lease can be reconciled, never newly dispatched.
                    later = now + timedelta(minutes=16)
                    recovered = coordinator.lease_for_recovery("A", now=later)
                    self.assertEqual(recovered["id"], first["id"])
                    self.assertNotEqual(recovered["lease_id"], lease["lease_id"])
                    self.assertIsNone(coordinator.lease_for_dispatch("A", now=later))
                finally:
                    coordinator.db.close()

    def test_future_ready_at_blocks_new_dispatch_but_not_bound_recovery(self):
        """Cooldown clock gates HTTP only, even without a halted flag."""
        import tempfile
        from pathlib import Path
        from datetime import timedelta
        from three_api_coordinator import Coordinator, stamp
        from three_api_job_binding import JobRequestBindingStore

        with tempfile.TemporaryDirectory() as directory:
            coordinator = Coordinator(Path(directory) / "future-cooldown.sqlite")
            try:
                coordinator.seed()
                JobRequestBindingStore(coordinator)
                now = self.kwargs["requested_at"]
                first = coordinator.db.execute(
                    "SELECT id FROM jobs WHERE lane='A' AND page=1 "
                    "ORDER BY account_ordinal LIMIT 1"
                ).fetchone()
                coordinator.db.execute(
                    "INSERT INTO job_request_bindings "
                    "(job_id,request_id,request_digest,reserved_at) "
                    "VALUES (?,?,?,?)",
                    (first["id"], "req-" + "c" * 64, "offline-digest",
                     stamp(now)),
                )
                coordinator.db.execute(
                    "UPDATE lanes SET halted=0,ready_at=? WHERE lane='A'",
                    (stamp(now + timedelta(minutes=65)),),
                )
                self.assertIsNone(coordinator.lease_for_dispatch("A", now=now))
                diagnostics = coordinator.recovery_ready_diagnostics(now=now)
                row = next(x for x in diagnostics["lanes"] if x["lane"] == "A")
                self.assertEqual(row["recoverable_now"], 1)
                recovered = coordinator.lease_for_recovery("A", now=now)
                self.assertEqual(recovered["id"], first["id"])
            finally:
                coordinator.db.close()

    def test_unbound_quota_halt_never_claims_recovery(self):
        """An empty binding store cannot manufacture capture recovery."""
        import tempfile
        from pathlib import Path
        from three_api_coordinator import Coordinator
        from three_api_job_binding import JobRequestBindingStore

        with tempfile.TemporaryDirectory() as directory:
            coordinator = Coordinator(Path(directory) / "no-binding.sqlite")
            try:
                coordinator.seed()
                JobRequestBindingStore(coordinator)
                now = self.kwargs["requested_at"]
                coordinator.observe_usage("A", None, now=now)
                report = coordinator.recovery_ready_diagnostics(now=now)
                row = next(x for x in report["lanes"] if x["lane"] == "A")
                self.assertEqual(row["recoverable_now"], 0)
                self.assertIsNone(coordinator.lease_for_recovery("A", now=now))
                self.assertIsNone(coordinator.lease_for_dispatch("A", now=now))
            finally:
                coordinator.db.close()

    def test_quota_halt_survives_coordinator_reopen(self):
        """Crash/restart must not silently release a blocked HTTP lane."""
        import tempfile
        from pathlib import Path
        from three_api_coordinator import Coordinator
        from three_api_job_binding import JobRequestBindingStore

        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "restart-quota.sqlite"
            now = self.kwargs["requested_at"]
            first = Coordinator(path)
            try:
                first.seed()
                JobRequestBindingStore(first)
                job_id = first.db.execute(
                    "SELECT id FROM jobs WHERE lane='A' AND page=1 "
                    "ORDER BY account_ordinal LIMIT 1"
                ).fetchone()["id"]
                first.db.execute(
                    "INSERT INTO job_request_bindings "
                    "(job_id,request_id,request_digest,reserved_at) "
                    "VALUES (?,?,?,?)",
                    (job_id, "req-" + "d" * 64, "offline-digest",
                     now.isoformat()),
                )
                first.observe_usage("A", 85, now=now)
            finally:
                first.db.close()
            second = Coordinator(path)
            try:
                self.assertIsNone(second.lease_for_dispatch("A", now=now))
                self.assertEqual(
                    second.lease_for_recovery("A", now=now)["id"], job_id)
                state = second.db.execute(
                    "SELECT halted,reason FROM lanes WHERE lane='A'"
                ).fetchone()
                self.assertEqual((state["halted"], state["reason"]),
                                 (1, "usage_limit"))
            finally:
                second.db.close()

    def test_quota_cooldown_boundary_requires_explicit_reset(self):
        """Passing 65 minutes alone must not authorize fresh HTTP."""
        import tempfile
        from pathlib import Path
        from datetime import timedelta
        from three_api_coordinator import Coordinator
        from three_api_job_binding import JobRequestBindingStore

        with tempfile.TemporaryDirectory() as directory:
            coordinator = Coordinator(Path(directory) / "cooldown-boundary.sqlite")
            try:
                coordinator.seed()
                JobRequestBindingStore(coordinator)
                now = self.kwargs["requested_at"]
                self.assertTrue(coordinator.observe_usage("A", 85, now=now))
                at_boundary = now + timedelta(minutes=65)
                self.assertIsNone(
                    coordinator.lease_for_dispatch("A", now=at_boundary))
                self.assertFalse(
                    coordinator.reset_after_verified_cooldown(
                        "A", now=now + timedelta(minutes=64)))
                self.assertIsNone(
                    coordinator.lease_for_dispatch("A", now=at_boundary))
                self.assertTrue(
                    coordinator.reset_after_verified_cooldown(
                        "A", now=at_boundary))
                self.assertIsNotNone(
                    coordinator.lease_for_dispatch("A", now=at_boundary))
            finally:
                coordinator.db.close()

    def test_quota_halt_on_one_lane_preserves_other_lanes(self):
        """Offline lane quota state cannot bypass predecessor cursor evidence."""
        import tempfile
        from pathlib import Path
        from three_api_coordinator import Coordinator
        from three_api_job_binding import JobRequestBindingStore

        with tempfile.TemporaryDirectory() as directory:
            coordinator = Coordinator(Path(directory) / "lane-isolation.sqlite")
            try:
                coordinator.seed()
                JobRequestBindingStore(coordinator)
                now = self.kwargs["requested_at"]
                self.assertTrue(coordinator.observe_usage("B", None, now=now))
                self.assertIsNone(coordinator.lease_for_dispatch("B", now=now))
                a = coordinator.lease_for_dispatch("A", now=now)
                c = coordinator.lease_for_dispatch("C", now=now)
                self.assertIsNotNone(a)
                self.assertEqual(a["lane"], "A")
                # C owns page 2; no page-1 receipt/cursor exists yet.
                # Its absence is a prerequisite, not a B-quota leak.
                self.assertIsNone(c)
                diagnostic = coordinator.dispatch_ready_diagnostics(now=now)
                c_lane = next(row for row in diagnostic["lanes"]
                              if row["lane"] == "C")
                self.assertTrue(c_lane["lane_ready"])
                self.assertEqual(c_lane["eligible_jobs"], 0)
                state = coordinator.db.execute(
                    "SELECT halted,reason FROM lanes WHERE lane='B'"
                ).fetchone()
                self.assertEqual((state["halted"], state["reason"]),
                                 (1, "missing_usage"))
            finally:
                coordinator.db.close()

    def test_missing_bound_capture_fails_closed_before_http(self):
        """A reserved request with no capture must never be sent again."""
        from unittest.mock import Mock, patch
        runner = object.__new__(LiveSerialStep)
        runner.coordinator = Mock()
        runner.coordinator.path = "isolated-test.sqlite"
        runner.coordinator.lease_for_recovery.side_effect = [
            {"id": 1, "account_ordinal": 1, "lane": "A"}]
        runner.bindings = Mock()
        runner.bindings.lookup.return_value = {
            "request_id": "req-" + "e" * 64}
        runner.evidence = Mock()
        runner.executor = Mock()
        runner.executor.capture.get_capture_for_request.return_value = None
        runner.executor.transport._lanes = {
            lane: Mock(_caller_ig_id=caller)
            for lane, caller in self.kwargs["caller_ids"].items()
        }
        with patch("three_api_live_serial_step.serial_tick_lock") as lock:
            lock.return_value.__enter__.return_value = None
            with self.assertRaisesRegex(LiveSerialStepError,
                                        "Bound request has no capture"):
                runner.step(valid_official_roster(), **self.kwargs)
        runner.bindings.dispatch_once.assert_not_called()
        runner.coordinator.lease_for_dispatch.assert_not_called()
        runner.bindings.commit_captured_page.assert_not_called()

    def test_recovery_precedes_fresh_dispatch(self):
        """Durable capture reconciliation always has serial priority."""
        from unittest.mock import Mock, patch
        runner = object.__new__(LiveSerialStep)
        runner.coordinator = Mock()
        runner.coordinator.path = "isolated-test.sqlite"
        job = {"id": 1, "account_ordinal": 1, "lane": "A"}
        runner.coordinator.lease_for_recovery.side_effect = [job]
        runner.bindings = Mock()
        runner.bindings.lookup.return_value = {
            "request_id": "req-" + "f" * 64}
        runner.bindings.commit_captured_page.return_value = {"terminal": True}
        runner.evidence = Mock()
        runner.executor = Mock()
        capture = Mock()
        capture.request = Mock()
        runner.executor.capture.get_capture_for_request.return_value = capture
        runner.executor.transport._lanes = {
            lane: Mock(_caller_ig_id=caller)
            for lane, caller in self.kwargs["caller_ids"].items()
        }
        with patch("three_api_live_serial_step.serial_tick_lock") as lock:
            lock.return_value.__enter__.return_value = None
            result = runner.step(valid_official_roster(), **self.kwargs)
        self.assertEqual(result["status"], "recovered")
        self.assertEqual(result["lane"], "A")
        runner.bindings.dispatch_once.assert_not_called()
        runner.coordinator.lease_for_dispatch.assert_not_called()
        runner.bindings.commit_captured_page.assert_called_once()

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
            result = runner.step(valid_official_roster(), **self.kwargs)
        self.assertEqual(result["status"], "no_ready_work")
        runner.bindings.dispatch_once.assert_not_called()
        self.assertEqual(runner.coordinator.lease_for_recovery.call_count, 3)


    def test_bounded_driver_rejects_invalid_limits_and_approval(self):
        from unittest.mock import patch
        from three_api_live_serial_step import run_bounded_serial_steps
        with patch.object(LiveSerialStep, "step") as step:
            for count in (0, 883, True, "5"):
                with self.subTest(count=count), self.assertRaises(LiveSerialStepError):
                    run_bounded_serial_steps(self.runner, [], max_steps=count, approved=True)
            with self.assertRaisesRegex(LiveSerialStepError, "approval"):
                run_bounded_serial_steps(self.runner, [], max_steps=1, approved=False)
            step.assert_not_called()

    def test_bounded_driver_caps_calls_and_stops_on_no_work(self):
        from unittest.mock import patch
        from three_api_live_serial_step import run_bounded_serial_steps
        with patch.object(LiveSerialStep, "step", return_value={"status": "page_committed"}) as step:
            result = run_bounded_serial_steps(self.runner, [], max_steps=3, approved=True)
        self.assertEqual(step.call_count, 3)
        self.assertEqual(result["committed_pages"], 3)
        with patch.object(LiveSerialStep, "step", side_effect=[
                {"status": "recovered"}, {"status": "no_ready_work"}]) as step:
            result = run_bounded_serial_steps(self.runner, [], max_steps=5, approved=True)
        self.assertEqual(step.call_count, 2)
        self.assertEqual(result["recovered_pages"], 1)
        self.assertEqual(result["last_status"], "no_ready_work")

    def test_bounded_driver_never_retries_failed_step(self):
        from unittest.mock import patch
        from three_api_live_serial_step import run_bounded_serial_steps
        with patch.object(LiveSerialStep, "step",
                          side_effect=LiveSerialStepError("capture missing")) as step:
            with self.assertRaisesRegex(LiveSerialStepError, "capture missing"):
                run_bounded_serial_steps(self.runner, [], max_steps=10, approved=True)
        step.assert_called_once()


    def test_bounded_driver_refreshes_clock_for_each_page(self):
        from unittest.mock import patch
        from datetime import timedelta
        from three_api_live_serial_step import run_bounded_serial_steps
        start = self.kwargs["requested_at"]
        stamps = iter([start, start + timedelta(seconds=2)])
        with patch.object(LiveSerialStep, "step",
                          return_value={"status": "page_committed"}) as step:
            run_bounded_serial_steps(self.runner, [], max_steps=2,
                                     clock=lambda: next(stamps), approved=True)
        self.assertEqual(step.call_args_list[0].kwargs["requested_at"], start)
        self.assertEqual(step.call_args_list[1].kwargs["requested_at"],
                         start + timedelta(seconds=2))

    def test_bounded_driver_rejects_clock_rollback_before_second_call(self):
        from unittest.mock import patch
        from datetime import timedelta
        from three_api_live_serial_step import run_bounded_serial_steps
        start = self.kwargs["requested_at"]
        stamps = iter([start, start - timedelta(seconds=1)])
        with patch.object(LiveSerialStep, "step",
                          return_value={"status": "page_committed"}) as step:
            with self.assertRaisesRegex(LiveSerialStepError, "Monotonic"):
                run_bounded_serial_steps(self.runner, [], max_steps=2,
                                         clock=lambda: next(stamps), approved=True)
        step.assert_called_once()


    def test_offline_evidence_blocks_live_before_coordinator_access(self):
        from unittest.mock import Mock
        from three_api_evidence import PageEvidenceStore
        # Deliberately uninitialized: the gate must not read staging DB or
        # interact with a transport when a live attempt is requested.
        self.runner.evidence = object.__new__(PageEvidenceStore)
        self.runner.coordinator = Mock()
        self.runner.executor = Mock()
        with self.assertRaisesRegex(LiveSerialStepError, "Offline staging evidence"):
            self.runner.step([{} for _ in range(294)], **self.kwargs)
        self.runner.coordinator.assert_not_called()
        self.runner.executor.assert_not_called()

    def test_offline_evidence_still_requires_explicit_approval(self):
        from three_api_evidence import PageEvidenceStore
        self.runner.evidence = object.__new__(PageEvidenceStore)
        with self.assertRaisesRegex(LiveSerialStepError, "Explicit live approval"):
            self.runner.step([{} for _ in range(294)],
                             **{**self.kwargs, "approved": False})


    def test_readiness_report_rejects_offline_evidence_without_side_effects(self):
        from unittest.mock import Mock
        from three_api_evidence import PageEvidenceStore
        from three_api_live_serial_step import offline_live_readiness_report
        self.runner.evidence = object.__new__(PageEvidenceStore)
        self.runner.coordinator = Mock()
        self.runner.executor = Mock()
        report = offline_live_readiness_report(self.runner)
        self.assertFalse(report["live_ready"])
        self.assertEqual(report["meta_calls"], 0)
        self.assertEqual(report["db_writes"], 0)
        self.assertIn("offline_staging_evidence_port", report["blockers"])
        self.runner.coordinator.assert_not_called()
        self.runner.executor.assert_not_called()


if __name__ == "__main__":
    unittest.main()
