"""Offline receipt-backed P50 history anchor; no network or operational writes."""
from __future__ import annotations

from datetime import datetime, timezone

from three_api_runtime_contract import validate_raw_receipt


def anchor_latest_tail(store, journal, raw_writer, *, account_key, capture_id,
                       terminal=False, oldest_post_at=None):
    """Anchor only after independent persisted Capture and RAW verification."""
    record = journal.get_capture(capture_id)
    if record is None or record.capture_id != capture_id:
        raise ValueError("Missing durable Capture record")
    if record.request.account_key != account_key:
        raise ValueError("Capture belongs to a different account")
    if record.request.phase != "catchup" or record.request.page != 3:
        raise ValueError("Only verified third latest page may anchor history")
    raw_receipt = raw_writer.get_raw_receipt(capture_id)
    if raw_receipt is None:
        raise ValueError("Missing durable RAW receipt")
    validate_raw_receipt(record, raw_receipt)
    if not isinstance(raw_receipt.raw_receipt_id, str) or not raw_receipt.raw_receipt_id:
        raise ValueError("Invalid RAW receipt identity")
    if record.cursor_state.request_id != record.request.request_id:
        raise ValueError("Capture cursor source mismatch")
    if record.cursor_state.current_cursor != record.request.cursor:
        raise ValueError("Capture cursor does not match request")
    if record.cursor_state.page != record.request.page:
        raise ValueError("Capture page does not match request")
    if type(terminal) is not bool:
        raise ValueError("Explicit latest-tail terminal flag required")
    if terminal and record.cursor_state.next_cursor is not None:
        raise ValueError("Terminal latest-tail Capture contains continuation cursor")
    if not terminal and not record.cursor_state.next_cursor:
        raise ValueError("Missing verified continuation cursor")
    if (record.cursor_state.next_cursor is not None and
            (not isinstance(record.cursor_state.next_cursor, str)
             or not record.cursor_state.next_cursor
             or record.cursor_state.next_cursor == record.request.cursor)):
        raise ValueError("Invalid or repeated latest-tail continuation cursor")
    # The history cutoff must be based on persisted posts, never caller input.
    timestamps = []
    for post in record.normalized_posts:
        value = post.get("timestamp")
        if not isinstance(value, str):
            raise ValueError("Latest-tail post missing timestamp evidence")
        try:
            parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
        except ValueError as exc:
            raise ValueError("Invalid persisted latest-tail timestamp") from exc
        if parsed.utcoffset() is None:
            raise ValueError("Latest-tail timestamp must include timezone")
        timestamps.append(parsed.astimezone(timezone.utc))
    actual_oldest = min(timestamps) if timestamps else None
    if oldest_post_at is not None:
        if (not isinstance(oldest_post_at, datetime)
                or oldest_post_at.utcoffset() is None
                or actual_oldest is None
                or oldest_post_at.astimezone(timezone.utc) != actual_oldest):
            raise ValueError("Latest-tail oldest timestamp differs from Capture")
    elif actual_oldest is not None:
        raise ValueError("Missing latest-tail oldest timestamp")
    if actual_oldest is None and not terminal:
        raise ValueError("Nonterminal latest-tail Capture has no dated posts")
    return store.anchor_verified_latest_tail(
        account_key=account_key, next_cursor=record.cursor_state.next_cursor,
        capture_id=capture_id, raw_receipt_id=raw_receipt.raw_receipt_id,
        raw_receipt_verified=True, capture_verified=True,
        terminal=terminal, oldest_post_at=oldest_post_at)
