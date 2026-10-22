"""Persist a synthetic history response through Capture and RAW without HTTP.

This is an offline-only ingress. It never calls a transport, schedules work,
or authorizes live Meta collection.
"""
from __future__ import annotations

from three_api_contract_executor import capture_record_from_response
from three_api_runtime_contract import (
    commit_raw_and_confirm, persist_capture_verified,
)


def ingest_offline_history_response(capture, raw_writer, request, response):
    if getattr(request, "phase", None) != "history":
        raise ValueError("History request required")
    if getattr(request, "history_offline_only", None) is not True:
        raise ValueError("Explicit offline history contract required")
    if response.request_id != request.request_id:
        raise ValueError("Response request mismatch")
    if not 200 <= response.http_status < 300:
        raise ValueError("Successful fixture response required")
    existing = capture.get_capture_for_request(request.request_id)
    if existing is not None:
        if existing.request.request_spec_digest != request.request_spec_digest:
            raise ValueError("Persisted request digest conflict")
        return commit_raw_and_confirm(capture, raw_writer, existing)
    if capture.get_intent_outcome(request.request_id) is not None:
        raise ValueError("Unresolved history intent; no replay")
    # Validate schema before creating a durable intent. A rejected fixture
    # must not leave a pending request that looks like an unknown HTTP outcome.
    record = capture_record_from_response(request, response)
    if capture.save_intent(request) is not True:
        raise ValueError("History intent already exists")
    evidence = capture.save_response_evidence(response)
    if evidence["classification"] != "safe":
        raise ValueError("Unsafe history response; no RAW commit")
    persist_capture_verified(capture, record)
    return commit_raw_and_confirm(capture, raw_writer, record)


def ingest_and_commit_offline_history_page(
        store, capture, raw_writer, request, response, *,
        oldest_post_at, terminal=False):
    """Persist one synthetic page and advance its history cursor only afterward."""
    if getattr(request, "phase", None) != "history":
        raise ValueError("History request required")
    if getattr(request, "history_offline_only", None) is not True:
        raise ValueError("Explicit offline history request required")
    # Check expected cursor before any Capture or RAW write.
    current = store.read(request.account_key)
    if (current is None or current["state"] != "continuation_ready"
            or not isinstance(current["cursor"], str)
            or not current["cursor"] or current["cursor"] != request.cursor):
        raise ValueError("Verified history anchor and matching cursor required")
    # The terminal flag and oldest timestamp must be consistent with the
    # verified response. Never allow a caller to mark an arbitrary page done.
    from datetime import datetime
    if type(terminal) is not bool:
        raise ValueError("Explicit terminal flag required")
    if oldest_post_at is not None and (not isinstance(oldest_post_at, datetime)
            or oldest_post_at.utcoffset() is None):
        raise ValueError("Timezone-aware oldest timestamp required")
    if terminal and getattr(response, "next_cursor", None) is not None:
        raise ValueError("Terminal history page cannot contain next cursor")
    receipt = ingest_offline_history_response(
        capture, raw_writer, request, response)
    from three_api_p50_history_commit import commit_persisted_history_page
    record = capture.get_capture_for_request(request.request_id)
    if record is None:
        raise ValueError("Missing durable history Capture after ingestion")
    decision = commit_persisted_history_page(
        store, capture, raw_writer, account_key=request.account_key,
        capture_id=record.capture_id, oldest_post_at=oldest_post_at,
        terminal=terminal)
    return {"raw_receipt": receipt, "history": decision}
