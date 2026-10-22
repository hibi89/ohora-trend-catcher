"""Bounded serial ingestion of already supplied P50 history fixtures.

No HTTP transport, scheduler, credentials, or operational database selection.
A failed page stops the batch; earlier durable commits remain recoverable.
"""
from __future__ import annotations

from datetime import datetime
from three_api_p50_history_offline_ingress import ingest_and_commit_offline_history_page


def ingest_offline_history_batch(store, capture, raw_writer, pages, *, max_pages=50):
    if type(max_pages) is not int or not 1 <= max_pages <= 50:
        raise ValueError("History batch budget must be 1..50")
    pages = tuple(pages)
    if len(pages) > max_pages:
        raise ValueError("History fixture batch exceeds explicit budget")
    seen = set()
    expected_cursors = {}
    first_cursors = {}
    for page in pages:
        if not isinstance(page, dict) or set(page) != {
                "request", "response", "oldest_post_at", "terminal"}:
            raise ValueError("Invalid history fixture page")
        request, response = page["request"], page["response"]
        if (getattr(request, "phase", None) != "history"
                or getattr(request, "history_offline_only", None) is not True
                or getattr(request, "request_spec", None) is None
                or request.request_spec.limit != 4
                or not isinstance(request.cursor, str) or not request.cursor
                or response.request_id != request.request_id
                or not 200 <= response.http_status < 300):
            raise ValueError("Invalid offline history fixture identity")
        account_key = request.account_key
        if account_key not in first_cursors:
            first_cursors[account_key] = request.cursor
        if account_key in expected_cursors and request.cursor != expected_cursors[account_key]:
            raise ValueError("History fixture chain cursor mismatch")
        if account_key in expected_cursors and expected_cursors[account_key] is None:
            raise ValueError("History fixture follows terminal account page")
        expected_cursors[account_key] = response.next_cursor
        if request.request_id in seen:
            raise ValueError("Duplicate history fixture request")
        seen.add(request.request_id)
        if type(page["terminal"]) is not bool:
            raise ValueError("Explicit terminal flag required")
        oldest = page["oldest_post_at"]
        if oldest is not None and (
                not isinstance(oldest, datetime) or oldest.utcoffset() is None):
            raise ValueError("Timezone-aware oldest timestamp required")
        if not page["terminal"] and oldest is None:
            raise ValueError("Nonterminal page requires oldest timestamp")
        if page["terminal"] and response.next_cursor is not None:
            raise ValueError("Terminal fixture cannot have next cursor")
        if not page["terminal"] and response.next_cursor is None:
            raise ValueError("Nonterminal fixture requires continuation cursor")
    # Verify every account against persisted state before the first RAW write.
    for account_key, cursor in first_cursors.items():
        row = store.read(account_key)
        if (row is None or row["state"] != "continuation_ready"
                or row["cursor"] != cursor):
            raise ValueError("History batch starts from unverified persisted cursor")
    completed = []
    for page in pages:
        request = page["request"]
        result = ingest_and_commit_offline_history_page(
            store, capture, raw_writer, request, page["response"],
            oldest_post_at=page["oldest_post_at"], terminal=page["terminal"])
        completed.append({
            "account_key": request.account_key,
            "request_id": request.request_id,
            "state": result["history"]["state"],
        })
    return {"mode": "P50_HISTORY_OFFLINE_BATCH",
            "completed": completed, "pages_committed": len(completed),
            "network_requests": 0}
