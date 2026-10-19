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
    # Coordinator.lease returns the pre-update row with the newly minted lease.
    # The database is authoritative for actual lease validity at commit time.
    if job.get("state") not in ("pending", "leased") or not job.get("lease_id"):
        raise AdapterGuardError("A valid lease token is required")
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


def execute_guarded(job, request, evidence, executor, *, bindings=None, now=None):
    """Offline-only dispatch bridge; durable job reservation is mandatory.

    A basic job/cursor validation alone is NOT enough: an expired lease could
    otherwise produce a fresh request_id and duplicate an ambiguous HTTP
    attempt. Only JobRequestBindingStore.dispatch_once may authorize the
    first send. Subsequent dispatches fail closed, regardless of lease token.
    """
    from three_api_job_binding import JobRequestBindingStore

    if not isinstance(bindings, JobRequestBindingStore):
        raise AdapterGuardError("Durable job request binding is required")
    validate_job_request(job, request, evidence)
    return bindings.dispatch_once(job, request, evidence, executor, now=now)


def verify_raw_completion(request, receipt, capture_port, raw_port):
    """Read-only gate before any coordinator page-evidence acknowledgement.

    Verify the durable capture, matching RAW receipt, and confirmed intent.
    This does NOT commit coordinator evidence or repair expired leases.
    """
    from three_api_runtime_contract import validate_raw_receipt

    if receipt is None or not getattr(request, "request_id", None):
        raise AdapterGuardError("Missing RAW completion evidence")
    try:
        record = capture_port.get_capture_for_request(request.request_id)
        if record is None or record.request.request_id != request.request_id:
            raise AdapterGuardError("Durable capture unavailable")
        persisted = raw_port.get_raw_receipt(record.capture_id)
        if persisted is None:
            raise AdapterGuardError("Persisted RAW receipt unavailable")
        validate_raw_receipt(record, receipt)
        validate_raw_receipt(record, persisted)
        if (persisted.raw_receipt_id != receipt.raw_receipt_id
                or persisted.status not in ("committed", "already_committed") ):
            raise AdapterGuardError("RAW completion evidence mismatch")
    except AdapterGuardError:
        raise
    except (AttributeError, TypeError, ValueError):
        raise AdapterGuardError("Invalid RAW completion evidence") from None
    return persisted.raw_receipt_id


def commit_verified_page(job, request, receipt, capture_port, raw_port,
                         evidence, *, response, now=None):
    """Offline-only bridge: verify RAW durability before atomic page completion.

    The response must be an independently verified normalized page edge.
    This bridge never makes HTTP requests, retries, or writes production RAW.
    A stale lease fails in PageEvidenceStore.commit_page.
    """
    validate_job_request(job, request, evidence)
    receipt_id = verify_raw_completion(request, receipt, capture_port, raw_port)
    if not callable(getattr(evidence, "commit_page", None)):
        raise AdapterGuardError("Page evidence commit unavailable")
    return evidence.commit_page(
        job["id"], job["lease_id"], receipt_id, response,
        requested_cursor=request.cursor, now=now,
    )
