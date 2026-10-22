"""Durable RAW-backed page evidence adapter for the three-API coordinator.

Stores cursor evidence in the isolated coordinator database ONLY AFTER an
original bound capture and committed RAW receipt have been checked. The
caller owns all already-open connections. No HTTP, tokens, scheduling, legacy
database access or operational authorization is provided by this module.

Three stores cannot commit atomically together; a failure after RAW commit
is recovered from the existing immutable Capture/RAW records with NO resend.
"""
from __future__ import annotations

from three_api_capture import CaptureJournal
from three_api_evidence import PageEvidenceStore
from three_api_raw_writer import ContractRawWriter
from three_api_runtime_contract import digest_sha256, validate_raw_receipt


class VerifiedPageEvidenceError(ValueError):
    """Fail-closed error that never includes account IDs, cursors or payload."""


class VerifiedRawPageEvidenceStore(PageEvidenceStore):
    """A coordinator evidence port backed by verified Capture and RAW v2.

    This port deliberately cannot stage arbitrary caller-supplied posts.
    It does not grant access to live Meta by itself.
    """

    def __init__(self, coordinator, capture, raw):
        if type(capture) is not CaptureJournal or type(raw) is not ContractRawWriter:
            raise VerifiedPageEvidenceError("Original durable Capture/RAW ports required")
        super().__init__(coordinator)
        self.capture = capture
        self.raw = raw

    def commit_staged_page(self, *args, **kwargs):
        raise VerifiedPageEvidenceError("Staging-only commit is not allowed")

    def commit_page(self, job_id, lease_id, receipt, response,
                    requested_cursor=None, now=None, staged_posts=None):
        if staged_posts is not None:
            raise VerifiedPageEvidenceError("Staging posts are not durable RAW")
        if type(job_id) is not int or not isinstance(lease_id, str):
            raise VerifiedPageEvidenceError("Invalid coordinator lease identity")
        bound = self.db.execute(
            "SELECT b.request_id,b.request_digest,j.account_ordinal,j.page,"
            "j.lane,j.phase FROM job_request_bindings b "
            "JOIN jobs j ON j.id=b.job_id WHERE b.job_id=?", (job_id,)
        ).fetchone()
        if bound is None:
            raise VerifiedPageEvidenceError("Immutable request binding required")
        try:
            record = self.capture.get_capture_for_request(bound["request_id"])
            if record is None:
                raise VerifiedPageEvidenceError("Original Capture is unavailable")
            request = record.request
            if (request.request_id != bound["request_id"]
                    or digest_sha256(request) != bound["request_digest"]
                    or (request.account_ordinal, request.page,
                        request.lane, request.phase)
                    != (bound["account_ordinal"], bound["page"],
                        bound["lane"], bound["phase"])
                    or requested_cursor != request.cursor):
                raise VerifiedPageEvidenceError("Captured request differs from binding")
            persisted = self.raw.get_raw_receipt(record.capture_id)
            if persisted is None:
                raise VerifiedPageEvidenceError("Committed RAW receipt unavailable")
            validate_raw_receipt(record, persisted)
            if (persisted.raw_receipt_id != receipt
                    or persisted.status not in ("committed", "already_committed")):
                raise VerifiedPageEvidenceError("RAW receipt mismatch")
            expected = {"data": list(record.normalized_posts)}
            cursor = record.cursor_state.next_cursor
            if cursor is not None:
                expected["paging"] = {
                    "next": "durable-capture-next-present",
                    "cursors": {"after": cursor},
                }
            if response != expected:
                raise VerifiedPageEvidenceError("Page differs from immutable Capture")
        except VerifiedPageEvidenceError:
            raise
        except (TypeError, ValueError, AttributeError, KeyError):
            raise VerifiedPageEvidenceError("Durable RAW page verification failed") from None

        # The base commit validates lease, predecessor and cursor chain in a
        # single coordinator transaction. RAW was committed beforehand.
        return super().commit_page(
            job_id, lease_id, receipt, expected,
            requested_cursor=requested_cursor, now=now,
        )
