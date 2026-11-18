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
import json
import sqlite3

from three_api_epochs import ThreeApiEpochStore, EpochError, _hash
from three_api_capture import CaptureJournal
from three_api_raw_writer import ContractRawWriter, RawWriteError
from three_api_observation_versions import _post_components
from three_api_runtime_contract import (
    digest_sha256, sanitize_meta_response, validate_raw_receipt,
)


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
        """Project one stored daily-front P1/P2 proof, never send HTTP."""
        return self._project_verified_page(job_id, history=False)

    def project_history_page(self, job_id):
        """Project OFFLINE-only history proof into an already-bound P3 job.

        This never authorizes the Meta transport, never schedules history,
        never writes a new capture/RAW, and never verifies a 365/730 cutoff.
        """
        return self._project_verified_page(job_id, history=True)

    def _project_verified_page(self, job_id, *, history):
        """Require durable P1 response, Capture, RAW v2, ACK and P2 state proof."""
        stage = "bound_job"
        try:
            job = self.epochs._job_row(job_id)
            expected_kind = "history" if history else "daily"
            expected_phase = "history" if history else "daily_front"
            if (job["epoch_kind"] != expected_kind or
                    job["phase"] != expected_phase or
                    job["state"] not in ("bound", "done")):
                raise OfflineEpochContractError("Bound epoch page proof required")
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
            if (epoch is None or member is None
                    or request.phase != ("history" if history else "front")
                    or (history and request.history_offline_only is not True)
                    or (not history and request.day_kst != date.fromisoformat(epoch[0]))
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
                    or source["http_status"] != record.response_http_status
                    or not 200 <= source["http_status"] < 300
                    # Retained pre-normalization response must actually be
                    # the provenance of this successful CaptureRecord. Two
                    # individually valid digests do not prove linkage.
                    or sanitize_meta_response(source["safe_body"]) !=
                    record.sanitized_response
                    or record.cursor_state.source_digest !=
                    digest_sha256(record.sanitized_response)
                    or record.cursor_state.current_cursor != cursor
                    or (record.cursor_state.next_cursor is None
                        and len(record.normalized_posts) >= 4)):
                raise OfflineEpochContractError(
                    "Retained response and CaptureRecord disagree"
                )
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
            # Legacy RAW field_observations are an independent, immutable
            # per-request audit trail. An intact P2 metric state must not
            # excuse a missing/changed view_count observation when P3 commits.
            # Historical requests never asked for views and recorded
            # view_count=not_tested; never manufacture zeros on replay.
            stage = "metric_field_observations"
            actual_fields = self.raw._db.execute(
                "SELECT account_key,media_type,field_name,status,item_id "
                "FROM field_observations WHERE request_id=? "
                "AND resource_type='posts' AND field_name IN "
                "('like_count','comments_count','view_count')",
                (request_id,),
            ).fetchall()
            expected_fields = [
                (job["account_key"], post.get("media_type") or "", field,
                 record.post_field_status[post["id"]].get(field, "not_tested"),
                 post["id"])
                for post in record.normalized_posts
                for field in ("like_count", "comments_count", "view_count")
            ]
            if sorted(map(tuple, actual_fields)) != sorted(expected_fields):
                raise OfflineEpochContractError(
                    "RAW metric field observations incomplete"
                )
            # P2 append-only query/post checks are part of the same RAW
            # transaction as the receipt. An intact receipt alone must not
            # excuse missing structure, stale state, or deleted media rows.
            stage = "versioned_raw_evidence"
            query = self.raw._db.execute(
                "SELECT evidence_id,capture_id,account_key,checked_at,"
                "outcome,returned_count,quality_status FROM query_observations "
                "WHERE request_id=?", (request_id,)
            ).fetchone()
            if (query is None or tuple(query) != (
                    source["evidence_id"], record.capture_id,
                    job["account_key"], actual_at, "success",
                    len(record.normalized_posts), "verified")):
                raise OfflineEpochContractError(
                    "Versioned RAW query proof incomplete"
                )
            checks = self.raw._db.execute(
                "SELECT post_id,account_key,observed_at,"
                "content_state_id,metrics_state_id,structure_state_id,"
                "locator_state_id FROM post_checks WHERE request_id=?",
                (request_id,)
            ).fetchall()
            if len(checks) != len(expected) or {row[0] for row in checks} != set(expected):
                raise OfflineEpochContractError(
                    "Versioned RAW post checks incomplete"
                )
            for check in checks:
                post = expected[check[0]]
                if check[1] != job["account_key"] or check[2] != actual_at:
                    raise OfflineEpochContractError(
                        "Versioned RAW post identity differs"
                    )
                pieces = _post_components(record, post)
                for component, state_id in zip(
                        ("content", "metrics", "structure", "locator"),
                        check[3:]):
                    actual_state = self.raw._db.execute(
                        "SELECT post_id,component,full_values_json,"
                        "field_states_json FROM post_states WHERE state_id=?",
                        (state_id,)
                    ).fetchone()
                    values, states = pieces[component]
                    expected_state = (
                        post["id"], component,
                        json.dumps(values, ensure_ascii=False, sort_keys=True,
                                   separators=(",", ":")),
                        json.dumps(states, ensure_ascii=False, sort_keys=True,
                                   separators=(",", ":")),
                    )
                    if actual_state is None or tuple(actual_state) != expected_state:
                        raise OfflineEpochContractError(
                            "Versioned RAW post state differs"
                        )
                children = post.get("children") or []
                if post.get("media_type") == "CAROUSEL_ALBUM":
                    members = self.raw._db.execute(
                        "SELECT position,post_id,media_id,media_type "
                        "FROM media_structure_members WHERE structure_state_id=? "
                        "ORDER BY position",(check[5],)
                    ).fetchall()
                    if [tuple(row) for row in members] != [
                            (position, post["id"], child["id"], child.get("media_type"))
                            for position,child in enumerate(children)]:
                        raise OfflineEpochContractError(
                            "Per-observation carousel order differs"
                        )
                else:
                    children = [post]
                for child in children:
                    media = self.raw._db.execute(
                        "SELECT post_id,account_key,media_role FROM post_media "
                        "WHERE media_id=?", (child["id"],)
                    ).fetchone()
                    role = ("carousel_child" if
                            post.get("media_type") == "CAROUSEL_ALBUM" else "single")
                    if media is None or tuple(media) != (
                            post["id"],job["account_key"],role):
                        raise OfflineEpochContractError(
                            "RAW media ownership or identity differs"
                        )
            stage = "epoch_commit"
            fields = dict(
                observed_at=record.observed_at,
                ordered_ids=[post["id"] for post in record.normalized_posts],
                next_cursor=record.cursor_state.next_cursor,
                terminal_basis=("continued" if
                    record.cursor_state.next_cursor is not None else
                    "verified_terminal"),
                source_evidence_id=source["evidence_id"],
                raw_receipt_id=receipt.raw_receipt_id,
                source_digest=record.cursor_state.source_digest,
            )
            if history:
                return self.epochs.commit_history_page(
                    job_id, request_id, **fields
                )
            return self.epochs.commit_daily_page(job_id, request_id, **fields)
        except OfflineEpochContractError:
            raise
        except (EpochError, RawWriteError, sqlite3.Error, ValueError, KeyError, TypeError, AttributeError):
            # Static stage names aid incident diagnosis without logging IDs,
            # paging cursors, captions, URLs or response contents.
            raise OfflineEpochContractError(
                "Stored epoch/Capture/RAW proof rejected at " + stage
            ) from None
