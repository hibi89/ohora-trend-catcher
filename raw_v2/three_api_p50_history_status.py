"""Read-only historical collection progress."""
from collections import Counter
from three_api_p50_history_plan import plan_next_history_page

def history_status(store, account_keys, *, latest_complete_keys):
    keys = tuple(account_keys)
    if len(keys) != 50 or len(set(keys)) != 50:
        raise ValueError("Exactly 50 distinct accounts required")
    rows = [store.read(k) for k in keys]
    if any(r is None or r["account_key"] != k for k, r in zip(keys, rows)):
        raise ValueError("History roster not initialized")
    plan = plan_next_history_page(
        store, keys, latest_complete_keys=latest_complete_keys, max_pages=1)
    return {
        "accounts": 50,
        "states": dict(Counter(r["state"] for r in rows)),
        "verified_pages": sum(r["pages_verified"] for r in rows),
        "anchored": sum(r["state"] != "ready" for r in rows),
        "next_job": plan["jobs"][0] if plan["jobs"] else None,
        "network_requests": 0,
        "database_writes": 0,
    }
