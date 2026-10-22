"""P50 history checkpoint decisions; pure offline logic, no network or database.

History can advance only from an immutable, RAW-receipted page. The stored
cursor is opaque and never reconstructed from a URL. This helper does not
schedule HTTP, authorize retries, or alter the frozen 294-account engine.
"""
from __future__ import annotations

from datetime import date, datetime, timedelta, timezone


def history_checkpoint(*, account_key, current_cursor, next_cursor,
                       oldest_post_at, cutoff_day, raw_receipt_verified,
                       capture_verified, terminal, uncertain_http=False):
    """Return a conservative decision for one account's 365-day history.

    A missing timestamp is not evidence that the requested window is covered.
    A terminal page is authoritative only after both durable receipts.
    """
    if not isinstance(account_key, str) or not account_key.startswith("P50_"):
        raise ValueError("P50 account identity required")
    if not isinstance(cutoff_day, date) or isinstance(cutoff_day, datetime):
        raise ValueError("Explicit history cutoff date required")
    if current_cursor is not None and (not isinstance(current_cursor, str) or not current_cursor):
        raise ValueError("Invalid current cursor")
    if next_cursor is not None and (not isinstance(next_cursor, str) or not next_cursor):
        raise ValueError("Invalid next cursor")
    if oldest_post_at is not None and (
            not isinstance(oldest_post_at, datetime)
            or oldest_post_at.tzinfo is None
            or oldest_post_at.utcoffset() is None):
        raise ValueError("Timezone-aware oldest post timestamp required")
    if type(raw_receipt_verified) is not bool or type(capture_verified) is not bool:
        raise ValueError("Explicit receipt verification required")
    if type(terminal) is not bool or type(uncertain_http) is not bool:
        raise ValueError("Explicit terminal and HTTP state required")
    if uncertain_http:
        return {"state": "blocked_unknown_http", "next_cursor": current_cursor,
                "eligible_to_dispatch": False}
    if not (raw_receipt_verified and capture_verified):
        return {"state": "blocked_unverified_page", "next_cursor": current_cursor,
                "eligible_to_dispatch": False}
    if oldest_post_at is not None and oldest_post_at.astimezone(timezone(timedelta(hours=9))).date() < cutoff_day:
        return {"state": "window_covered", "next_cursor": None,
                "eligible_to_dispatch": False}
    if terminal:
        if next_cursor is not None:
            raise ValueError("Terminal page cannot have continuation cursor")
        return {"state": "terminal_verified", "next_cursor": None,
                "eligible_to_dispatch": False}
    if next_cursor is None:
        return {"state": "blocked_missing_cursor", "next_cursor": current_cursor,
                "eligible_to_dispatch": False}
    if next_cursor == current_cursor:
        return {"state": "blocked_repeated_cursor", "next_cursor": current_cursor,
                "eligible_to_dispatch": False}
    return {"state": "continuation_ready", "next_cursor": next_cursor,
            "eligible_to_dispatch": True}
