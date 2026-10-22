"""Offline-only Meta paging evidence validator.

Do not treat logical page numbers as offsets. Caller must persist validated
edges transactionally with collected page data before marking a job complete.
This module performs no network or database writes.
"""
from __future__ import annotations

import re

CURSOR = re.compile(r"[A-Za-z0-9_=+/\-]{1,4096}\Z")


def validate_page_edge(response, *, requested_cursor=None, prior_cursors=()):
    """Return the next cursor and terminal status, or fail closed.

    A full page with no usable cursor is ambiguous; never advance it.
    """
    if not isinstance(response, dict) or not isinstance(response.get("data"), list):
        raise ValueError("Invalid Meta media response")
    if requested_cursor is not None and (
        not isinstance(requested_cursor, str) or not CURSOR.fullmatch(requested_cursor)
    ):
        raise ValueError("Invalid requested cursor")
    seen = set()
    for cursor in prior_cursors:
        if not isinstance(cursor, str) or not CURSOR.fullmatch(cursor):
            raise ValueError("Invalid prior cursor")
        if cursor in seen:
            raise ValueError("Repeated prior cursor")
        seen.add(cursor)
    if requested_cursor is not None and requested_cursor in seen:
        raise ValueError("Requested cursor already consumed")
    paging = response.get("paging", {})
    if not isinstance(paging, dict):
        raise ValueError("Invalid paging object")
    cursors = paging.get("cursors", {})
    if not isinstance(cursors, dict):
        raise ValueError("Invalid paging cursors")
    after = cursors.get("after")
    if after is not None and (not isinstance(after, str) or not CURSOR.fullmatch(after)):
        raise ValueError("Invalid response cursor")
    if after and (after == requested_cursor or after in seen):
        raise ValueError("Cursor cycle detected")
    next_url = paging.get("next")
    if next_url is not None and not isinstance(next_url, str):
        raise ValueError("Invalid next URL")
    if next_url and not after:
        raise ValueError("Next page without usable cursor")
    record_count = len(response["data"])
    if record_count == 0 and after:
        raise ValueError("Empty page carries an unverified next cursor")
    if next_url:
        status = "cursor_candidate"
    elif after:
        # Business Discovery can return a cursor without a paging.next URL.
        # Preserve it as a candidate; only a follow-up request can prove more
        # data exists, and a short page without a cursor is the terminal proof.
        status = "cursor_candidate"
    elif record_count < 4:
        status = "terminal_verified"
    else:
        status = "pagination_ambiguous"
    return {
        "next_cursor": after if status == "cursor_candidate" else None,
        "terminal": status == "terminal_verified",
        "pagination_status": status,
        "records": len(response["data"]),
        "verified_from_response": True,
    }
