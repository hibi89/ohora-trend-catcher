"""Explain P50 latest dispatch blockers from SQLite, without opening a writer.

No Meta HTTP, tokens, leases, cursor mutations, or quota release. Reports only
aggregated job counts, never account identities or request payloads.
"""
from __future__ import annotations

import argparse
from contextlib import closing
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import sqlite3

LANES = ("A", "B", "C")


def _time(raw):
    value = datetime.fromisoformat(raw.replace("Z", "+00:00"))
    if value.utcoffset() is None:
        raise ValueError("Timezone-aware database timestamp required")
    return value.astimezone(timezone.utc)


def inspect_queue(workspace, *, now=None):
    now = now or datetime.now(timezone.utc)
    if not isinstance(now, datetime) or now.utcoffset() is None:
        raise ValueError("Timezone-aware reference time required")
    now = now.astimezone(timezone.utc)
    root = Path(workspace).expanduser()
    if root.name.lower() != "p50" or root.is_symlink() or not root.is_dir():
        raise ValueError("Existing P50 workspace required")
    candidates = sorted(root.glob("????-??-??/coordinator.sqlite"))
    if not candidates:
        raise ValueError("No P50 daily coordinator")
    path = candidates[-1]
    if path.is_symlink() or path.parent.is_symlink():
        raise ValueError("Unsafe coordinator path")
    with closing(sqlite3.connect(path.resolve().as_uri() + "?mode=ro", uri=True)) as db:
        db.row_factory = sqlite3.Row
        tables = {r[0] for r in db.execute(
            "SELECT name FROM sqlite_master WHERE type='table'")}
        if not {"lanes", "jobs", "page_evidence", "job_request_bindings"} <= tables:
            raise ValueError("Required P50 cursor/binding tables missing")
        lanes = db.execute(
            "SELECT lane,halted,reason,ready_at FROM lanes ORDER BY lane").fetchall()
        if len(lanes) != 3 or [r["lane"] for r in lanes] != list(LANES):
            raise ValueError("Invalid lane roster")
        jobs = db.execute(
            "SELECT id,account_ordinal,page,lane,state,leased_until FROM jobs"
        ).fetchall()
        if not jobs:
            raise ValueError("Daily jobs missing")
        done = {(r["account_ordinal"], r["page"]) for r in jobs
                if r["state"] == "done"}
        bindings = {r[0] for r in db.execute(
            "SELECT job_id FROM job_request_bindings")}
        proofs = {(r[0], r[1]): (r[2], r[3]) for r in db.execute(
            "SELECT account_ordinal,page,terminal,response_cursor FROM page_evidence"
        )}
        rows = {}
        for lane in lanes:
            ready = not lane["halted"] and _time(lane["ready_at"]) <= now
            rows[lane["lane"]] = {
                "halted": bool(lane["halted"]), "reason": lane["reason"],
                "ready_at_utc": lane["ready_at"], "lane_ready": ready,
                "done": 0, "eligible_before_gate": 0,
                "dispatchable_now": 0, "waiting_predecessor": 0,
                "waiting_cursor": 0, "already_bound": 0,
                "active_lease": 0,
            }
        for job in jobs:
            state = rows[job["lane"]]
            if job["state"] == "done":
                state["done"] += 1
                continue
            if job["id"] in bindings:
                state["already_bound"] += 1
                continue
            if (job["state"] == "leased" and job["leased_until"]
                    and _time(job["leased_until"]) > now):
                state["active_lease"] += 1
                continue
            if any((job["account_ordinal"], prior) not in done
                   for prior in range(1, job["page"])):
                state["waiting_predecessor"] += 1
                continue
            if job["page"] > 1:
                proof = proofs.get((job["account_ordinal"], job["page"] - 1))
                if proof is None or proof[0] != 0 or proof[1] is None:
                    state["waiting_cursor"] += 1
                    continue
            state["eligible_before_gate"] += 1
            if state["lane_ready"]:
                state["dispatchable_now"] += 1
        # Daily latest front coverage matters independently of catch-up pages.
        by_page = {
            str(page): {
                "done": sum(j["state"] == "done" and j["page"] == page for j in jobs),
                "total": sum(j["page"] == page for j in jobs),
            } for page in (1, 2, 3)
        }
        remaining = len(jobs) - len(done)
        ready_count = sum(item["dispatchable_now"] for item in rows.values())
        held_eligible = sum(item["eligible_before_gate"]
                            for item in rows.values() if item["halted"])
        reason = ("ready_for_latest" if ready_count else
                  "waiting_quota_lane" if held_eligible else
                  "waiting_predecessor_or_evidence")
        return {
            "schema": "ohora.p50.queue.diagnostic.v1",
            "date_kst": path.parent.name, "expected_pages": 150,
            "jobs_total": len(jobs), "jobs_done": len(done),
            "jobs_remaining": remaining,
            "front_accounts_completed": by_page["1"]["done"],
            "page_coverage": by_page,
            "daily_complete": remaining == 0 and len(jobs) == 150,
            "dispatchable_now": ready_count,
            "held_lanes_eligible_jobs": held_eligible,
            "next_action": reason,
            "lanes": rows, "meta_calls": 0, "db_writes": 0,
        }


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--workspace", type=Path,
                        default=Path(os.environ.get("LOCALAPPDATA", str(Path.home() / "AppData" / "Local"))) / "OHORA_P50" / "p50")
    args = parser.parse_args(argv)
    try:
        result = inspect_queue(args.workspace)
    except (OSError, sqlite3.Error, ValueError, TypeError) as exc:
        print(json.dumps({"status": "unavailable", "error_type": type(exc).__name__,
                          "meta_calls": 0, "db_writes": 0}))
        return 2
    print(json.dumps(result, ensure_ascii=False, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
