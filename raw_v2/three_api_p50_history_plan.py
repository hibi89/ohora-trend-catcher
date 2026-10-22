"""Offline P50 history dispatch planner; no HTTP, credentials or writes.

One account at a time; latest daily collection remains separate. A persisted
history cursor can be resumed after midnight without reusing daily jobs.
"""
from __future__ import annotations

from datetime import date


def plan_next_history_page(store, account_keys, *, latest_complete_keys, max_pages=1):
    if type(max_pages) is not int or not 1 <= max_pages <= 50:
        raise ValueError("History page budget must be 1..50")
    keys = tuple(account_keys)
    if len(keys) != 50 or len(set(keys)) != 50:
        raise ValueError("Exactly 50 unique P50 accounts required")
    complete = set(latest_complete_keys)
    if not complete.issubset(set(keys)):
        raise ValueError("Unknown latest-complete account")
    jobs = []
    for key in keys:
        if key not in complete:
            continue
        row = store.read(key)
        if row is None or row["account_key"] != key:
            raise ValueError("History account not initialized")
        # Never dispatch an unanchored history cursor. A validated latest-tail
        # receipt must establish the first cursor before live history begins.
        if row["state"] == "ready":
            continue
        if row["state"] != "continuation_ready":
            continue
        if not row["cursor"]:
            raise ValueError("Persisted continuation missing cursor")
        jobs.append({"account_key": key, "cursor": row["cursor"],
                     "verified_pages": row["pages_verified"],
                     "cutoff_day": date.fromisoformat(row["cutoff_day"]).isoformat(),
                     "requires_explicit_live_approval": True})
        if len(jobs) == max_pages:
            break
    return {"mode": "P50_HISTORY_OFFLINE_PLAN", "jobs": jobs,
            "network_requests": 0, "database_writes": 0}
