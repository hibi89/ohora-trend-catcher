"""Offline-only P50 latest-to-history handoff using completed jobs and receipts.

The caller supplies exact capture IDs from the latest collector. This module
never discovers a cursor by guessing, sends HTTP, or touches the 294-account DB.
"""
from __future__ import annotations

from datetime import datetime, timezone

from three_api_p50_history_anchor import anchor_latest_tail
from three_api_p50_roster import validate_p50_roster


def handoff_completed_latest_accounts(coordinator, history_store, journal,
                                      raw_writer, accounts, capture_ids):
    """Anchor completed latest tails; reruns skip already-anchored accounts.

    A completed three-page coordinator chain is necessary but not sufficient:
    anchor_latest_tail independently verifies persisted Capture and RAW.
    """
    validate_p50_roster(accounts)
    if not isinstance(capture_ids, dict) or not set(capture_ids).issubset(
            {account["key"] for account in accounts}):
        raise ValueError("Invalid P50 latest-tail capture mapping")
    if any(not isinstance(value, str) or not value for value in capture_ids.values()):
        raise ValueError("Latest-tail capture identifiers must be nonempty")
    completed = []
    deferred = []
    already = []
    for account in accounts:
        key, ordinal = account["key"], account["ordinal"]
        state = history_store.read(key)
        if state is None or state["account_key"] != key:
            raise ValueError("P50 history roster not initialized")
        if state["state"] != "ready":
            supplied = capture_ids.get(key)
            if supplied is not None:
                receipt = history_store.db.execute(
                    "SELECT capture_id FROM history_receipts WHERE account_key=? "
                    "ORDER BY committed_at ASC LIMIT 1", (key,)).fetchone()
                if receipt is None or receipt["capture_id"] != supplied:
                    raise ValueError("Conflicting latest-tail capture for resumed account")
            already.append(key)
            continue
        jobs = coordinator.db.execute(
            "SELECT page,phase,state FROM jobs WHERE account_ordinal=? ORDER BY page",
            (ordinal,)).fetchall()
        if (len(jobs) != 3 or
                [(row["page"], row["phase"], row["state"]) for row in jobs] != [
                    (1, "front", "done"), (2, "catchup", "done"),
                    (3, "catchup", "done")]):
            deferred.append(key)
            continue
        capture_id = capture_ids.get(key)
        if capture_id is None:
            deferred.append(key)
            continue
        record = journal.get_capture(capture_id)
        if (record is None or record.request.account_ordinal != ordinal
                or record.request.account_key != key):
            raise ValueError("Latest-tail Capture does not match P50 roster ordinal")
        timestamps = []
        for post in record.normalized_posts:
            value = post.get("timestamp")
            if not isinstance(value, str):
                raise ValueError("Missing persisted latest-tail timestamp")
            try:
                parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
            except ValueError as exc:
                raise ValueError("Invalid persisted latest-tail timestamp") from exc
            if parsed.utcoffset() is None:
                raise ValueError("Timezone required for latest-tail timestamp")
            timestamps.append(parsed.astimezone(timezone.utc))
        oldest = min(timestamps) if timestamps else None
        terminal = record.cursor_state.next_cursor is None
        anchor_latest_tail(
            history_store, journal, raw_writer, account_key=key,
            capture_id=capture_id, terminal=terminal, oldest_post_at=oldest)
        completed.append(key)
    return {"mode": "P50_OFFLINE_LATEST_HISTORY_HANDOFF",
            "anchored": completed, "deferred": deferred, "already_anchored": already,
            "network_requests": 0}
