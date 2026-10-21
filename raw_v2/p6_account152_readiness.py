"""Read-only inspection of P6 account 152 dispatch safety. Never calls Meta or writes SQLite."""
from __future__ import annotations
import argparse
import json
from pathlib import Path
import sqlite3

def inspect(workspace: Path, day: str):
    path = workspace / day / "coordinator.sqlite"
    if not path.is_file() or path.is_symlink():
        raise ValueError("Existing coordinator database required")
    with sqlite3.connect(path.as_uri() + "?mode=ro", uri=True) as db:
        db.row_factory = sqlite3.Row
        job = db.execute(
            "SELECT id,lane,page,state,lease_id,leased_until FROM jobs "
            "WHERE account_ordinal=152 AND page=1"
        ).fetchone()
        lane = db.execute(
            "SELECT halted,reason,ready_at FROM lanes WHERE lane='B'"
        ).fetchone()
        if job is None or lane is None or job["lane"] != "B":
            raise ValueError("Frozen account 152 lane mapping not found")
        binding_table = db.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' "
            "AND name='job_request_bindings'"
        ).fetchone()
        bound = bool(binding_table and db.execute(
            "SELECT 1 FROM job_request_bindings WHERE job_id=?",
            (job["id"],)
        ).fetchone())
        return {
            "mode": "READ_ONLY_NO_META_NO_DB_WRITES",
            "account_ordinal": 152,
            "lane": "B",
            "page": 1,
            "job_state": job["state"],
            "already_bound": bound,
            "lane_halted": bool(lane["halted"]),
            "lane_reason": lane["reason"],
            "lane_ready_at": lane["ready_at"],
            "needs_manual_lane_review": bool(lane["halted"]),
            "eligible_for_new_dispatch_only_if_lane_ready": (
                job["state"] == "pending" and not bound and not lane["halted"]
            ),
        }

if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--workspace", type=Path, required=True)
    parser.add_argument("--day", required=True)
    args = parser.parse_args()
    try:
        print(json.dumps(inspect(args.workspace, args.day), sort_keys=True))
    except (ValueError, sqlite3.Error):
        parser.exit(2, "Read-only account 152 inspection unavailable.\n")
