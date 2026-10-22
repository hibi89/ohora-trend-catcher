"""P50 local operator CLI: history, latest and read-only status.

Only explicit --live AND --confirm-owner-approval can dispatch HTTP.
No scheduler, credential output, automatic retry of unknown outcomes or main DB.
"""
from __future__ import annotations

import argparse
from datetime import datetime, timezone, timedelta
import json
import os
from pathlib import Path
import sqlite3
from contextlib import closing

from three_api_env_loader import load_three_api_environment
from three_api_p50_manual_pilot import run_p50
from three_api_p50_status import inspect_p50
from three_api_p50_history_live_runner import run_history
from three_api_serial_lock import serial_tick_lock

KST = timezone(timedelta(hours=9))


def _paths():
    home = Path.home()
    return (home / "Downloads" / "ohora_p50_roster_20261022.csv",
            Path(os.environ.get("LOCALAPPDATA", str(home))) / "OHORA_P50" / "p50")


def _needs_latest(workspace):
    root = Path(workspace).expanduser()
    if root.name.lower() != "p50" or root.is_symlink():
        raise ValueError("Dedicated non-symlink P50 workspace required")
    candidates = sorted(root.glob("????-??-??/coordinator.sqlite"))
    if not candidates:
        raise ValueError("P50 latest coordinator missing; refusing implicit workspace initialization")
    latest = candidates[-1]
    if latest.is_symlink() or latest.parent.is_symlink():
        raise ValueError("Symlink P50 coordinator is forbidden")
    with closing(sqlite3.connect(latest.resolve().as_uri() + "?mode=ro", uri=True)) as db:
        # Latest work is 150 fixed jobs; preexisting V10/V11 observations do
        # NOT count as verified P50 three-page Capture+RAW completion.
        done, count = db.execute(
            "SELECT SUM(CASE WHEN state='done' THEN 1 ELSE 0 END), COUNT(*) FROM jobs"
        ).fetchone()
        return count != 150 or done != 150


def history_status(workspace):
    root = Path(workspace).expanduser()
    if root.name.lower() != "p50" or root.is_symlink():
        raise ValueError("Dedicated P50 workspace required")
    path = root / "history.sqlite"
    if not path.exists():
        return {"history": "not_initialized"}
    if path.is_symlink():
        raise ValueError("History DB symlink forbidden")
    with closing(sqlite3.connect(path.resolve().as_uri() + "?mode=ro", uri=True)) as db:
        state = dict(db.execute(
            "SELECT state, COUNT(*) FROM history_accounts GROUP BY state").fetchall())
        verified = db.execute("SELECT COALESCE(SUM(pages_verified),0) FROM history_accounts").fetchone()[0]
        accounts = [
            {"account_key": key, "state": status,
             "pages_verified": pages, "cursor_present": cursor is not None}
            for key, status, pages, cursor in db.execute(
                "SELECT account_key,state,pages_verified,cursor "
                "FROM history_accounts ORDER BY account_key")
        ]
        return {"history": "initialized", "states": state,
                "history_pages_verified": verified, "accounts": accounts}


def main(argv=None):
    source, workspace = _paths()
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("mode", choices=("history", "latest", "status"))
    parser.add_argument("--source", type=Path, default=source)
    parser.add_argument("--workspace", type=Path, default=workspace)
    parser.add_argument("--credentials-dir", type=Path)
    parser.add_argument("--max-pages", type=int, default=None)
    parser.add_argument("--live", action="store_true")
    parser.add_argument("--confirm-owner-approval", action="store_true")
    args = parser.parse_args(argv)
    try:
        if args.max_pages is not None:
            ceiling = 150 if args.mode == "latest" else 50000 if args.mode == "history" else 0
            if not 1 <= args.max_pages <= ceiling:
                raise ValueError("--max-pages outside permitted mode-specific range")
        if args.mode == "status":
            if args.live or args.confirm_owner_approval or args.credentials_dir:
                raise ValueError("Read-only status cannot accept live options")
            result = {"latest": inspect_p50(args.workspace),
                      **history_status(args.workspace)}
        else:
            if not args.live or not args.confirm_owner_approval:
                raise ValueError("Live operation requires both explicit approval flags")
            environment = os.environ
            if args.credentials_dir is not None:
                environment = load_three_api_environment(
                    args.credentials_dir, base_environment=os.environ)
            if args.mode == "latest":
                with serial_tick_lock(Path(args.workspace).expanduser() / "operator"):
                    result = run_p50(
                        args.source, args.workspace, live=True, approved=True,
                        environment=environment,
                        max_pages=args.max_pages or 150)
            else:
                # Resume only pending fixed P50 latest jobs, never start a
                # blanket re-fetch of all existing observations.
                latest_result = None
                if _needs_latest(args.workspace):
                    with serial_tick_lock(Path(args.workspace).expanduser() / "operator"):
                        latest_result = run_p50(
                            args.source, args.workspace, live=True, approved=True,
                            environment=environment, max_pages=150)
                # A quota-held or incomplete latest round is NOT permission
                # to start history in the same invocation. Recheck durable
                # latest completion, rather than trusting a return status.
                if latest_result is not None and _needs_latest(args.workspace):
                    result = {
                        "status": "latest_incomplete",
                        "http_requests_history": 0,
                        "latest_preparation": {
                            "status": latest_result["status"],
                            "committed_pages": latest_result["committed_pages"],
                        },
                    }
                    print(json.dumps(result, ensure_ascii=False, sort_keys=True))
                    return 2
                result = run_history(
                    args.source, args.workspace, environment=environment,
                    live=True, approved=True, max_pages=args.max_pages or 5000)
                if latest_result is not None:
                    result["latest_preparation"] = {
                        "status": latest_result["status"],
                        "committed_pages": latest_result["committed_pages"]}
        print(json.dumps(result, ensure_ascii=False, sort_keys=True))
        if args.mode == "history" and result.get("collection_complete") is False:
            return 2
        if args.mode != "status" and result.get("status") in (
                "quota_hold", "manual_review", "blocked_unknown_http",
                "needs_verified_latest", "latest_incomplete", "account_blocked",
                "blocked_missing_cursor", "blocked_unverified_page",
                "missing_usage", "usage_limit"):
            return 2
        return 0
    except KeyboardInterrupt:
        parser.exit(130, "P50 interrupted; existing receipts and cursors preserved.\n")
    except (ValueError, TypeError, RuntimeError, OSError, sqlite3.Error):
        parser.exit(2, "P50 safely stopped; inspect account states and local setup.\n")


if __name__ == "__main__":
    raise SystemExit(main())
