"""Offline-only preflight guard for a coordinator job and contract request.

This module performs no network access, credential reads, or database writes.
It is a fail-closed boundary, not a live adapter or a completion mechanism.
"""
from __future__ import annotations


class AdapterGuardError(ValueError):
    """Sanitized mapping rejection without leaking cursors or credentials."""


def validate_job_request(job, request, evidence):
    """Check an already-built RequestEnvelope before any transport is allowed.

    The caller must separately verify the durable request intent and RAW
    receipt when reconciling an expired lease. Passing this guard never
    authorizes re-sending a persisted intent.
    """
    if not isinstance(job, dict) or not isinstance(job.get("id"), int):
        raise AdapterGuardError("Invalid coordinator job")
    if job.get("state") not in ("pending", "leased"):
        raise AdapterGuardError("Job is not dispatch eligible")
    if job.get("state") != "leased" or not job.get("lease_id"):
        raise AdapterGuardError("A valid active lease is required")
    page = job.get("page")
    ordinal = job.get("account_ordinal")
    lane = job.get("lane")
    if (type(page) is not int or page not in (1, 2, 3)
            or type(ordinal) is not int or not 1 <= ordinal <= 294
            or lane not in ("A", "B", "C")):
        raise AdapterGuardError("Invalid job identity")
    expected_phase = "front" if page == 1 else "catchup"
    if job.get("phase") != expected_phase:
        raise AdapterGuardError("Invalid daily continuation phase")
    if (request.account_ordinal != ordinal or request.page != page
            or request.lane != lane or request.phase != expected_phase):
        raise AdapterGuardError("Job and request identity mismatch")
    try:
        required = evidence.required_cursor(ordinal, page)
    except (ValueError, KeyError):
        raise AdapterGuardError("Predecessor page evidence missing") from None
    if request.cursor != required or request.request_spec.after != required:
        raise AdapterGuardError("Request cursor differs from durable evidence")
    return True
