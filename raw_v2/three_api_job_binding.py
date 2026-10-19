"""Offline-only durable coordinator-job-to-request binding.

A reservation is committed to the coordinator SQLite database BEFORE any
request can be passed to the contract executor. After a crash, the same job is
NEVER dispatched a second time, even with a different request ID or lease.
A persisted capture can be recovered into RAW through a separate, no-HTTP
operation. A missing capture or ambiguous request outcome blocks for manual
review. This module does not enable a live Meta dispatcher.
"""
from __future__ import annotations

import re

from three_api_adapter_guard import validate_job_request
from three_api_coordinator import stamp, utcnow
from three_api_runtime_contract import (
    RequestEnvelope, commit_raw_and_confirm, digest_sha256, utc,
)


_REQUEST_ID = re.compile(r"req-[0-9a-f]{64}\Z")


class JobBindingError(RuntimeError):
    """Safe, non-secret rejection of a duplicate or invalid job dispatch."""


class JobRequestBindingStore:
    """Reserve an immutable request against one coordinator job for all leases.

    This must use the SAME isolated SQLite connection as Coordinator and
    PageEvidenceStore. There is no cross-database transaction with Capture/RAW;
    a crash between reservation and Capture.save_intent blocks, not retries.
    """

    def __init__(self, coordinator):
        self.coordinator = coordinator
        self.db = coordinator.db
        self.db.execute("""
            CREATE TABLE IF NOT EXISTS job_request_bindings (
                job_id INTEGER PRIMARY KEY REFERENCES jobs(id),
                request_id TEXT NOT NULL UNIQUE,
                request_digest TEXT NOT NULL,
                reserved_at TEXT NOT NULL
            )
        """)

    def lookup(self, job_id):
        if type(job_id) is not int:
            raise JobBindingError("Invalid job identity")
        row = self.db.execute(
            "SELECT job_id,request_id,request_digest,reserved_at "
            "FROM job_request_bindings WHERE job_id=?", (job_id,),
        ).fetchone()
        return None if row is None else dict(row)

    def _check_current_lease(self, job, now):
        row = self.db.execute(
            """SELECT id,account_ordinal,page,lane,phase FROM jobs
               WHERE id=? AND lease_id=? AND state='leased'
                 AND leased_until>?""",
            (job["id"], job["lease_id"], stamp(now)),
        ).fetchone()
        if row is None or any(
            row[key] != job.get(key)
            for key in ("account_ordinal", "page", "lane", "phase")
        ):
            raise JobBindingError("Missing, stale, or expired coordinator lease")

    def reserve(self, job, request, evidence, *, now=None):
        """Atomic first-only reservation; existing bindings NEVER authorize send.

        Call before executor.execute, including before Capture.save_intent.
        If the process crashes after this commit, operator recovery is required.
        """
        if not isinstance(request, RequestEnvelope):
            raise JobBindingError("Validated RequestEnvelope required")
        if not _REQUEST_ID.fullmatch(request.request_id) or request.retry_of is not None:
            raise JobBindingError("Only initial immutable request IDs allowed")
        now = utc(now or utcnow())
        self.db.execute("BEGIN IMMEDIATE")
        try:
            validate_job_request(job, request, evidence)
            self._check_current_lease(job, now)
            if self.lookup(job["id"]) is not None:
                raise JobBindingError("Job already has a durable request binding")
            self.db.execute(
                """INSERT INTO job_request_bindings
                   (job_id,request_id,request_digest,reserved_at)
                   VALUES (?,?,?,?)""",
                (job["id"], request.request_id, digest_sha256(request), stamp(now)),
            )
            self.db.execute("COMMIT")
        except BaseException:
            self.db.execute("ROLLBACK")
            raise
        return request.request_id

    def _verify_bound(self, job, request, evidence, now):
        if not isinstance(request, RequestEnvelope):
            raise JobBindingError("Validated RequestEnvelope required")
        now = utc(now or utcnow())
        validate_job_request(job, request, evidence)
        self._check_current_lease(job, now)
        saved = self.lookup(job["id"])
        if (saved is None or saved["request_id"] != request.request_id or
                saved["request_digest"] != digest_sha256(request)):
            raise JobBindingError("Request differs from immutable job binding")

    def dispatch_once(self, job, request, evidence, executor, *, now=None):
        """Offline executor bridge: reserve once before any transport attempt.

        No automatic retries, polling, lease extension or scheduler. An
        executor without .execute is rejected BEFORE reservation.
        """
        if not callable(getattr(executor, "execute", None)):
            raise JobBindingError("Invalid contract executor")
        self.reserve(job, request, evidence, now=now)
        return executor.execute(request)

    def recover_captured(self, job, request, evidence, capture, raw_writer, *,
                         now=None):
        """Recover only a durable capture for the ORIGINAL bound request.

        This method never calls transport or Capture.save_intent. Missing
        capture fails closed even if the first reservation preceded HTTP.
        RAW commit is idempotent by capture digest; page completion separately
        requires a fresh valid lease and verified page edge.
        """
        self._verify_bound(job, request, evidence, now)
        record = capture.get_capture_for_request(request.request_id)
        if record is None:
            raise JobBindingError("Bound request has no durable capture; no resend")
        if digest_sha256(record.request) != digest_sha256(request):
            raise JobBindingError("Captured request differs from durable binding")
        return commit_raw_and_confirm(capture, raw_writer, record)
