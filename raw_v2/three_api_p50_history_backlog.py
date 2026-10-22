"""Offline P50 history backlog scanner; never sends HTTP or changes state."""
from __future__ import annotations
from three_api_p50_history_plan import plan_next_history_page

def scan_history_backlog(store, account_keys, *, latest_complete_keys,
                         captured_pages, max_accounts=50):
    if type(max_accounts) is not int or not 1 <= max_accounts <= 50:
        raise ValueError("Account scan budget must be 1..50")
    if not isinstance(captured_pages, dict):
        raise ValueError("Persisted page index required")
    plan = plan_next_history_page(
        store, account_keys, latest_complete_keys=latest_complete_keys,
        max_pages=max_accounts)
    ready = []
    missing = []
    for job in plan["jobs"]:
        key = (job["account_key"], job["cursor"])
        if key in captured_pages:
            ready.append(job)
        else:
            missing.append(job)
    return {
        "ready": ready,
        "awaiting_persisted_page": missing,
        "network_requests": 0,
        "database_writes": 0,
    }
