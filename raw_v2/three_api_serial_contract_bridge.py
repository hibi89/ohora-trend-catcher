"""Offline-only serialized bridge for durable three-API request bindings.

Not a Meta dispatcher. The injected executor MUST use an explicitly marked
fixture transport. No credentials, GraphHTTPClient, scheduler or operating DB.
"""
from __future__ import annotations

from datetime import datetime
from three_api_serial_lock import SerialTickBusy, serial_tick_lock


class SerialContractBridgeError(RuntimeError):
    """Safe denial without request body, token or cursor."""


class OfflineFixtureTransport:
    """Exact in-memory transport type; never delegates to a network sender."""

    offline_fixture_only = True

    def __init__(self, response):
        self.response = response
        self.calls = 0

    def send(self, request):
        self.calls += 1
        return self.response


class OfflineSerialContractBridge:
    """One bound synthetic contract attempt under a cross-process DB gate.

    Reservation precedes the fake transport attempt. Any unknown outcome stays
    bound and cannot be sent again. This bridge does not select a job, choose a
    quota lane or permit an HTTP transport.
    """

    def __init__(self, coordinator, bindings, executor):
        from three_api_contract_executor import ThreeApiContractExecutor
        from three_api_job_binding import JobRequestBindingStore
        if not isinstance(bindings, JobRequestBindingStore):
            raise TypeError("Durable job binding store required")
        if not isinstance(executor, ThreeApiContractExecutor):
            raise TypeError("Contract executor required")
        if bindings.coordinator is not coordinator:
            raise SerialContractBridgeError("Coordinator identity mismatch")
        self.coordinator = coordinator
        self.bindings = bindings
        self.executor = executor

    def dispatch_fixture_once(self, job, request, evidence, *, now):
        """Dispatch one offline fixture only, never an authorized Graph sender."""
        if not isinstance(now, datetime) or now.tzinfo is None:
            raise SerialContractBridgeError("Timezone-aware time required")
        if not isinstance(job, dict) or job.get("lane") not in ("A", "B", "C"):
            raise SerialContractBridgeError("Invalid coordinator lane")
        if getattr(request, "lane", None) != job["lane"]:
            raise SerialContractBridgeError("Request lane mismatch")
        if getattr(request, "phase", None) == "history":
            raise SerialContractBridgeError("History transport is disabled")
        transport = self.executor.transport
        if type(transport) is not OfflineFixtureTransport:
            raise SerialContractBridgeError("Only an offline fixture transport is accepted")
        try:
            with serial_tick_lock(self.coordinator.path):
                # Fail closed if fixture marker changed while awaiting gate.
                if self.executor.transport is not transport or type(transport) is not OfflineFixtureTransport:
                    raise SerialContractBridgeError("Offline fixture authorization lost")
                return self.bindings.dispatch_once(
                    job, request, evidence, self.executor, now=now)
        except SerialTickBusy:
            return {"status": "serial_busy", "selected_lane": None}

    def commit_captured_fixture_page(self, job, request, evidence, *, now):
        """Commit a previously captured fixture to RAW/page evidence under the gate.

        There is deliberately no transport attempt or retry in this path. The
        original durable capture and writer are taken from the same executor,
        not accepted from the caller. An absent capture remains blocked in the
        binding store, even if a dispatch was reserved before a process crash.
        """
        if not isinstance(now, datetime) or now.tzinfo is None:
            raise SerialContractBridgeError("Timezone-aware time required")
        if not isinstance(job, dict) or job.get("lane") not in ("A", "B", "C"):
            raise SerialContractBridgeError("Invalid coordinator lane")
        if getattr(request, "lane", None) != job["lane"]:
            raise SerialContractBridgeError("Request lane mismatch")
        if getattr(request, "phase", None) == "history":
            raise SerialContractBridgeError("History transport is disabled")
        capture = self.executor.capture
        raw = self.executor.raw
        transport = self.executor.transport
        if capture is None or raw is None:
            raise SerialContractBridgeError("Durable capture and RAW ports required")
        if type(transport) is not OfflineFixtureTransport:
            raise SerialContractBridgeError("Only an offline fixture transport is accepted")
        try:
            with serial_tick_lock(self.coordinator.path):
                if (self.executor.capture is not capture or self.executor.raw is not raw
                        or self.executor.transport is not transport
                        or type(transport) is not OfflineFixtureTransport):
                    raise SerialContractBridgeError("Offline fixture ports changed during gate")
                return self.bindings.commit_captured_page(
                    job, request, evidence, capture, raw, now=now)
        except SerialTickBusy:
            return {"status": "serial_busy", "selected_lane": None}
