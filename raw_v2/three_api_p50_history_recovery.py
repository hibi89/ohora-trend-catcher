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
    """Recover bounded verified pages, including multiple pages per account."""
    if type(max_steps) is not int or not 1 <= max_steps <= 50:
        raise ValueError("Recovery step budget must be 1..50")
    account_keys = tuple(account_keys)
    if lane not in ("A", "B", "C"):
        raise ValueError("Unknown P50 API lane")
    if not isinstance(captured_pages, dict):
        raise ValueError("Explicit persisted page index required")
    # An account's captured page index must be keyed by its immutable
    # (account_key, cursor) pair; malformed keys cannot be silently skipped.
    if any(not isinstance(key, str) or not key for key in account_keys):
        raise ValueError("Invalid pilot account key")
    valid_accounts = set(account_keys)
    if len(valid_accounts) != 50 or len(account_keys) != 50:
        raise ValueError("Exactly 50 pilot accounts required")
    for key in captured_pages:
        if (not isinstance(key, tuple) or len(key) != 2
                or not isinstance(key[0], str)
                or key[0] not in valid_accounts
                or not isinstance(key[1], str) or not key[1]):
            raise ValueError("Invalid persisted history page index key")
    # Reject malformed page descriptors before the first cursor can advance.
    from datetime import datetime
    seen_capture_ids = set()
    for descriptor in captured_pages.values():
        if (not isinstance(descriptor, dict) or set(descriptor) !=
                {"capture_id", "oldest_post_at", "terminal"}):
            raise ValueError("Invalid persisted history page descriptor")
        if (not isinstance(descriptor["capture_id"], str)
                or not descriptor["capture_id"]
                or type(descriptor["terminal"]) is not bool
                or (descriptor["oldest_post_at"] is not None and
                    (not isinstance(descriptor["oldest_post_at"], datetime) or
                     descriptor["oldest_post_at"].utcoffset() is None))):
            raise ValueError("Invalid persisted history page evidence")
        if descriptor["capture_id"] in seen_capture_ids:
            raise ValueError("Duplicate history Capture across indexed pages")
        seen_capture_ids.add(descriptor["capture_id"])
        if descriptor["oldest_post_at"] is None and not descriptor["terminal"]:
            raise ValueError("Nonterminal history page requires oldest timestamp")
    completed = []
    latest_complete_keys = frozenset(latest_complete_keys)
    if any(not isinstance(key, str) or not key for key in latest_complete_keys):
        raise ValueError("Invalid latest-complete account key")
    if not latest_complete_keys.issubset(valid_accounts):
        raise ValueError("Unknown latest-complete history account")
    if not isinstance(usernames, dict) or any(
            key not in usernames or not isinstance(usernames[key], str)
            or not usernames[key].strip() for key in latest_complete_keys):
        raise ValueError("Missing verified account username")
    backlog = scan_history_backlog(store, account_keys,
        latest_complete_keys=latest_complete_keys, captured_pages=captured_pages)
    available = {j['account_key'] for j in backlog['ready']}
    deferred = [j['account_key'] for j in backlog['awaiting_persisted_page']]
    for _ in range(max_steps):
        job = prepare_history_request(
            store, account_keys, usernames,
            latest_complete_keys=available, lane=lane)
        if job is None:
            break
        key = (job["account_key"], job["cursor"])
        evidence = captured_pages.get(key)
        if evidence is None:
            if key[0] not in deferred:
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
        persisted = store.read(key[0])
        if (persisted is None or persisted["cursor"] == key[1] or
                persisted["state"] != decision["state"]):
            raise RuntimeError("History commit did not advance durable account state")
        completed.append({"account_key": key[0], "state": decision["state"]})
        if decision["state"] != "continuation_ready":
            available.discard(key[0])
        elif (key[0], store.read(key[0])["cursor"]) not in captured_pages:
            available.discard(key[0])
            if key[0] not in deferred:
                deferred.append(key[0])
    # A ready account without a persisted next page is deferred, not runnable.
    runnable = {key for key in available if (key, store.read(key)["cursor"]) in captured_pages}
    budget_exhausted = bool(runnable)
    return {"status": "step_budget_exhausted" if budget_exhausted else
            ("awaiting_persisted_page" if deferred else
             ("completed" if completed else "no_eligible_cursor")),
            "completed": completed, "network_requests": 0,
            "deferred_accounts": deferred}
