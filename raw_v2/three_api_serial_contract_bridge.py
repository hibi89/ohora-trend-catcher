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

    def run_fixture_page_once(self, job, request, evidence, *, now):
        """One offline-only entrypoint: first dispatch OR capture-only recovery.

        Hold ONE cross-process gate across durable reservation, the exact
        in-memory fixture send, Capture/RAW receipt, and page/cursor commit.
        An existing binding can ONLY resume from a previously durable capture:
        never re-send a request after an interrupted or uncertain attempt.
        Job choice and quota rotation are intentionally outside this method.
        """
        from three_api_capture import CaptureJournal
        from three_api_evidence import PageEvidenceStore
        from three_api_raw_writer import ContractRawWriter

        if not isinstance(now, datetime) or now.utcoffset() is None:
            raise SerialContractBridgeError("Timezone-aware time required")
        if (not isinstance(job, dict) or type(job.get("id")) is not int
                or job.get("lane") not in ("A", "B", "C")):
            raise SerialContractBridgeError("Invalid coordinator job")
        if (getattr(request, "lane", None) != job["lane"]
                or getattr(request, "phase", None) not in ("front", "catchup")):
            raise SerialContractBridgeError("Invalid request lane or phase")
        if (not isinstance(evidence, PageEvidenceStore)
                or evidence.coordinator is not self.coordinator):
            raise SerialContractBridgeError("Original page evidence store required")

        transport = self.executor.transport
        capture = self.executor.capture
        raw = self.executor.raw
        if (type(transport) is not OfflineFixtureTransport
                or not isinstance(capture, CaptureJournal)
                or not isinstance(raw, ContractRawWriter)):
            raise SerialContractBridgeError("Isolated fixture transport and durable ports required")

        try:
            with serial_tick_lock(self.coordinator.path):
                if (self.executor.transport is not transport
                        or self.executor.capture is not capture
                        or self.executor.raw is not raw
                        or type(self.executor.transport) is not OfflineFixtureTransport):
                    raise SerialContractBridgeError("Offline fixture ports changed during gate")
                # Read only once under the gate. A reserved request is NEVER
                # dispatched again, including after crash or lease renewal.
                if self.bindings.lookup(job["id"]) is None:
                    self.bindings.dispatch_once(
                        job, request, evidence, self.executor, now=now)
                return self.bindings.commit_captured_page(
                    job, request, evidence, capture, raw, now=now)
        except SerialTickBusy:
            return {"status": "serial_busy", "selected_lane": None}

    def step_fixture_serial(self, fixtures, evidence, *, now,
                            lane_order=("A", "B", "C")):
        """Select at most one coordinator job and finish it under ONE OS gate.

        The caller supplies only inert (RequestEnvelope, ResponseEnvelope)
        fixture pairs, keyed by (lane, account ordinal, page). No callbacks,
        tokens, HTTP clients, autonomous timers, or operating database paths.
        Bound work is recovery-only and takes precedence over new requests.
        """
        from three_api_adapter_guard import validate_job_request
        from three_api_capture import CaptureJournal
        from three_api_evidence import PageEvidenceStore
        from three_api_raw_writer import ContractRawWriter
        from three_api_runtime_contract import RequestEnvelope, ResponseEnvelope
        from three_api_coordinator import LANES

        if not isinstance(now, datetime) or now.utcoffset() is None:
            raise SerialContractBridgeError("Timezone-aware time required")
        if (type(lane_order) not in (tuple, list)
                or len(lane_order) != len(LANES)
                or any(type(x) is not str for x in lane_order)
                or set(lane_order) != set(LANES)):
            raise SerialContractBridgeError("Exactly three unique lanes required")
        if type(fixtures) is not dict:
            raise SerialContractBridgeError("Only explicit fixture dictionaries accepted")
        if (not isinstance(evidence, PageEvidenceStore)
                or evidence.coordinator is not self.coordinator):
            raise SerialContractBridgeError("Original coordinator evidence required")
        transport = self.executor.transport
        capture = self.executor.capture
        raw = self.executor.raw
        if (type(transport) is not OfflineFixtureTransport
                or not isinstance(capture, CaptureJournal)
                or not isinstance(raw, ContractRawWriter)):
            raise SerialContractBridgeError("Offline durable fixture ports required")
        try:
            with serial_tick_lock(self.coordinator.path):
                if (self.executor.transport is not transport
                        or self.executor.capture is not capture
                        or self.executor.raw is not raw
                        or type(self.executor.transport) is not OfflineFixtureTransport):
                    raise SerialContractBridgeError("Offline fixture ports changed during gate")
                db = self.coordinator.db
                db.execute("""CREATE TABLE IF NOT EXISTS offline_contract_serial_turn (
                    singleton INTEGER PRIMARY KEY CHECK(singleton=1),
                    last_lane TEXT NOT NULL CHECK(last_lane IN ('A','B','C'))
                )""")
                row = db.execute(
                    "SELECT last_lane FROM offline_contract_serial_turn WHERE singleton=1"
                ).fetchone()
                order = tuple(lane_order)
                if row is not None:
                    pivot = order.index(row["last_lane"]) + 1
                    order = order[pivot:] + order[:pivot]

                def finish(lane, job, request, *, recovery):
                    # An existing binding MUST have its ORIGINAL immutable
                    # captured request; never infer permission to re-send.
                    if not recovery:
                        self.bindings.dispatch_once(
                            job, request, evidence, self.executor, now=now)
                    edge = self.bindings.commit_captured_page(
                        job, request, evidence, capture, raw, now=now)
                    db.execute(
                        """INSERT INTO offline_contract_serial_turn(singleton,last_lane)
                           VALUES (1,?)
                           ON CONFLICT(singleton) DO UPDATE SET last_lane=excluded.last_lane""",
                        (lane,))
                    return {"status": "page_committed", "selected_lane": lane,
                            "account_ordinal": job["account_ordinal"],
                            "page": job["page"], "terminal": edge["terminal"],
                            "next_cursor_present": edge["next_cursor"] is not None,
                            "recovery_only": recovery}

                def apply_durable_usage(lane, request):
                    # Missing/malformed response evidence fails closed with a
                    # persisted >=65 min gate, NOT an inferred healthy quota.
                    saved = capture.get_response_evidence(request.request_id)
                    usage = None if saved is None else saved.get("safe_usage", {}).get(
                        "max_usage_percent")
                    if type(usage) is not int or not 0 <= usage <= 100:
                        usage = None
                    self.coordinator.observe_usage(lane, usage, now=now)

                for lane in order:
                    # Lease predicates enforce halted/cooldown and predecessor
                    # page proof; do not implement a competing job selector.
                    bound = self.coordinator.lease_for_recovery(lane, now=now)
                    if bound is not None:
                        saved = self.bindings.lookup(bound["id"])
                        record = (capture.get_capture_for_request(saved["request_id"])
                                  if saved is not None else None)
                        if record is None:
                            raise SerialContractBridgeError(
                                "Bound request requires manual reconciliation; no resend")
                        request = record.request
                        try:
                            return finish(lane, bound, request, recovery=True)
                        finally:
                            apply_durable_usage(lane, request)

                    job = self.coordinator.lease_for_dispatch(lane, now=now)
                    if job is None:
                        continue
                    key = (lane, job["account_ordinal"], job["page"])
                    pair = fixtures.get(key)
                    # No reservation or transport for an absent/invalid fixture.
                    # Return the unused lease to pending so the operator may
                    # safely repair fixture inputs immediately.
                    try:
                        if (type(pair) not in (tuple, list) or len(pair) != 2
                                or not isinstance(pair[0], RequestEnvelope)
                                or not isinstance(pair[1], ResponseEnvelope)):
                            raise SerialContractBridgeError("Fixture pair unavailable")
                        request, response = pair
                        validate_job_request(job, request, evidence)
                        if response.request_id != request.request_id:
                            raise SerialContractBridgeError("Fixture response identity mismatch")
                    except Exception:
                        db.execute("BEGIN IMMEDIATE")
                        try:
                            changed = db.execute(
                                """UPDATE jobs SET state='pending',lease_id=NULL,
                                      leased_until=NULL
                                   WHERE id=? AND lease_id=? AND state='leased'
                                     AND NOT EXISTS (
                                         SELECT 1 FROM job_request_bindings b
                                         WHERE b.job_id=jobs.id)""",
                                (job["id"], job["lease_id"])).rowcount
                            if changed != 1:
                                raise SerialContractBridgeError(
                                    "Unable to release unused fixture lease")
                            db.execute("COMMIT")
                        except BaseException:
                            db.execute("ROLLBACK")
                            raise
                        raise
                    transport.response = response
                    try:
                        return finish(lane, job, request, recovery=False)
                    finally:
                        # Even if schema verification, capture persistence,
                        # RAW, or cursor completion fails, usage evidence (or
                        # missing evidence) MUST be reflected before next tick.
                        if self.bindings.lookup(job["id"]) is not None:
                            apply_durable_usage(lane, request)
                return {"status": "no_ready_work", "selected_lane": None}
        except SerialTickBusy:
            return {"status": "serial_busy", "selected_lane": None}
