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
    existing = capture.get_capture_for_request(request.request_id)
    if existing is not None:
        if existing.request.request_spec_digest != request.request_spec_digest:
            raise ValueError("Persisted request digest conflict")
        return commit_raw_and_confirm(capture, raw_writer, existing)
    if capture.get_intent_outcome(request.request_id) is not None:
        raise ValueError("Unresolved history intent; no replay")
    if capture.save_intent(request) is not True:
        raise ValueError("History intent already exists")
    evidence = capture.save_response_evidence(response)
    if evidence["classification"] != "safe":
        raise ValueError("Unsafe history response; no RAW commit")
    record = capture_record_from_response(request, response)
    persist_capture_verified(capture, record)
    return commit_raw_and_confirm(capture, raw_writer, record)
