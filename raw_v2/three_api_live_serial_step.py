"""Opt-in one-request serial adapter. Never constructs HTTP or reads secrets.

Caller must supply an authorized ThreeLaneMetaTransport, durable contract
executor, original coordinator, binding store and page evidence store.
No CLI, scheduler, automatic retry or operating DB connection is created.
"""
from datetime import datetime, timedelta

from three_api_coordinator import LANES, COOLDOWN_MINUTES, stamp
from three_api_job_binding import JobRequestBindingStore
from three_api_live_lane_router import ThreeLaneMetaTransport
from three_api_serial_lock import serial_tick_lock, SerialTickBusy
from three_api_contract_executor import ThreeApiContractExecutor
from three_api_capture import CaptureJournal
from three_api_raw_writer import ContractRawWriter
from three_api_evidence import PageEvidenceStore
from three_api_live_request_factory import prepare_job_request


class LiveSerialStepError(RuntimeError):
    pass


class LiveSerialStep:
    def __init__(self, coordinator, bindings, evidence, executor):
        if not isinstance(bindings, JobRequestBindingStore) or bindings.coordinator is not coordinator:
            raise LiveSerialStepError("Original binding store required")
        if not isinstance(evidence, PageEvidenceStore) or evidence.coordinator is not coordinator:
            raise LiveSerialStepError("Original evidence store required")
        if not isinstance(executor, ThreeApiContractExecutor):
            raise LiveSerialStepError("Contract executor required")
        if (type(executor.transport) is not ThreeLaneMetaTransport
                or not isinstance(executor.capture, CaptureJournal)
                or not isinstance(executor.raw, ContractRawWriter)):
            raise LiveSerialStepError("Explicit three-lane transport and durable ports required")
        self.coordinator = coordinator
        self.bindings = bindings
        self.evidence = evidence
        self.executor = executor

    def step(self, accounts, *, run_id, roster_digest, day_kst,
             requested_at, api_version, caller_ids, approved=False):
        if approved is not True:
            raise LiveSerialStepError("Explicit live approval required")
        if not isinstance(requested_at, datetime) or requested_at.utcoffset() is None:
            raise LiveSerialStepError("Timezone-aware request time required")
        if not isinstance(accounts, (list, tuple)) or len(accounts) != 294:
            raise LiveSerialStepError("Official 294-account roster required")
        if not isinstance(caller_ids, dict) or set(caller_ids) != set(LANES):
            raise LiveSerialStepError("A/B/C caller identities required")
        if any(caller_ids[lane] != self.executor.transport._lanes[lane]._caller_ig_id
               for lane in LANES):
            raise LiveSerialStepError("Caller identities do not match transport")
        try:
            with serial_tick_lock(self.coordinator.path):
                now = requested_at
                for lane in LANES:
                    job = self.coordinator.lease_for_recovery(lane, now=now)
                    if job is not None:
                        saved = self.bindings.lookup(job["id"])
                        record = (self.executor.capture.get_capture_for_request(saved["request_id"])
                                  if saved else None)
                        if record is None:
                            raise LiveSerialStepError("Bound request has no capture; no resend")
                        result = self.bindings.commit_captured_page(
                            job, record.request, self.evidence,
                            self.executor.capture, self.executor.raw, now=now)
                        return {"status": "recovered", "lane": lane,
                                "account_ordinal": job["account_ordinal"],
                                "terminal": result["terminal"]}
                for lane in LANES:
                    job = self.coordinator.lease_for_dispatch(lane, now=now)
                    if job is None:
                        continue
                    # Preflight must finish before reserving an immutable intent.
                    account = dict(accounts[job["account_ordinal"] - 1])
                    account.setdefault("ordinal", job["account_ordinal"])
                    try:
                        request = prepare_job_request(
                            job, self.evidence, account, run_id=run_id,
                            roster_digest=roster_digest, day_kst=day_kst,
                            requested_at=now, api_version=api_version,
                            caller_id=caller_ids[lane])
                    except Exception:
                        raise LiveSerialStepError("Request preflight failed") from None
                    # Hold the lane before any transport attempt. A crash or
                    # unknown HTTP outcome must not free quota automatically.
                    hold = stamp(now + timedelta(minutes=COOLDOWN_MINUTES))
                    changed = self.coordinator.db.execute(
                        "UPDATE lanes SET halted=1,reason='live_pending_usage',"
                        "ready_at=MAX(ready_at,?) WHERE lane=? AND halted=0",
                        (hold, lane)).rowcount
                    if changed != 1:
                        raise LiveSerialStepError("Lane quota gate unavailable")
                    try:
                        self.bindings.dispatch_once(
                            job, request, self.evidence, self.executor, now=now)
                        result = self.bindings.commit_captured_page(
                            job, request, self.evidence, self.executor.capture,
                            self.executor.raw, now=now)
                    finally:
                        saved = self.executor.capture.get_response_evidence(request.request_id)
                        usage = (saved.get("safe_usage", {}).get("max_usage_percent")
                                 if saved and saved.get("classification") == "safe" else None)
                        if type(usage) is int and 0 <= usage < 85:
                            self.coordinator.db.execute(
                                "UPDATE lanes SET halted=0,reason=NULL,ready_at=? "
                                "WHERE lane=? AND reason='live_pending_usage'",
                                (stamp(now), lane))
                        elif type(usage) is int and usage >= 85:
                            self.coordinator.observe_usage(lane, usage, now=now)
                    return {"status": "page_committed", "lane": lane,
                            "account_ordinal": job["account_ordinal"],
                            "terminal": result["terminal"]}
                return {"status": "no_ready_work"}
        except SerialTickBusy:
            return {"status": "serial_busy"}
