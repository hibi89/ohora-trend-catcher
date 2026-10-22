"""Offline P50 history integration facade; no Meta transport."""
from __future__ import annotations

from three_api_p50_history_recovery import recover_history_pages
from three_api_p50_history_status import history_status


def run_offline_history_session(store, journal, raw_writer, account_keys,
                                usernames, *, latest_complete_keys,
                                captured_pages, max_steps=10, lane="A"):
    """Inspect, recover only durable pages, and report persisted state."""
    before = history_status(
        store, account_keys, latest_complete_keys=latest_complete_keys)
    recovery = recover_history_pages(
        store, journal, raw_writer, account_keys, usernames,
        latest_complete_keys=latest_complete_keys,
        captured_pages=captured_pages, max_steps=max_steps, lane=lane)
    after = history_status(
        store, account_keys, latest_complete_keys=latest_complete_keys)
    return {
        "mode": "P50_HISTORY_OFFLINE_SESSION",
        "before": before,
        "recovery": recovery,
        "after": after,
        "network_requests": 0,
        "history_pages_added": (
            after["verified_pages"] - before["verified_pages"]),
    }
