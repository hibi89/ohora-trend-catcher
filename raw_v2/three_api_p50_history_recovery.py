"""Bounded, serial, offline P50 history recovery loop.

Only consumes already-persisted captures. No transport or RAW mutation.
"""
from __future__ import annotations

from three_api_p50_history_commit import commit_persisted_history_page
from three_api_p50_history_request import prepare_history_request
from three_api_p50_history_backlog import scan_history_backlog


def recover_history_pages(store, journal, raw_writer, account_keys, usernames, *,
                          latest_complete_keys, captured_pages, max_steps=10,
                          lane="A"):
    """Recover one verified page per ready account; defer absent evidence."""
    if type(max_steps) is not int or not 1 <= max_steps <= 50:
        raise ValueError("Recovery step budget must be 1..50")
    if not isinstance(captured_pages, dict):
        raise ValueError("Explicit persisted page index required")
    completed = []
    backlog = scan_history_backlog(store, account_keys,
        latest_complete_keys=latest_complete_keys, captured_pages=captured_pages)
    available = {j['account_key'] for j in backlog['ready']}
    deferred = [j['account_key'] for j in backlog['awaiting_persisted_page']]
    budget_exhausted = len(available) > max_steps
    for _ in range(min(max_steps, len(available))):
        job = prepare_history_request(
            store, account_keys, usernames,
            latest_complete_keys=available, lane=lane)
        if job is None:
            return {"status": "awaiting_persisted_page" if deferred else "no_eligible_cursor", "completed": completed,
                    "network_requests": 0, "deferred_accounts": deferred}
        key = (job["account_key"], job["cursor"])
        evidence = captured_pages.get(key)
        if evidence is None:
            deferred.append(key[0])
            available.discard(key[0])
            continue
        if not isinstance(evidence, dict) or set(evidence) != {
                "capture_id", "oldest_post_at", "terminal"}:
            raise ValueError("Invalid persisted history page descriptor")
        decision = commit_persisted_history_page(
            store, journal, raw_writer, account_key=key[0],
            capture_id=evidence["capture_id"],
            oldest_post_at=evidence["oldest_post_at"],
            terminal=evidence["terminal"])
        completed.append({"account_key": key[0], "state": decision["state"]})
        available.discard(key[0])
    return {"status": "step_budget_exhausted" if budget_exhausted else
            ("awaiting_persisted_page" if deferred else "no_eligible_cursor"),
            "completed": completed, "network_requests": 0,
            "deferred_accounts": deferred}
