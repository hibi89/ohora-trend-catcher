"""Fail-closed n8n tick adapter for bounded P6 collection.

The schedule decision is offline. Meta HTTP is possible only when the caller
explicitly enables the live gate; this module never stores credentials.
"""
from __future__ import annotations

import argparse
from datetime import date, datetime, timezone
import json
import os
from pathlib import Path
import re
import sqlite3
import sys
import tempfile

from src.collection_schedule import decide, KST
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "raw_v2"))
from three_api_manual_pilot import (
    load_three_api_environment, read_pilot_status, run_manual_pilot,
)


def _state_path(path: Path) -> Path:
    if path.exists():
        data = json.loads(path.read_text(encoding="utf-8"))
        if (not isinstance(data, dict) or data.get("schema_version") != 1
                or set(data) != {"schema_version", "first_verified_success_at"}):
            raise ValueError("Invalid scheduler state")
        stamp = data["first_verified_success_at"]
        if stamp is not None:
            datetime.fromisoformat(stamp.replace("Z", "+00:00"))
        return path
    return path


def _save_state(path: Path, stamp: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = json.dumps({"schema_version": 1,
                          "first_verified_success_at": stamp},
                         separators=(",", ":"))
    fd, temporary = tempfile.mkstemp(prefix=".p6-state-", dir=path.parent)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as stream:
            stream.write(payload)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


def _pilot_status_day(workspace: Path, today: str) -> str | None:
    """Return the newest existing pilot coordinator day for a read-only snapshot."""
    root = workspace.expanduser()
    if not root.is_dir() or root.is_symlink():
        return None
    days = [child.name for child in root.iterdir()
            if child.is_dir() and not child.is_symlink()
            and re.fullmatch(r"20\d{2}-\d{2}-\d{2}", child.name)
            and child.name <= today
            and (child / "coordinator.sqlite").is_file()
            and not (child / "coordinator.sqlite").is_symlink()]
    return max(days) if days else None


def _receipt_snapshot(workspace: Path, today: str) -> dict:
    """Read durable receipt/lane counts through the existing read-only status API."""
    root = workspace.expanduser()
    raw = root / "p6_pilot_raw.sqlite"
    day = _pilot_status_day(root, today)
    if not raw.exists() and day is None:
        return {"durable_receipts": 0, "lanes": None}
    if raw.is_symlink() or not raw.is_file() or day is None:
        raise ValueError("Pilot receipt baseline unavailable")
    snapshot = read_pilot_status(root, day=day)
    receipts = snapshot.get("durable_receipts")
    lanes = snapshot.get("lanes")
    if (type(receipts) is not int or receipts < 0 or not isinstance(lanes, dict)
            or set(lanes) != {"A", "B", "C"}):
        raise ValueError("Malformed pilot receipt snapshot")
    return {"durable_receipts": receipts, "lanes": lanes}


def _read_gate(workspace: Path, today: str, now: datetime):
    """Read latest coordinator state without creating/opening a writer."""
    root = workspace.resolve()
    candidates = []
    if root.is_dir() and not root.is_symlink():
        for child in root.iterdir():
            if (child.is_dir() and not child.is_symlink()
                    and re.fullmatch(r"20\d{2}-\d{2}-\d{2}", child.name)
                    and child.name <= today):
                db = child / "coordinator.sqlite"
                if db.is_file() and not db.is_symlink():
                    candidates.append((child.name, db))
    if not candidates:
        return "absent", None, False, None
    day, path = max(candidates)
    uri = path.resolve().as_uri() + "?mode=ro"
    with sqlite3.connect(uri, uri=True) as db:
        lanes = db.execute("SELECT lane,halted,reason,ready_at FROM lanes").fetchall()
        jobs = db.execute("SELECT state,COUNT(*) FROM jobs GROUP BY state").fetchall()
        binding_table = db.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' AND name='job_request_bindings'"
        ).fetchone()
        unknown = False
        if binding_table:
            capture = path.parent / "capture.sqlite"
            if capture.is_file() and not capture.is_symlink():
                with sqlite3.connect(capture.resolve().as_uri() + "?mode=ro", uri=True) as cdb:
                    unknown = cdb.execute(
                        "SELECT 1 FROM intents WHERE outcome='unknown' LIMIT 1"
                    ).fetchone() is not None
    if len(lanes) != 3 or {row[0] for row in lanes} != {"A", "B", "C"}:
        raise ValueError("Incomplete coordinator gates")
    if unknown:
        return "manual_review", None, False, date.fromisoformat(day)
    ready_rows = []
    for lane, halted, reason, ready in lanes:
        if type(halted) is not int or halted not in (0, 1) or (halted and not reason):
            raise ValueError("Malformed coordinator gate")
        ready_dt = datetime.fromisoformat(ready.replace("Z", "+00:00"))
        if ready_dt.utcoffset() is None:
            raise ValueError("Invalid coordinator time")
        if not halted and ready_dt <= now:
            ready_rows.append(ready_dt)
    counts = dict(jobs)
    daily_state = "running" if counts else "absent"
    if day == today and counts and counts.get("leased", 0):
        daily_state = "manual_review" if unknown else "running"
    return (daily_state, max(ready_rows) if ready_rows else None,
            bool(ready_rows), date.fromisoformat(day))


def tick(*, source: Path, workspace: Path, credentials_dir: Path,
         state_file: Path, now: datetime | None = None, max_pages: int = 3,
         live: bool = False, confirm_owner_approval: bool = False) -> dict:
    if type(max_pages) is not int or not 1 <= max_pages <= 3:
        raise ValueError("Per-tick page cap must be 1..3")
    now = now or datetime.now(timezone.utc)
    if now.tzinfo is None or now.utcoffset() is None:
        raise ValueError("Timezone-aware current time required")
    _state_path(state_file)
    state = json.loads(state_file.read_text(encoding="utf-8")) if state_file.exists() else {
        "schema_version": 1, "first_verified_success_at": None}
    first_success = state["first_verified_success_at"]

    # Read-only current or prior coordinator gate. Holds survive KST rollover.
    today = now.astimezone(KST).date().isoformat()
    daily_state, parsed_ready, lane_allowed, daily_day = _read_gate(workspace, today, now)
    decision = decide(now=now, live_runner_verified=True,
                      first_verified_success_at=(datetime.fromisoformat(first_success.replace("Z", "+00:00")) if first_success else None),
                      daily_day_kst=daily_day,
                      daily_state=daily_state, lane_dispatch_allowed=lane_allowed,
                      ready_at=parsed_ready)
    out = {"action": decision.action, "day_kst": decision.day_kst,
           "committed_pages": 0, "max_pages": max_pages,
           "live_meta_called": False}
    if decision.action not in ("BOOTSTRAP_DUE", "START_DAILY", "CONTINUE_DAILY"):
        return out
    if not live or not confirm_owner_approval:
        out["action"] = "WAIT_EXPLICIT_LIVE_APPROVAL"
        return out
    # Establish an independent read-only baseline before any approved dispatch.
    # An absent pilot store is a valid zero baseline; a partial/unreadable store
    # fails closed so the scheduled runner cannot write without a receipt check.
    try:
        before = _receipt_snapshot(workspace, today)
    except Exception:
        return {**out, "action": "MANUAL_REVIEW",
                "storage_verification": {"status": "baseline_unavailable"},
                "live_meta_called": False}
    env = load_three_api_environment(credentials_dir, base_environment=os.environ)
    result = run_manual_pilot(source, workspace, live=True,
                              confirm_owner_approval=True,
                              environment=env, max_pages=max_pages)
    out.update({"action": "LIVE_TICK_COMPLETE", "status": result["status"],
                "committed_pages": result["committed_pages"],
                "account_failures": result["account_failures"],
                # A bounded runner step may end as no_ready_work or
                # serial_busy before transport dispatch. Count only statuses
                # that prove an HTTP outcome was received; exceptions are
                # handled separately as unknown_after_live_error.
                "live_meta_called": result["status"] in
                ("page_committed", "account_failed")})
    try:
        after = _receipt_snapshot(workspace, today)
        delta = after["durable_receipts"] - before["durable_receipts"]
        verified = delta >= result["committed_pages"] and delta >= 0
        out["storage_verification"] = {
            "status": "verified" if verified else "receipt_delta_mismatch",
            "receipt_delta": delta,
            "durable_receipts": after["durable_receipts"],
            "lanes": after["lanes"],
        }
    except Exception:
        out["storage_verification"] = {"status": "readback_unavailable"}
    verified = result.get("verified_success_at")
    if result["committed_pages"] and isinstance(verified, str) and not first_success:
        _save_state(state_file, verified)
        out["first_success_persisted"] = True
    else:
        out["first_success_persisted"] = False
    return out


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", type=Path, required=True)
    parser.add_argument("--workspace", type=Path, required=True)
    parser.add_argument("--credentials-dir", type=Path, required=True)
    parser.add_argument("--state-file", type=Path, required=True)
    parser.add_argument("--max-pages", type=int, default=3)
    parser.add_argument("--live", action="store_true")
    parser.add_argument("--confirm-owner-approval", action="store_true")
    args = parser.parse_args(argv)
    try:
        result = tick(source=args.source, workspace=args.workspace,
                      credentials_dir=args.credentials_dir,
                      state_file=args.state_file, max_pages=args.max_pages,
                      live=args.live,
                      confirm_owner_approval=args.confirm_owner_approval)
    except Exception:
        # Once an explicitly approved live run starts, an exception may occur
        # after the request has reached Meta. Do not claim zero calls without
        # durable transport evidence proving that no attempt began.
        live_status = ("unknown_after_live_error"
                       if args.live and args.confirm_owner_approval
                       else "not_attempted")
        result = {
            "action": "MANUAL_REVIEW",
            "live_meta_called": None if live_status.startswith("unknown") else False,
            "live_meta_call_status": live_status,
        }
        print(json.dumps(result, separators=(",", ":")))
        return 2
    print(json.dumps(result, separators=(",", ":")))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
