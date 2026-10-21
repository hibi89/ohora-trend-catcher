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
    load_three_api_environment, run_manual_pilot,
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
    env = load_three_api_environment(credentials_dir, base_environment=os.environ)
    result = run_manual_pilot(source, workspace, live=True,
                              confirm_owner_approval=True,
                              environment=env, max_pages=max_pages)
    out.update({"action": "LIVE_TICK_COMPLETE", "status": result["status"],
                "committed_pages": result["committed_pages"],
                "account_failures": result["account_failures"],
                "live_meta_called": result["attempted_steps"] > 0})
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
        result = {"action": "MANUAL_REVIEW", "live_meta_called": False}
        print(json.dumps(result, separators=(",", ":")))
        return 2
    print(json.dumps(result, separators=(",", ":")))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
