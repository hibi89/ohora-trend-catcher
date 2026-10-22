"""P50 explicit manual history continuation using existing Meta/Capture/RAW ports.

The operator is the only live trigger. History requests are bound in an
independent SQLite file BEFORE the one permitted HTTP attempt; a crash cannot
silently turn an ambiguous request into an automatic repeat.
"""
from __future__ import annotations

from contextlib import ExitStack
from datetime import date, datetime, timedelta, timezone
import re
import sqlite3
from pathlib import Path

from collector import connect
from three_api_capture import CaptureJournal
from three_api_contract_executor import ThreeApiContractExecutor, request_envelope_id
from three_api_coordinator import COOLDOWN_MINUTES, stamp
from three_api_manual_pilot import _credentials, _transports, VERSION
from three_api_p50_coordinator import P50Coordinator
from three_api_p50_history_handoff import handoff_completed_latest_accounts
from three_api_p50_history_plan import plan_next_history_page
from three_api_p50_history_store import P50HistoryStore
from three_api_p50_roster import load_p50_roster
from three_api_raw_writer import ContractRawWriter
from three_api_request_spec import build_request_spec
from three_api_runtime_contract import (
    CONTRACT_VERSION, RequestEnvelope, commit_raw_and_confirm,
    digest_sha256, validate_raw_receipt,
)
from three_api_serial_lock import serial_tick_lock

KST = timezone(timedelta(hours=9))
LANES = ("A", "B", "C")


def _setup_bindings(store):
    store.db.execute("""CREATE TABLE IF NOT EXISTS history_requests(
        account_key TEXT NOT NULL REFERENCES history_accounts(account_key),
        page INTEGER NOT NULL,
        request_id TEXT NOT NULL UNIQUE,
        cursor TEXT NOT NULL,
        lane TEXT NOT NULL CHECK(lane IN ('A','B','C')),
        status TEXT NOT NULL CHECK(status IN ('pending','committed','blocked')),
        PRIMARY KEY(account_key,page))""")


def _dated_posts(record):
    timestamps = []
    for post in record.normalized_posts:
        raw = post.get("timestamp")
        if not isinstance(raw, str):
            raise ValueError("History post timestamp missing")
        try:
            dt = datetime.fromisoformat(raw.replace("Z", "+00:00"))
        except ValueError as exc:
            raise ValueError("Invalid history timestamp") from exc
        if dt.utcoffset() is None:
            raise ValueError("History timestamps require timezone")
        timestamps.append(dt.astimezone(timezone.utc))
    if not timestamps and record.cursor_state.next_cursor is not None:
        raise ValueError("Empty nonterminal page requires manual evidence review")
    return min(timestamps) if timestamps else None


def _recover(store, journal, raw, account, pending):
    """One pending intent may be recovered from Capture only; never resend."""
    key = account["key"]
    row = store.read(key)
    found = store.db.execute(
        "SELECT 1 FROM history_receipts WHERE capture_id=? AND account_key=?",
        ("cap-" + pending["request_id"], key)).fetchone()
    if found:
        store.db.execute(
            "UPDATE history_requests SET status='committed' WHERE request_id=?",
            (pending["request_id"],))
        return {"status": "already_committed", "account_key": key}
    if row["state"] != "continuation_ready" or row["cursor"] != pending["cursor"]:
        raise ValueError("History pending request/checkpoint mismatch")
    record = journal.get_capture_for_request(pending["request_id"])
    if record is None:
        store.db.execute(
            "UPDATE history_accounts SET state='blocked_unknown_http' WHERE account_key=?",
            (key,))
        store.db.execute(
            "UPDATE history_requests SET status='blocked' WHERE request_id=?",
            (pending["request_id"],))
        return {"status": "blocked_unknown_http", "account_key": key}
    if (record.request.phase != "history"
            or record.request.history_live_approved is not True
            or record.request.account_key != key
            or record.request.account_ordinal != account["ordinal"]
            or record.request.cursor != pending["cursor"]
            or record.request.lane != pending["lane"]
            or record.request.page != pending["page"]):
        raise ValueError("History Capture does not match persistent request binding")
    # A full four-post page with no next cursor is ambiguous, not proof
    # that the target account has ended. Preserve Capture/RAW for review.
    if record.cursor_state.next_cursor is None and len(record.normalized_posts) == 4:
        commit_raw_and_confirm(journal, raw, record)
        store.db.execute(
            "UPDATE history_accounts SET state='blocked_missing_cursor' WHERE account_key=?",
            (key,))
        store.db.execute(
            "UPDATE history_requests SET status='blocked' WHERE request_id=?",
            (pending["request_id"],))
        return {"status": "blocked_missing_cursor", "account_key": key}
    # Idempotent RAW recovery. Does not access transport.
    receipt = commit_raw_and_confirm(journal, raw, record)
    persisted = raw.get_raw_receipt(record.capture_id)
    validate_raw_receipt(record, persisted)
    decision = store.commit_verified_page(
        account_key=key, expected_cursor=pending["cursor"],
        next_cursor=record.cursor_state.next_cursor,
        oldest_post_at=_dated_posts(record),
        terminal=record.cursor_state.next_cursor is None,
        capture_id=record.capture_id, raw_receipt_id=receipt.raw_receipt_id,
        raw_receipt_verified=True, capture_verified=True)
    store.db.execute(
        "UPDATE history_requests SET status='committed' WHERE request_id=?",
        (pending["request_id"],))
    return {"status": "recovered", "account_key": key, "state": decision["state"]}


def _release_verified_history_hold(store, coordinator, journal, raw, lane, reason, now):
    """Resume a completed history request without waiting or replaying HTTP.

    A missing/unknown response, uncommitted checkpoint or unverified RAW
    receipt NEVER opens the lane.
    """
    if not isinstance(reason, str) or not reason.startswith("history_pending:"):
        return False
    request_id = reason[len("history_pending:"):]
    binding = store.db.execute(
        "SELECT status FROM history_requests WHERE request_id=? AND lane=?",
        (request_id, lane)).fetchone()
    if binding is None or binding["status"] != "committed":
        return False
    record = journal.get_capture_for_request(request_id)
    if record is None or record.request.lane != lane:
        return False
    receipt = raw.get_raw_receipt(record.capture_id)
    if receipt is None:
        return False
    validate_raw_receipt(record, receipt)
    if store.db.execute(
            "SELECT 1 FROM history_receipts WHERE capture_id=?",
            (record.capture_id,)).fetchone() is None:
        return False
    evidence = journal.get_response_evidence(request_id)
    usage = (evidence.get("safe_usage", {}).get("max_usage_percent")
             if evidence and evidence.get("classification") == "safe" else None)
    if type(usage) is int and 0 <= usage < 85:
        coordinator.db.execute(
            "UPDATE lanes SET halted=0,reason=NULL,ready_at=? "
            "WHERE lane=? AND reason=?",
            (stamp(now), lane, reason))
        return True
    if type(usage) is int and usage >= 85:
        coordinator.observe_usage(lane, usage, now=now)
    return False


def _ready_lane(coordinator, lane, now):
    item = coordinator.db.execute(
        "SELECT halted,reason,ready_at FROM lanes WHERE lane=?", (lane,)).fetchone()
    if item is None:
        raise ValueError("Missing lane safety state")
    return item["halted"] == 0 and item["ready_at"] <= stamp(now)


def _reserve(store, account, row, lane, request_id, page):
    store.db.execute("BEGIN IMMEDIATE")
    try:
        current = store.read(account["key"])
        if (current["state"] != "continuation_ready" or
                current["cursor"] != row["cursor"] or
                current["pages_verified"] != row["pages_verified"]):
            raise ValueError("History account changed before dispatch")
        store.db.execute(
            "INSERT INTO history_requests VALUES(?,?,?,?,?,'pending')",
            (account["key"], page, request_id, row["cursor"], lane))
        store.db.execute("COMMIT")
    except BaseException:
        store.db.execute("ROLLBACK")
        raise


def history_step(store, coordinator, journal, raw, transport, accounts,
                 roster_digest, caller_ids, api_version, *, now):
    """One bounded step: recover first, otherwise send at most ONE history page."""
    _setup_bindings(store)
    by_key = {a["key"]: a for a in accounts}
    for pending in store.db.execute(
            "SELECT * FROM history_requests WHERE status='pending' ORDER BY page,account_key").fetchall():
        return _recover(store, journal, raw, by_key[pending["account_key"]], pending)
    # Completed history requests may have crashed after checkpoint commit but
    # before clearing their conservative lane hold. Reconcile from BOTH durable
    # receipts and below-85 quota evidence, never from elapsed time alone.
    for lane_state in coordinator.db.execute(
            "SELECT lane,halted,reason FROM lanes").fetchall():
        if lane_state["halted"] and isinstance(lane_state["reason"], str):
            _release_verified_history_hold(
                store, coordinator, journal, raw,
                lane_state["lane"], lane_state["reason"], now)
    # If any lane remains held, the A/B/C shared-quota assumption is unknown:
    # no alternate-lane bypass or automatic reset after 65 minutes.
    for row in coordinator.db.execute("SELECT lane,halted FROM lanes").fetchall():
        if row["halted"]:
            return {"status": "quota_hold", "lane": row["lane"]}
    plan = plan_next_history_page(
        store, [a["key"] for a in accounts],
        latest_complete_keys=[a["key"] for a in accounts
                              if store.read(a["key"])["state"] != "ready"],
        max_pages=50)
    for job in plan["jobs"]:
        account = by_key[job["account_key"]]
        row = store.read(account["key"])
        ordinal, page = account["ordinal"], row["pages_verified"] + 4
        lane = LANES[(ordinal + row["pages_verified"] - 1) % 3]
        if not _ready_lane(coordinator, lane, now):
            continue
        spec = build_request_spec(
            lane, account["username"], after=row["cursor"], limit=4,
            include_carousel_children=True, include_account_metrics=True)
        spec_digest = digest_sha256({
            "api_version": api_version,
            "target_ig_user_id": caller_ids[lane], "spec": spec})
        run_id = "p50-history-" + row["cutoff_day"].replace("-", "")
        request_id = request_envelope_id(
            run_id=run_id, roster_digest=roster_digest, account_key=account["key"],
            lane=lane, phase="history", page=page, cursor=row["cursor"],
            request_spec_digest=spec_digest)
        request = RequestEnvelope(
            CONTRACT_VERSION, request_id, run_id, roster_digest, now.astimezone(KST).date(),
            ordinal, account["key"], account["username"], lane, "history",
            page, row["cursor"], now, spec, spec_digest,
            api_version=api_version, target_ig_user_id=caller_ids[lane],
            history_live_approved=True)
        _reserve(store, account, row, lane, request_id, page)
        hold_reason = "history_pending:" + request_id
        changed = coordinator.db.execute(
            "UPDATE lanes SET halted=1,reason=?,ready_at=MAX(ready_at,?) "
            "WHERE lane=? AND halted=0",
            (hold_reason, stamp(now + timedelta(minutes=COOLDOWN_MINUTES)), lane)).rowcount
        if changed != 1:
            raise ValueError("History lane safety hold could not be reserved")
        executor = ThreeApiContractExecutor(transport, journal, raw)
        try:
            executor.execute(request)
            pending = store.db.execute(
                "SELECT * FROM history_requests WHERE request_id=?", (request_id,)).fetchone()
            outcome = _recover(store, journal, raw, account, pending)
        except Exception:
            # Crash between Capture and RAW is still recoverable next launch.
            # A failed account may be isolated ONLY with verified below-85%
            # usage evidence. Unknown HTTP/RAW/storage failures stop globally.
            if journal.get_capture_for_request(request_id) is None:
                failure = journal.get_failure(request_id)
                evidence = journal.get_response_evidence(request_id)
                usage = (evidence.get("safe_usage", {}).get("max_usage_percent")
                         if evidence and evidence.get("classification") == "safe" else None)
                if failure is not None and type(usage) is not int:
                    match = re.search(r"\busage_pct=(\d{1,3})\b", failure.safe_message)
                    if match is not None:
                        usage = int(match.group(1))
                isolated = (failure is not None and failure.scope == "account"
                            and failure.category in (
                                "http_error", "cursor_invalid",
                                "permission", "invalid_response")
                            and type(usage) is int and 0 <= usage < 85)
                store.db.execute(
                    "UPDATE history_accounts SET state=? WHERE account_key=?",
                    ("blocked_unverified_page" if isolated
                     else "blocked_unknown_http", account["key"]))
                store.db.execute(
                    "UPDATE history_requests SET status='blocked' WHERE request_id=?",
                    (request_id,))
                if isolated:
                    coordinator.db.execute(
                        "UPDATE lanes SET halted=0,reason=NULL,ready_at=? "
                        "WHERE lane=? AND reason=?",
                        (stamp(now), lane, hold_reason))
                    return {"status": "account_blocked", "account_key": account["key"],
                            "lane": lane}
            return {"status": "manual_review", "account_key": account["key"], "lane": lane}
        evidence = journal.get_response_evidence(request_id)
        usage = (evidence.get("safe_usage", {}).get("max_usage_percent")
                 if evidence and evidence.get("classification") == "safe" else None)
        if type(usage) is int and 0 <= usage < 85:
            coordinator.db.execute(
                "UPDATE lanes SET halted=0,reason=NULL,ready_at=? "
                "WHERE lane=? AND reason=?",
                (stamp(now), lane, hold_reason))
        elif type(usage) is int and usage >= 85:
            coordinator.observe_usage(lane, usage, now=now)
        return dict(outcome, lane=lane, usage_percent=usage)
    return {"status": "no_ready_work", "eligible": len(plan["jobs"])}


def _latest_captures(coordinator, journal, accounts):
    """Derive page-3 Capture IDs from exact immutable job/request bindings."""
    captures = {}
    if coordinator.db.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' "
            "AND name='job_request_bindings'").fetchone() is None:
        return captures
    for account in accounts:
        ordinal = account["ordinal"]
        jobs = coordinator.db.execute(
            "SELECT page,phase,state FROM jobs WHERE account_ordinal=? ORDER BY page",
            (ordinal,)).fetchall()
        if len(jobs) != 3 or any(row["state"] != "done" for row in jobs):
            continue
        binding = coordinator.db.execute(
            "SELECT b.request_id FROM job_request_bindings b "
            "JOIN jobs j ON j.id=b.job_id "
            "WHERE j.account_ordinal=? AND j.page=3 AND j.state='done'",
            (ordinal,)).fetchone()
        if binding is None:
            continue
        capture = journal.get_capture_for_request(binding["request_id"])
        if capture is not None:
            captures[account["key"]] = capture.capture_id
    return captures


def _final_history_status(events, coordinator):
    """Preserve explicit failure, otherwise surface any durable quota hold."""
    status = events[-1]["status"] if events else "no_ready_work"
    if status in ("manual_review", "blocked_unknown_http",
                  "blocked_missing_cursor", "blocked_unverified_page",
                  "account_blocked"):
        return status
    held = coordinator.db.execute(
        "SELECT lane FROM lanes WHERE halted=1 ORDER BY lane LIMIT 1").fetchone()
    return "quota_hold" if held is not None else status


def run_history(source, workspace, *, environment, max_pages=5000,
                live=False, approved=False, clock=None):
    """Explicit operator invocation; no HTTP unless both live gates are set."""
    if live is not True or approved is not True:
        raise ValueError("History requires both explicit live and owner approval")
    if type(max_pages) is not int or not 1 <= max_pages <= 50000:
        raise ValueError("History bounded page budget must be 1..50000")
    accounts, digest = load_p50_roster(source)
    root = Path(workspace).expanduser()
    if root.is_symlink() or root.name.lower() != "p50" or not root.is_dir():
        raise ValueError("Existing isolated P50 workspace required")
    root = root.resolve()
    days = sorted(root.glob("????-??-??/coordinator.sqlite"))
    if not days:
        raise ValueError("P50 latest collection must establish an existing day first")
    control_path = days[-1]
    raw_path = root / "p50_raw.sqlite"
    latest_capture_path = control_path.parent / "capture.sqlite"
    history_capture_path = root / "history_capture.sqlite"
    history_db_path = root / "history.sqlite"
    for path in (control_path.parent, control_path, raw_path, latest_capture_path,
                 history_capture_path, history_db_path):
        if path.is_symlink():
            raise ValueError("P50 symlink store forbidden")
    if not all(path.is_file() for path in (control_path, raw_path, latest_capture_path)):
        raise ValueError("Missing latest RAW/Capture receipts")
    values = _credentials(environment)
    version = environment.get("P6_META_GRAPH_VERSION", "v26.0")
    if not isinstance(version, str) or not VERSION.fullmatch(version):
        raise ValueError("Invalid Graph version")
    now_fn = clock or (lambda: datetime.now(timezone.utc))
    with serial_tick_lock(root / "operator"):
        with ExitStack() as stack:
            raw_db = connect(raw_path, accounts, digest)
            stack.callback(raw_db.close)
            raw_db.isolation_level = None
            raw = ContractRawWriter(raw_db)
            coordinator = P50Coordinator(control_path)
            stack.callback(coordinator.db.close)
            latest = CaptureJournal(str(latest_capture_path))
            stack.callback(latest.close)
            with P50HistoryStore(history_db_path) as store:
                old = store.db.execute("SELECT DISTINCT cutoff_day FROM history_accounts").fetchall()
                cutoff = (date.fromisoformat(old[0][0]) if len(old) == 1
                          else now_fn().astimezone(KST).date() - timedelta(days=365))
                store.initialize([a["key"] for a in accounts], cutoff)
                _setup_bindings(store)
                mapping = _latest_captures(coordinator, latest, accounts)
                handoff = handoff_completed_latest_accounts(
                    coordinator, store, latest, raw, accounts,
                    {k: v for k, v in mapping.items() if store.read(k)["state"] == "ready"})
                if not any(store.read(a["key"])["state"] != "ready" for a in accounts):
                    return {"status": "needs_verified_latest", "deferred": len(handoff["deferred"]),
                            "http_requests": 0, "pages_committed": 0}
                journal = CaptureJournal(str(history_capture_path))
                stack.callback(journal.close)
                transport = _transports(values)
                events = []
                committed = 0
                for _ in range(max_pages):
                    result = history_step(
                        store, coordinator, journal, raw, transport, accounts,
                        digest, {lane: values[lane][1] for lane in LANES},
                        version, now=now_fn())
                    events.append(result)
                    if result["status"] in ("recovered", "already_committed"):
                        committed += result["status"] == "recovered"
                    if result["status"] in ("no_ready_work", "quota_hold", "manual_review",
                                            "blocked_unknown_http"):
                        break
                counts = dict(store.db.execute(
                    "SELECT state,COUNT(*) FROM history_accounts GROUP BY state").fetchall())
                finished = counts.get("window_covered", 0) + counts.get("terminal_verified", 0)
                final_status = _final_history_status(events, coordinator)
                # Only an entirely verified 50-account window may report complete.
                # Do not overwrite a safety hold or explicit failure with success.
                if finished == len(accounts) and final_status == "no_ready_work":
                    final_status = "completed"
                # A run can make progress on healthy accounts after an isolated
                # failure; its final no_ready_work must not hide blocked accounts.
                if final_status == "no_ready_work" and finished != len(accounts):
                    blocked = [state for state in (
                        "blocked_unknown_http", "blocked_unverified_page",
                        "blocked_missing_cursor", "blocked_repeated_cursor")
                        if counts.get(state, 0)]
                    if blocked:
                        final_status = "manual_review"
                return {"status": final_status,
                        "collection_complete": finished == len(accounts),
                        "completed_accounts": finished,
                        "remaining_accounts": len(accounts) - finished,
                        "pages_committed": committed, "attempted_steps": len(events),
                        "account_states": counts,
                        "last_account": events[-1].get("account_key") if events else None,
                        "last_lane": events[-1].get("lane") if events else None}
