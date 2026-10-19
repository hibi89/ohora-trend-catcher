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
    if not isinstance(job, dict) or type(job.get("id")) is not int:
        raise AdapterGuardError("Invalid coordinator job")
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
    try:
        matched = (request.account_ordinal == ordinal and request.page == page
                   and request.lane == lane and request.phase == expected_phase)
        cursor = request.cursor
        spec_cursor = request.request_spec.after
    except (AttributeError, TypeError):
        raise AdapterGuardError("Invalid request envelope") from None
    if not matched:
        raise AdapterGuardError("Job and request identity mismatch")
    try:
        required = evidence.required_cursor(ordinal, page)
    except (ValueError, KeyError, AttributeError, TypeError):
        raise AdapterGuardError("Predecessor page evidence missing") from None
    if cursor != required or spec_cursor != required:
        raise AdapterGuardError("Request cursor differs from durable evidence")
    return True


def execute_guarded(job, request, evidence, executor):
    """Explicit one-request bridge; never leases, retries, or commits page evidence.

    The executor must be a ThreeApiContractExecutor-compatible object.
    Its durable intent logic determines whether an existing request may be
    recovered from capture; this bridge never authorizes resending it.
    """
    validate_job_request(job, request, evidence)
    if not callable(getattr(executor, "execute", None)):
        raise AdapterGuardError("Invalid contract executor")
    return executor.execute(request)
