"""Explicit, guarded B-lane resume after manual verification. No Meta calls.

Preserves the immutable failed request and never retries it. Operator must
supply --approve-b-resume; otherwise this is read-only. Does not dispatch work.
"""
from __future__ import annotations
import argparse
from datetime import datetime, timezone
import json
from pathlib import Path
import sqlite3
import sys

def run(workspace: Path, day: str, approve: bool):
    root = workspace.expanduser()
    control = root / day / "coordinator.sqlite"
    capture = root / day / "capture.sqlite"
    if (not root.is_dir() or root.is_symlink() or not control.is_file()
            or not capture.is_file() or control.is_symlink() or capture.is_symlink()):
        raise ValueError("Existing isolated pilot stores required")
    uri = control.resolve().as_uri() + ("?mode=rw" if approve else "?mode=ro")
    with sqlite3.connect(uri, uri=True, timeout=15, isolation_level=None) as db:
        db.row_factory = sqlite3.Row
        if approve:
            db.execute("BEGIN IMMEDIATE")
        try:
            lane = db.execute(
                "SELECT halted,reason,ready_at FROM lanes WHERE lane='B'"
            ).fetchone()
            failed = db.execute(
                "SELECT j.id,j.state,j.account_ordinal,j.page,b.request_id "
                "FROM jobs j JOIN job_request_bindings b ON b.job_id=j.id "
                "WHERE j.id=451 AND j.lane='B'"
            ).fetchone()
            candidate = db.execute(
                "SELECT j.id,j.state,j.lane,j.page,"
                "EXISTS(SELECT 1 FROM job_request_bindings b WHERE b.job_id=j.id) AS bound "
                "FROM jobs j WHERE j.account_ordinal=152 AND j.page=1"
            ).fetchone()
            if lane is None or failed is None or candidate is None:
                raise ValueError("Required B-lane evidence missing")
            with sqlite3.connect(capture.resolve().as_uri()+"?mode=ro",uri=True) as c:
                outcome = c.execute(
                    "SELECT outcome FROM intents WHERE request_id=?",
                    (failed["request_id"],)
                ).fetchone()
                captured = c.execute(
                    "SELECT COUNT(*) FROM captures WHERE request_id=?",
                    (failed["request_id"],)
                ).fetchone()[0]
            now = datetime.now(timezone.utc)
            ready = datetime.fromisoformat(lane["ready_at"])
            safe = (
                failed["account_ordinal"] == 151
                and failed["page"] == 1
                and failed["state"] == "leased"
                and outcome is not None and outcome[0] == "failed_no_retry"
                and captured == 0
                and candidate["lane"] == "B"
                and candidate["state"] == "pending"
                and candidate["bound"] == 0
                and lane["halted"] == 1
                and lane["reason"] == "live_pending_usage"
                and ready <= now
            )
            if not safe:
                raise ValueError("Guarded B-lane resume conditions not met")
            if approve:
                changed = db.execute(
                    "UPDATE lanes SET halted=0,reason=NULL "
                    "WHERE lane='B' AND halted=1 AND reason='live_pending_usage' "
                    "AND ready_at<=?",
                    (now.isoformat(),)
                ).rowcount
                if changed != 1:
                    raise ValueError("B lane state changed")
                db.execute("COMMIT")
            return {
                "mode": "MANUAL_B_RESUME" if approve else "READ_ONLY_B_RESUME_PREFLIGHT",
                "safe_conditions": True,
                "lane_resumed": bool(approve),
                "failed_job_451_preserved": True,
                "account_152_unbound": True,
                "meta_called": False,
            }
        except BaseException:
            if approve:
                db.execute("ROLLBACK")
            raise

def main():
    p = argparse.ArgumentParser()
    p.add_argument("--workspace", type=Path, required=True)
    p.add_argument("--day", required=True)
    p.add_argument("--approve-b-resume", action="store_true")
    a = p.parse_args()
    try:
        print(json.dumps(run(a.workspace,a.day,a.approve_b_resume),sort_keys=True))
    except (ValueError,sqlite3.Error,OverflowError):
        sys.exit("B resume refused: safety conditions not met.")

if __name__ == "__main__":
    main()
