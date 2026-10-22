"""Opt-in one-request serial adapter. Never constructs HTTP or reads secrets.

Caller must supply an authorized ThreeLaneMetaTransport, durable contract
executor, original coordinator, binding store and page evidence store.
No CLI, scheduler, automatic retry or operating DB connection is created.
"""
from datetime import datetime, timedelta
import re

from three_api_coordinator import LANES, COOLDOWN_MINUTES, stamp
from three_api_job_binding import JobRequestBindingStore
from three_api_live_lane_router import ThreeLaneMetaTransport
from three_api_serial_lock import serial_tick_lock, SerialTickBusy
from three_api_contract_executor import ThreeApiContractExecutor, ContractExecutionError
from three_api_capture import CaptureJournal
from three_api_raw_writer import ContractRawWriter
from three_api_evidence import PageEvidenceStore
from three_api_verified_page_evidence import VerifiedRawPageEvidenceStore
from three_api_live_request_factory import prepare_job_request
from three_api_roster_preflight import validate_official_roster, RosterPreflightError


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
        # The only non-staging evidence type must verify against the EXACT
        # Capture and RAW ports used by this executor, not a different store.
        if type(evidence) is VerifiedRawPageEvidenceStore and (
                evidence.capture is not executor.capture
                or evidence.raw is not executor.raw):
            raise LiveSerialStepError("Evidence and executor durable ports differ")
        if type(evidence) not in (PageEvidenceStore, VerifiedRawPageEvidenceStore):
            raise LiveSerialStepError("Unrecognized evidence port")
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
        # Explicitly isolated P50 mode; all other coordinators remain 294-only.
        from three_api_p50_coordinator import P50Coordinator
        from three_api_p50_roster import validate_p50_roster, PilotRosterError
        is_p50 = type(getattr(self, "coordinator", None)) is P50Coordinator
        expected_count = 50 if is_p50 else 294
        if not isinstance(accounts, (list, tuple)) or len(accounts) != expected_count:
            raise LiveSerialStepError(
                "P50 50-account roster required" if is_p50
                else "Official 294-account roster required")
        # Reject missing A/B/C callers and forbidden offline evidence before
        # touching an injected transport or coordinator, even with bad rows.
        if not isinstance(caller_ids, dict) or set(caller_ids) != set(LANES):
            raise LiveSerialStepError("A/B/C caller identities required")
        if type(getattr(self, "evidence", None)) is PageEvidenceStore:
            raise LiveSerialStepError("Offline staging evidence cannot authorize live Meta collection")
        try:
            if is_p50:
                validate_p50_roster(accounts)
            else:
                validate_official_roster(accounts)
        except (RosterPreflightError, PilotRosterError) as exc:
            raise LiveSerialStepError(str(exc)) from None
        if any(caller_ids[lane] != self.executor.transport._lanes[lane]._caller_ig_id
               for lane in LANES):
            raise LiveSerialStepError("Caller identities do not match transport")
        try:
            with serial_tick_lock(self.coordinator.path):
                now = requested_at
                # Read-only scan of immutable bindings: terminal failed requests
                # must not be re-leased or starve other recoverable captures.
                excluded = []
                # This is a mandatory real SQLite read. Tests must explicitly
                # model rows, not rely on Mock.fetchall() being iterable.
                # If the scan fails, abort before any recovery lease or HTTP.
                for binding in self.coordinator.db.execute(
                        "SELECT job_id,request_id FROM job_request_bindings").fetchall():
                    if (self.executor.capture.get_intent_outcome(binding["request_id"])
                            == "failed_no_retry"):
                        excluded.append(binding["job_id"])
                for lane in LANES:
                    job = (self.coordinator.lease_for_recovery(
                        lane, now=now, exclude_job_ids=excluded)
                        if excluded else self.coordinator.lease_for_recovery(lane, now=now))
                    if job is not None:
                        saved = self.bindings.lookup(job["id"])
                        record = (self.executor.capture.get_capture_for_request(saved["request_id"])
                                  if saved else None)
                        if record is None:
                            # A known failed HTTP response is terminal for THIS
                            # immutable request. Do not resend, recover, or erase it.
                            # Continue checking other lanes; never unhalt quota.
                            outcome = (self.executor.capture.get_intent_outcome(saved["request_id"])
                                       if saved else None)
                            if outcome == "failed_no_retry":
                                continue
                            raise LiveSerialStepError("Bound request has no capture; no resend")
                        result = self.bindings.commit_captured_page(
                            job, record.request, self.evidence,
                            self.executor.capture, self.executor.raw, now=now)
                        return {"status": "recovered", "lane": lane,
                                "account_ordinal": job["account_ordinal"],
                                "terminal": result["terminal"]}
                # Rotate only among eligible lanes. A-first dispatch previously
                # starved B (and eventually C), despite valid frozen jobs.
                # Recovery above still takes precedence; the original lease
                # remains the final quota/cursor/binding authorization gate.
                if is_p50:
                    dynamic_job = self.coordinator.lease_dynamic_p50(now=now)
                    dispatch_candidates = ((dynamic_job["lane"], dynamic_job),) if dynamic_job else ()
                else:
                    dispatch_candidates = ((lane, self.coordinator.lease_for_dispatch(lane, now=now))
                                           for lane in self.coordinator.dispatch_lane_order(now=now))
                for lane, job in dispatch_candidates:
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
                        self.coordinator.db.execute(
                            "UPDATE jobs SET state='pending',lease_id=NULL,leased_until=NULL "
                            "WHERE id=? AND lease_id=? AND state='leased' "
                            "AND NOT EXISTS (SELECT 1 FROM job_request_bindings "
                            "WHERE job_id=?)",
                            (job["id"], job["lease_id"], job["id"]))
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
                    page_committed = False
                    account_failed = False
                    account_error_usage = None
                    error_response_usage = None
                    try:
                        try:
                            self.bindings.dispatch_once(
                                job, request, self.evidence, self.executor, now=now)
                        except ContractExecutionError:
                            # Only Meta's exact missing-user code/subcode may be
                            # treated as a terminal account failure. This is a
                            # durable, already-bound failed intent, NOT a retry.
                            binding = self.bindings.lookup(job["id"])
                            failure = self.executor.capture.get_failure(request.request_id)
                            if failure is not None:
                                usage_match = re.search(
                                    r"\busage_pct=(\d{1,3})\b", failure.safe_message)
                                if usage_match is not None:
                                    candidate_usage = int(usage_match.group(1))
                                    if 0 <= candidate_usage <= 100:
                                        error_response_usage = candidate_usage
                            if (binding is None
                                    or binding["request_id"] != request.request_id
                                    or self.executor.capture.get_intent_outcome(
                                        request.request_id) != "failed_no_retry"
                                    or failure is None
                                    or failure.category != "http_error"
                                    or failure.scope != "account"
                                    or failure.http_status != 400
                                    or failure.outcome != "failed"):
                                raise
                            match = re.fullmatch(
                                r"Target account unavailable code=110 subcode=2207013"
                                r"(?: usage_pct=(\d{1,3}))?",
                                failure.safe_message,
                            )
                            if match is None:
                                raise
                            if match.group(1) is not None:
                                account_error_usage = int(match.group(1))
                                if not 0 <= account_error_usage <= 100:
                                    raise
                            account_failed = True
                        if not account_failed:
                            result = self.bindings.commit_captured_page(
                                job, request, self.evidence, self.executor.capture,
                                self.executor.raw, now=now)
                            page_committed = True
                    finally:
                        saved = self.executor.capture.get_response_evidence(request.request_id)
                        usage = (saved.get("safe_usage", {}).get("max_usage_percent")
                                 if saved and saved.get("classification") == "safe" else None)
                        if (page_committed and type(usage) is int and 0 <= usage < 85
                                and self.bindings.lookup(job["id"]) is not None
                                and self.executor.capture.get_capture_for_request(request.request_id) is not None):
                            self.coordinator.db.execute(
                                "UPDATE lanes SET halted=0,reason=NULL,ready_at=? "
                                "WHERE lane=? AND reason='live_pending_usage'",
                                (stamp(now), lane))
                        elif (account_failed and type(account_error_usage) is int
                              and account_error_usage < 85):
                            # Evidence came from the strict Meta usage-header
                            # parser, not the account's HTTP 400 status alone.
                            self.coordinator.db.execute(
                                "UPDATE lanes SET halted=0,reason=NULL,ready_at=? "
                                "WHERE lane=? AND reason='live_pending_usage'",
                                (stamp(now), lane))
                        elif account_failed and type(account_error_usage) is int and account_error_usage >= 85:
                            self.coordinator.observe_usage(lane, account_error_usage, now=now)
                        elif type(error_response_usage) is int and error_response_usage >= 85:
                            # Preserve a global quota stop on any received error
                            # response; sub-threshold unknown errors remain held.
                            self.coordinator.observe_usage(lane, error_response_usage, now=now)
                        elif type(usage) is int and usage >= 85:
                            self.coordinator.observe_usage(lane, usage, now=now)
                        # Missing quota evidence, unknown outcomes and failures
                        # other than this exact account error remain held.
                    if account_failed:
                        return {"status": "account_failed", "lane": lane,
                                "account_ordinal": job["account_ordinal"],
                                "terminal": True}
                    return {"status": "page_committed", "lane": lane,
                            "account_ordinal": job["account_ordinal"],
                            "terminal": result["terminal"],
                            "verified_response_received_at": (
                                saved.get("received_at") if saved
                                and saved.get("classification") == "safe"
                                and page_committed else None)}
                return {"status": "no_ready_work"}
        except SerialTickBusy:
            return {"status": "serial_busy"}


def run_bounded_serial_steps(runner, accounts, *, max_steps, clock=None, **step_kwargs):
    """Opt-in bounded driver; no scheduler, sleeps, retries, or implicit approval.

    Stops on a busy lock, quota/no-ready-work, or the first exception.
    Each iteration calls the original fail-closed one-page step, preserving
    its durable reservation and serial lock. No HTTP is possible without
    the original explicit approval and supplied transport.
    """
    if not isinstance(runner, LiveSerialStep):
        raise LiveSerialStepError("Original live serial runner required")
    if type(max_steps) is not int or not 1 <= max_steps <= 294 * 3:
        raise LiveSerialStepError("Bounded step count required (1..882)")
    if step_kwargs.get("approved") is not True:
        raise LiveSerialStepError("Explicit live approval required")
    # Each page must receive a fresh wall-clock timestamp. Reusing a stale
    # requested_at across pages can corrupt cooldown and evidence chronology.
    from datetime import timezone
    if clock is None:
        clock = lambda: datetime.now(timezone.utc)
    if not callable(clock):
        raise LiveSerialStepError("Callable UTC clock required")
    results = []
    previous_time = None
    for _ in range(max_steps):
        current_time = clock()
        if (not isinstance(current_time, datetime)
                or current_time.utcoffset() is None
                or (previous_time is not None and current_time < previous_time)):
            raise LiveSerialStepError("Monotonic timezone-aware clock required")
        previous_time = current_time
        result = runner.step(accounts, **{**step_kwargs, "requested_at": current_time})
        status = result.get("status")
        if status not in ("page_committed", "account_failed", "recovered",
                          "no_ready_work", "serial_busy"):
            raise LiveSerialStepError("Unknown serial step outcome")
        results.append(result)
        if status in ("no_ready_work", "serial_busy"):
            break
    return {"attempted_steps": len(results),
            "committed_pages": sum(r["status"] == "page_committed"
                                   for r in results),
            "recovered_pages": sum(r["status"] == "recovered"
                                   for r in results),
            "account_failures": sum(r["status"] == "account_failed"
                                    for r in results),
            "last_status": results[-1]["status"],
            "results": results}


def offline_live_readiness_report(runner):
    """Read-only diagnostic; never touches HTTP, tokens, coordinator or DB.

    Distinguishes working offline fixture machinery from permission to run
    the experimental live adapter. Never confers live authorization.
    """
    if not isinstance(runner, LiveSerialStep):
        raise LiveSerialStepError("Original live serial runner required")
    evidence = getattr(runner, "evidence", None)
    blockers = []
    if type(evidence) is PageEvidenceStore:
        blockers.append("offline_staging_evidence_port")
    elif type(evidence) is VerifiedRawPageEvidenceStore:
        blockers.append("external_meta_pilot_not_authorized_or_verified")
    else:
        blockers.append("production_evidence_port_not_verified")
    blockers.append("live_runtime_and_raw_integration_not_accepted")
    return {"live_ready": False, "meta_calls": 0, "db_writes": 0,
            "blockers": blockers}
