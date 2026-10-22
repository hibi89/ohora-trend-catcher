"""Commit a previously persisted P50 history page after receipt verification.

No HTTP and no RAW writes; capture and RAW receipts must already exist.
"""
from __future__ import annotations

from datetime import datetime, timezone
from three_api_runtime_contract import validate_raw_receipt


def commit_persisted_history_page(store, journal, raw_writer, *,
                                  account_key, capture_id, oldest_post_at,
                                  terminal=False):
    record = journal.get_capture(capture_id)
    if record is None or record.capture_id != capture_id:
        raise ValueError("Missing persisted Capture")
    request = record.request
    if request.account_key != account_key or request.phase != "history":
        raise ValueError("Wrong account or non-history Capture")
    if record.cursor_state.request_id != request.request_id:
        raise ValueError("Cursor source mismatch")
    if record.cursor_state.current_cursor != request.cursor:
        raise ValueError("Cursor evidence mismatch")
    if record.cursor_state.page != request.page:
        raise ValueError("Page evidence mismatch")
    # Never trust caller-supplied completion dates over persisted Capture evidence.
    timestamps = []
    for post in record.normalized_posts:
        value = post.get("timestamp")
        if not isinstance(value, str):
            raise ValueError("History post missing timestamp evidence")
        try:
            parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
        except ValueError as exc:
            raise ValueError("Invalid persisted history timestamp") from exc
        if parsed.utcoffset() is None:
            raise ValueError("History timestamp must include timezone")
        timestamps.append(parsed.astimezone(timezone.utc))
    actual_oldest = min(timestamps) if timestamps else None
    if oldest_post_at is not None:
        if not isinstance(oldest_post_at, datetime) or oldest_post_at.utcoffset() is None:
            raise ValueError("Invalid oldest post timestamp")
        if actual_oldest is None or oldest_post_at.astimezone(timezone.utc) != actual_oldest:
            raise ValueError("Oldest post timestamp differs from persisted Capture")
    elif actual_oldest is not None:
        raise ValueError("Missing oldest post timestamp from persisted Capture")
    if type(terminal) is not bool:
        raise ValueError("Explicit terminal boolean required")
    if terminal and record.cursor_state.next_cursor is not None:
        raise ValueError("Terminal history Capture contains continuation cursor")
    receipt = raw_writer.get_raw_receipt(capture_id)
    if receipt is None:
        raise ValueError("Missing durable RAW receipt")
    validate_raw_receipt(record, receipt)
    if not isinstance(receipt.raw_receipt_id, str) or not receipt.raw_receipt_id:
        raise ValueError("Invalid RAW receipt identifier")
    return store.commit_verified_page(
        account_key=account_key, expected_cursor=request.cursor,
        next_cursor=record.cursor_state.next_cursor,
        oldest_post_at=oldest_post_at, terminal=terminal,
        capture_id=capture_id, raw_receipt_id=receipt.raw_receipt_id,
        raw_receipt_verified=True, capture_verified=True,
        uncertain_http=False)
