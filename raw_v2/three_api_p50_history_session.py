"""Offline P50 history integration facade; no Meta transport."""
from __future__ import annotations

from three_api_p50_history_recovery import recover_history_pages
from three_api_p50_history_status import history_status


def run_offline_history_session(store, journal, raw_writer, account_keys,
                                usernames, *, latest_complete_keys,
                                captured_pages, max_steps=10, lane="A"):
    """Inspect, recover only durable pages, and report persisted state."""
    account_keys = tuple(account_keys)
    latest_complete_keys = frozenset(latest_complete_keys)
    if type(max_steps) is not int or not 1 <= max_steps <= 50:
        raise ValueError("History session step budget must be 1..50")
    before = history_status(
        store, account_keys, latest_complete_keys=latest_complete_keys)
    recovery = recover_history_pages(
        store, journal, raw_writer, account_keys, usernames,
        latest_complete_keys=latest_complete_keys,
        captured_pages=captured_pages, max_steps=max_steps, lane=lane)
    after = history_status(
        store, account_keys, latest_complete_keys=latest_complete_keys)
    pages_added = after["verified_pages"] - before["verified_pages"]
    if pages_added != len(recovery["completed"]):
        raise RuntimeError("History session receipt count differs from durable pages")
    return {
        "mode": "P50_HISTORY_OFFLINE_SESSION",
        "before": before,
        "recovery": recovery,
        "after": after,
        "network_requests": 0,
        "history_pages_added": pages_added,
    }
