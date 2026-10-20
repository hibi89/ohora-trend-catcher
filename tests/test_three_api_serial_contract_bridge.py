"""No-network, no-RAW contract dispatch gate regression."""
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace
from unittest import TestCase, mock
import sys
import tempfile

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "raw_v2"))

from three_api_contract_executor import ThreeApiContractExecutor
from three_api_serial_contract_bridge import (
    OfflineSerialContractBridge, OfflineFixtureTransport, SerialContractBridgeError,
)
from three_api_serial_lock import serial_tick_lock


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
