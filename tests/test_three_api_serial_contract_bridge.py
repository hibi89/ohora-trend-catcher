"""No-network, no-RAW contract dispatch gate regression."""
from dataclasses import replace
from datetime import datetime, timezone, timedelta
from pathlib import Path
import sqlite3
from types import SimpleNamespace
from unittest import TestCase, mock
import sys
import tempfile

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "raw_v2"))

from three_api_contract_executor import (
    ThreeApiContractExecutor, ContractExecutionError,
)
from three_api_serial_contract_bridge import (
    OfflineSerialContractBridge, OfflineFixtureTransport, SerialContractBridgeError,
)
from three_api_serial_lock import serial_tick_lock
from three_api_coordinator import Coordinator
from three_api_evidence import PageEvidenceStore
from three_api_job_binding import JobBindingError, JobRequestBindingStore
from three_api_capture import CaptureJournal
from three_api_raw_writer import ContractRawWriter
from test_three_api_contract_executor import (
    OBSERVED_AT, SCHEMA, make_request, make_response,
)


NOW = datetime(2026, 10, 21, 12, tzinfo=timezone.utc)


class FakeBindings:
    def __init__(self, coordinator):
        self.coordinator = coordinator
        self.calls = []

    def dispatch_once(self, job, request, evidence, executor, *, now):
        self.calls.append((job, request, evidence, executor, now))
        return {"status": "fixture_dispatched", "lane": request.lane}


class SerialContractBridgeTests(TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.coordinator = SimpleNamespace(
            path=Path(self.tmp.name) / "isolated-coordinator.sqlite")
        self.bindings = FakeBindings(self.coordinator)
        self.transport = OfflineFixtureTransport(response=None)
        self.executor = ThreeApiContractExecutor(
            self.transport, None, None, clock=lambda: NOW)
        patcher = mock.patch(
            "three_api_job_binding.JobRequestBindingStore", FakeBindings)
        patcher.start()
        self.addCleanup(patcher.stop)
        self.bridge = OfflineSerialContractBridge(
            self.coordinator, self.bindings, self.executor)
        self.job = {"lane": "A"}
        self.request = SimpleNamespace(lane="A", phase="front")

    def test_one_fixture_dispatch_passes_durable_binding(self):
        result = self.bridge.dispatch_fixture_once(
            self.job, self.request, object(), now=NOW)
        self.assertEqual(result, {"status": "fixture_dispatched", "lane": "A"})
        self.assertEqual(len(self.bindings.calls), 1)

    def test_busy_gate_prevents_any_request_reservation(self):
        with serial_tick_lock(self.coordinator.path):
            result = self.bridge.dispatch_fixture_once(
                self.job, self.request, object(), now=NOW)
        self.assertEqual(result, {"status": "serial_busy", "selected_lane": None})
        self.assertEqual(self.bindings.calls, [])

    def test_non_fixture_transport_rejected_before_reservation(self):
        self.executor.transport = SimpleNamespace()
        with self.assertRaises(SerialContractBridgeError):
            self.bridge.dispatch_fixture_once(
                self.job, self.request, object(), now=NOW)
        self.assertEqual(self.bindings.calls, [])

    def test_transport_with_fetch_rejected_even_if_marked(self):
        self.executor.transport = SimpleNamespace(
            offline_fixture_only=True, fetch=lambda *args: None)
        with self.assertRaises(SerialContractBridgeError):
            self.bridge.dispatch_fixture_once(
                self.job, self.request, object(), now=NOW)
        self.assertEqual(self.bindings.calls, [])

    def test_wrong_lane_and_history_fail_closed(self):
        self.request.lane = "B"
        with self.assertRaises(SerialContractBridgeError):
            self.bridge.dispatch_fixture_once(
                self.job, self.request, object(), now=NOW)
        self.request.lane = "A"
        self.request.phase = "history"
        with self.assertRaises(SerialContractBridgeError):
            self.bridge.dispatch_fixture_once(
                self.job, self.request, object(), now=NOW)
        self.assertEqual(self.bindings.calls, [])

    def test_naive_clock_rejected(self):
        with self.assertRaises(SerialContractBridgeError):
            self.bridge.dispatch_fixture_once(
                self.job, self.request, object(), now=datetime(2026, 10, 21))
        self.assertEqual(self.bindings.calls, [])

    def test_wrong_coordinator_binding_is_rejected(self):
        with self.assertRaises(SerialContractBridgeError):
            OfflineSerialContractBridge(
                SimpleNamespace(path=self.coordinator.path),
                self.bindings, self.executor)


class RealFixtureCaptureBridgeTests(TestCase):
    """Real isolated SQLite ports; an in-memory response, never a Graph request."""

    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.root = Path(tmp.name)
        self.coordinator = Coordinator(self.root / "coordinator.sqlite")
        self.coordinator.seed()
        self.evidence = PageEvidenceStore(self.coordinator)
        self.bindings = JobRequestBindingStore(self.coordinator)
        self.job = self.coordinator.lease("A", now=OBSERVED_AT)
        self.request = make_request()
        self.capture = CaptureJournal(str(self.root / "capture.sqlite"))
        self.raw_db = sqlite3.connect(
            self.root / "synthetic_raw.sqlite", isolation_level=None)
        self.raw_db.executescript(SCHEMA)
        self.raw_db.execute("INSERT INTO accounts VALUES(?)", ("KR_001",))
        self.writer = ContractRawWriter(self.raw_db)
        self.transport = OfflineFixtureTransport(make_response(self.request))
        self.executor = ThreeApiContractExecutor(
            self.transport, self.capture, self.writer, clock=lambda: OBSERVED_AT)
        self.bridge = OfflineSerialContractBridge(
            self.coordinator, self.bindings, self.executor)
        self.addCleanup(lambda: self.raw_db.close())
        self.addCleanup(lambda: self.capture.close())
        self.addCleanup(lambda: self.coordinator.db.close())

    def raw_count(self):
        return self.raw_db.execute(
            "SELECT COUNT(*) FROM post_observations").fetchone()[0]

    def edge_count(self):
        return self.coordinator.db.execute(
            "SELECT COUNT(*) FROM page_evidence").fetchone()[0]

    def dispatch(self):
        return self.bridge.dispatch_fixture_once(
            self.job, self.request, self.evidence, now=OBSERVED_AT)

    def commit(self, *, job=None, now=OBSERVED_AT):
        return self.bridge.commit_captured_fixture_page(
            self.job if job is None else job,
            self.request, self.evidence, now=now)

    def test_real_bound_fixture_capture_raw_and_cursor_commit(self):
        receipt = self.dispatch()
        self.assertEqual(receipt.status, "committed")
        self.assertEqual(self.transport.calls, 1)
        self.assertEqual(self.raw_count(), 1)
        self.assertEqual(self.edge_count(), 0)
        edge = self.commit()
        self.assertTrue(edge["terminal"])
        self.assertEqual(self.edge_count(), 1)
        self.assertEqual(self.coordinator.db.execute(
            "SELECT state FROM jobs WHERE id=?", (self.job["id"],)
        ).fetchone()[0], "done")
        self.assertEqual(self.bindings.lookup(self.job["id"])["request_id"],
                         self.request.request_id)
        self.assertEqual(self.transport.calls, 1)
        self.assertEqual(self.raw_count(), 1)
        with self.assertRaises(JobBindingError):
            self.dispatch()

    def test_missing_capture_blocks_raw_completion_without_any_send(self):
        with self.assertRaises(JobBindingError):
            self.commit()
        self.assertEqual(self.transport.calls, 0)
        self.assertEqual(self.raw_count(), 0)
        self.assertEqual(self.edge_count(), 0)
        self.assertIsNone(self.bindings.lookup(self.job["id"]))

    def test_busy_serial_gate_blocks_page_completion_but_allows_later_replay(self):
        self.dispatch()
        with serial_tick_lock(self.coordinator.path):
            self.assertEqual(self.commit(), {
                "status": "serial_busy", "selected_lane": None})
        self.assertEqual(self.edge_count(), 0)
        self.assertEqual(self.raw_count(), 1)
        self.assertTrue(self.commit()["terminal"])
        self.assertEqual(self.edge_count(), 1)
        self.assertEqual(self.transport.calls, 1)

    def test_swapped_transport_refuses_completion_without_changing_raw(self):
        self.dispatch()
        self.executor.transport = object()
        with self.assertRaises(SerialContractBridgeError):
            self.commit()
        self.assertEqual(self.edge_count(), 0)
        self.assertEqual(self.raw_count(), 1)
        self.assertEqual(self.transport.calls, 1)
        self.executor.transport = self.transport
        self.assertTrue(self.commit()["terminal"])

    def test_sqlite_restart_replays_bound_capture_without_second_send(self):
        self.dispatch()
        self.coordinator.db.close()
        self.capture.close()
        self.raw_db.close()

        self.coordinator = Coordinator(self.root / "coordinator.sqlite")
        self.evidence = PageEvidenceStore(self.coordinator)
        self.bindings = JobRequestBindingStore(self.coordinator)
        self.capture = CaptureJournal(str(self.root / "capture.sqlite"))
        self.raw_db = sqlite3.connect(
            self.root / "synthetic_raw.sqlite", isolation_level=None)
        self.writer = ContractRawWriter(self.raw_db)
        no_send = OfflineFixtureTransport(None)
        self.executor = ThreeApiContractExecutor(
            no_send, self.capture, self.writer, clock=lambda: OBSERVED_AT)
        self.bridge = OfflineSerialContractBridge(
            self.coordinator, self.bindings, self.executor)
        future = OBSERVED_AT + timedelta(minutes=16)
        recovered_job = self.coordinator.lease_for_recovery("A", now=future)
        self.assertIsNotNone(recovered_job)
        self.assertEqual(recovered_job["id"], self.job["id"])
        self.assertTrue(self.commit(job=recovered_job, now=future)["terminal"])
        self.assertEqual(self.raw_count(), 1)
        self.assertEqual(self.edge_count(), 1)
        self.assertEqual(self.transport.calls, 1)
        self.assertEqual(no_send.calls, 0)

    def test_invalid_response_keeps_safe_evidence_and_never_resends(self):
        self.transport.response = make_response(
            self.request, account_id="17840000000000999")
        with self.assertRaises(ContractExecutionError):
            self.dispatch()
        self.assertIsNotNone(
            self.capture.get_response_evidence(self.request.request_id))
        self.assertEqual(self.transport.calls, 1)
        self.assertEqual(self.raw_count(), 0)
        self.assertEqual(self.edge_count(), 0)
        with self.assertRaises(JobBindingError):
            self.dispatch()
        self.assertEqual(self.transport.calls, 1)

    def test_raw_ack_interruption_recovers_without_another_fixture_send(self):
        with mock.patch.object(
                self.capture, "confirm_processed",
                side_effect=OSError("synthetic ack interruption")):
            with self.assertRaises(OSError):
                self.dispatch()
        self.assertEqual(self.raw_count(), 1)
        self.assertEqual(self.edge_count(), 0)
        self.assertEqual(self.transport.calls, 1)
        self.assertTrue(self.commit()["terminal"])
        self.assertEqual(self.raw_count(), 1)
        self.assertEqual(self.edge_count(), 1)
        self.assertEqual(self.transport.calls, 1)

    def test_capture_cursor_unlocks_real_second_page_job(self):
        base = make_response(self.request)
        payload = dict(base.payload)
        business = dict(payload["business_discovery"])
        media = dict(business["media"])
        media["paging"] = {
            "next": "https://graph.facebook.com/next",
            "cursors": {"after": "fixture-cursor-2"},
        }
        business["media"] = media
        payload["business_discovery"] = business
        self.transport.response = replace(
            base, payload=payload, next_cursor="fixture-cursor-2")
        self.dispatch()
        edge = self.commit()
        self.assertFalse(edge["terminal"])
        self.assertEqual(edge["next_cursor"], "fixture-cursor-2")
        self.assertEqual(
            self.evidence.required_cursor(1, 2), "fixture-cursor-2")
        next_job = self.coordinator.lease("C", now=OBSERVED_AT)
        self.assertEqual(
            (next_job["account_ordinal"], next_job["page"], next_job["lane"]),
            (1, 2, "C"))
        self.assertEqual(self.transport.calls, 1)

