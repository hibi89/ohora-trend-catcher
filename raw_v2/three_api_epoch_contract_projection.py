"""Offline-only P3 daily-epoch projection from durable P1/P2 contract receipts.

This module NEVER creates/sends HTTP requests, writes RAW records, schedules
history, or opens a database. A caller must supply a verified CaptureJournal,
a ContractRawWriter over an isolated RAW v2 clone, and a P3 epoch store.

Only after BOTH durable Capture/response evidence and an independently re-read
RAW receipt (with the matching processed acknowledgement) are verified may the
P3 page ledger advance. This is an integration seam, not live authorization.
"""
from __future__ import annotations

from datetime import date
import sqlite3

from three_api_epochs import ThreeApiEpochStore, EpochError, _hash
from three_api_capture import CaptureJournal
from three_api_raw_writer import ContractRawWriter, RawWriteError
from three_api_runtime_contract import digest_sha256, validate_raw_receipt


class OfflineEpochContractError(ValueError):
    """Sanitized contract/proof failure; no media or cursor values exposed."""


class OfflineEpochContractProjector:
    def __init__(self, epochs: ThreeApiEpochStore,
                 capture: CaptureJournal, raw: ContractRawWriter):
        if not isinstance(epochs, ThreeApiEpochStore) or not isinstance(
                capture, CaptureJournal) or not isinstance(raw, ContractRawWriter):
            raise OfflineEpochContractError("Existing durable offline ports required")
        self.epochs = epochs
        self.capture = capture
        self.raw = raw

    def project_front_page(self, job_id):
        """Finish a bound daily P3 job using ONLY already-committed P1/P2 proof.

        This never calls a transport, retries a request, supplies an
        observation clock, repairs RAW, or marks unresolved outcomes as done.
        Already-bound previous-day responses can complete after KST rollover.
        """
        stage = "bound_job"
        try:
            job = self.epochs._job_row(job_id)
            if (job["epoch_kind"] != "daily" or job["phase"] != "daily_front"
                    or job["state"] not in ("bound", "done")):
                raise OfflineEpochContractError("Bound daily front proof required")
            binding = self.epochs.db.execute(
                "SELECT request_id,requested_cursor,request_spec_digest "
                "FROM request_bindings_v2 WHERE job_id=?", (job_id,)
            ).fetchone()
            if binding is None:
                raise OfflineEpochContractError("Durable P3 request binding required")
            request_id, cursor, spec_hash = tuple(binding)
            stage = "capture_reread"
            record = self.capture.get_capture_for_request(request_id)
            if record is None:
                raise OfflineEpochContractError("Durable capture missing")
            request = record.request
            epoch = self.epochs.db.execute(
                "SELECT day_kst,roster_digest FROM collection_epochs WHERE epoch_id=?",
                (job["epoch_id"],)
            ).fetchone()
            member = self.epochs.db.execute(
                "SELECT username FROM roster_members WHERE roster_digest=? "
                "AND account_key=?", (job["roster_digest"], job["account_key"])
            ).fetchone()
            if (epoch is None or member is None or request.phase != "front"
                    or request.day_kst != date.fromisoformat(epoch[0])
                    or request.roster_digest != epoch[1]
                    or request.account_key != job["account_key"]
                    or request.account_ordinal != job["ordinal"]
                    or request.username != member[0]
                    or request.lane != job["lane"]
                    or request.page != job["page_seq"]
                    or request.request_id != request_id
                    or request.cursor != cursor or request.request_spec.after != cursor
                    or request.request_spec.limit != 4
                    or _hash(request.request_spec.__dict__) != spec_hash):
                raise OfflineEpochContractError("Epoch and captured request differ")
            # An independent, first-retained safe response proves observation
            # clock/source evidence existed before success normalization.
            stage = "safe_response_reread"
            source = self.capture.get_response_evidence(request_id)
            if (source is None or source["classification"] != "safe"
                    or source["evidence_id"] != "response-" + request_id
                    or not source["body_digest"] or source["safe_body"] is None):
                raise OfflineEpochContractError("Retained safe response evidence missing")
            actual_at = record.observed_at.isoformat(timespec="microseconds").replace(
                "+00:00", "Z"
            )
            if (source["received_at"] != actual_at
                    or record.cursor_state.source_digest !=
                    digest_sha256(record.sanitized_response)
                    or record.cursor_state.current_cursor != cursor
                    or record.cursor_state.next_cursor is None
                    and len(record.normalized_posts) >= 4):
                raise OfflineEpochContractError("Response clock or cursor proof invalid")
            stage = "raw_receipt_reread"
            receipt = self.raw.get_raw_receipt(record.capture_id)
            if receipt is None:
                raise OfflineEpochContractError("No committed RAW receipt")
            validate_raw_receipt(record, receipt)
            confirmation = self.capture._db.execute(
                "SELECT processed_receipt FROM captures WHERE capture_id=?",
                (record.capture_id,)
            ).fetchone()
            if confirmation is None or confirmation[0] != receipt.raw_receipt_id:
                raise OfflineEpochContractError("Unconfirmed RAW acknowledgement")
            # Verify recorded RAW rows, not only the receipt object.
            stage = "raw_observations"
            rows = self.raw._db.execute(
                "SELECT post_id,like_count,comments_count,like_present,"
                "comments_present,observed_at FROM post_observations "
                "WHERE request_id=?", (request_id,)
            ).fetchall()
            expected = {p["id"]: p for p in record.normalized_posts}
            if len(rows) != len(expected) or {r[0] for r in rows} != set(expected):
                raise OfflineEpochContractError("RAW observations incomplete")
            for row in rows:
                item = expected[row[0]]
                if (row[1] != item.get("like_count")
                        or row[2] != item.get("comments_count")
                        or row[3] != int("like_count" in item)
                        or row[4] != int("comments_count" in item)
                        or row[5] != actual_at):
                    raise OfflineEpochContractError("RAW observation evidence differs")
                owner = self.raw._db.execute(
                    "SELECT account_key FROM posts WHERE post_id=?", (row[0],)
                ).fetchone()
                if owner is None or owner[0] != job["account_key"]:
                    raise OfflineEpochContractError("RAW account ownership differs")
            stage = "epoch_commit"
            return self.epochs.commit_daily_page(
                job_id, request_id, observed_at=record.observed_at,
                ordered_ids=[post["id"] for post in record.normalized_posts],
                next_cursor=record.cursor_state.next_cursor,
                terminal_basis=("continued" if
                    record.cursor_state.next_cursor is not None else
                    "verified_terminal"),
                source_evidence_id=source["evidence_id"],
                raw_receipt_id=receipt.raw_receipt_id,
                source_digest=record.cursor_state.source_digest,
            )
        except OfflineEpochContractError:
            raise
        except (EpochError, RawWriteError, sqlite3.Error, ValueError, KeyError, TypeError, AttributeError):
            # Static stage names aid incident diagnosis without logging IDs,
            # paging cursors, captions, URLs or response contents.
            raise OfflineEpochContractError(
                "Stored epoch/Capture/RAW proof rejected at " + stage
            ) from None
