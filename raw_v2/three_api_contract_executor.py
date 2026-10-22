"""Crash-safe single-request 3-API contract executor.

The executor accepts injected ports and never creates an HTTP sender, reads a
credential file, starts a schedule, or chooses an operational database. Its
transport performs at most one attempt. Unknown outcomes stop for manual
reconciliation; persisted captures recover to RAW without another HTTP call.
"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Mapping
import json

from three_api_meta_normalizer import normalize_meta_page
from three_api_runtime_contract import (
    CONTRACT_VERSION, ContractError, FailureEvidence, TransportFailure,
    capture_from_response, commit_raw_and_confirm, digest_sha256,
    persist_capture_verified, sanitize_meta_response,
)


class ContractExecutionError(RuntimeError):
    """Safe runner error that never includes payloads, tokens, or cursors."""


def request_envelope_id(*, run_id: str, roster_digest: str, account_key: str,
                        lane: str, phase: str, page: int, cursor: str | None,
                        request_spec_digest: str, retry_of: str | None = None,
                        retry_nonce: str | None = None) -> str:
    """Stable id for one request intent; approved retries must have a new nonce."""
    if retry_of is None and retry_nonce is not None:
        raise ValueError("Retry nonce requires retry_of")
    if retry_of is not None and (not isinstance(retry_nonce, str) or not retry_nonce.strip()):
        raise ValueError("A manual retry requires a fresh retry nonce")
    seed = digest_sha256({
        "run_id": run_id, "roster_digest": roster_digest,
        "account_key": account_key, "lane": lane, "phase": phase,
        "page": page, "cursor": cursor, "request_spec_digest": request_spec_digest,
        "retry_of": retry_of, "retry_nonce": retry_nonce,
    })
    return "req-" + seed


def capture_record_from_response(request, response):
    """Normalize one successful response and bind identity, cursor and digest."""
    if response.request_id != request.request_id:
        raise ContractError(CONTRACT_VERSION, "invalid_response", "account",
                            "Response request identity mismatch", request.request_id)
    try:
        normalized = normalize_meta_page(
            response.payload, requested_cursor=request.request_spec.after,
            allow_partial_children=True)
        paging = normalized.get("paging", {})
        cursors = paging.get("cursors", {}) if isinstance(paging, Mapping) else {}
        next_cursor = cursors.get("after") if isinstance(cursors, Mapping) else None
        if next_cursor != response.next_cursor:
            raise ValueError("Response cursor does not match normalized payload")
        root = response.payload.get("business_discovery", response.payload)
        if not isinstance(root, Mapping):
            raise ValueError("Invalid target account envelope")
        requested_account_fields = frozenset(
            field for field in ("followers_count", "media_count")
            if field in request.request_spec.account_fields)
        account = {key: root[key] for key in ("id", "username", "followers_count", "media_count")
                   if key in root and (key in ("id", "username") or key in requested_account_fields)}
        posts = normalized["data"]
        raw_media = root.get("media", root)
        source_posts = raw_media.get("data", []) if isinstance(raw_media, Mapping) else []
        raw_posts_by_id = {p.get("id"): p for p in source_posts if isinstance(p, Mapping)}
        carousel = {}
        for post in posts:
            pid = post["id"]
            if post.get("media_type") != "CAROUSEL_ALBUM":
                carousel[pid] = "not_applicable"
            elif not isinstance(post.get("children"), list):
                carousel[pid] = "unknown"
            else:
                original = raw_posts_by_id.get(pid, {}).get("children", {})
                paging = original.get("paging", {}) if isinstance(original, Mapping) else {}
                carousel[pid] = ("partial" if isinstance(paging, Mapping) and
                                 (paging.get("next") or paging.get("has_next"))
                                 else "complete")
        sanitized = sanitize_meta_response(response.payload)
        source_digest = digest_sha256(sanitized)
        requested_post_fields = frozenset(
            field for field in ("like_count", "comments_count")
            if field in request.request_spec.media_fields.split(","))
        return capture_from_response(
            request, response, capture_id="cap-" + request.request_id,
            normalized_posts=posts, account_observation=account,
            account_fields_tested=requested_account_fields,
            carousel_completeness=carousel, source_digest=source_digest)
    except ContractError:
        raise
    except (ValueError, TypeError, KeyError, AttributeError):
        raise ContractError(CONTRACT_VERSION, "invalid_response", "account",
                            "Response rejected by offline schema validation",
                            request.request_id) from None


class ThreeApiContractExecutor:
    """Execute or recover one request through Transport/Capture/RAW ports."""

    def __init__(self, transport, capture, raw_writer, *, clock=None):
        self.transport = transport
        self.capture = capture
        self.raw = raw_writer
        self.clock = clock or (lambda: datetime.now(timezone.utc))

    def _record_failure(self, request, category, scope, outcome, message, status=None):
        failure = FailureEvidence(
            CONTRACT_VERSION, request.request_id, request.run_id, category,
            scope, outcome, self.clock(), message, status)
        self.capture.save_failure(failure)

    def execute(self, request):
        """Persist intent, make one transport attempt, commit capture then RAW."""
        # History's explicit offline-only envelope is ONLY for ingesting an
        # already-supplied synthetic response through a no-sender adapter.
        # Do not even persist an HTTP intent here.
        if (getattr(request, "phase", None) == "history"
                and getattr(request, "history_live_approved", False) is not True):
            raise ContractExecutionError(
                "Offline history fixture cannot enter live transport"
            )
        existing_capture = self.capture.get_capture_for_request(request.request_id)
        if existing_capture is not None:
            if existing_capture.request.request_spec_digest != request.request_spec_digest:
                raise ContractExecutionError("Request identity conflict")
            return commit_raw_and_confirm(self.capture, self.raw, existing_capture)

        outcome = self.capture.get_intent_outcome(request.request_id)
        if outcome is not None:
            if outcome == "pending":
                self.capture.mark_outcome_unknown(request.request_id)
                self._record_failure(request, "outcome_unknown", "account",
                    "outcome_unknown", "Persisted request needs manual reconciliation")
            raise ContractExecutionError("Existing request cannot be sent again")

        created = self.capture.save_intent(request)
        if created is not True:
            raise ContractExecutionError("Request intent already exists")
        try:
            response = self.transport.send(request)
        except TransportFailure as exc:
            outcome = "outcome_unknown" if exc.outcome_unknown else "failed"
            category = "outcome_unknown" if exc.outcome_unknown else exc.category
            scope = "account" if exc.outcome_unknown else exc.scope
            self._record_failure(request, category, scope, outcome,
                                 exc.safe_message, exc.http_status)
            raise ContractExecutionError("Transport stopped; review persisted failure") from None
        except Exception:
            self._record_failure(request, "outcome_unknown", "account",
                "outcome_unknown", "Transport result needs manual reconciliation")
            raise ContractExecutionError("Transport result is unknown; no retry was attempted") from None

        # P1: retain the safe HTTP evidence BEFORE any business schema, cursor
        # or usage validation. Rejection must not erase what Meta returned.
        try:
            evidence = self.capture.save_response_evidence(response)
        except Exception:
            self._record_failure(request, "capture_io", "global", "failed",
                                 "Response evidence could not be stored")
            raise ContractExecutionError("Response evidence write failed; no HTTP retry") from None
        if evidence["classification"] != "safe":
            self._record_failure(request, "invalid_response", "account", "failed",
                                 "Response cannot be safely retained")
            raise ContractExecutionError("Response evidence unavailable; manual review") from None

        try:
            record = capture_record_from_response(request, response)
            persist_capture_verified(self.capture, record)
        except ContractError as exc:
            self._record_failure(request, exc.category, exc.scope, "failed",
                                 exc.safe_message)
            raise ContractExecutionError("Response was rejected before RAW write") from None
        except Exception:
            self._record_failure(request, "capture_io", "global", "failed",
                                 "Capture could not be verified")
            raise ContractExecutionError("Capture could not be verified; no retry was attempted") from None

        # If RAW fails or the acknowledgement is interrupted, the durable
        # capture remains pending and the next call recovers without HTTP.
        return commit_raw_and_confirm(self.capture, self.raw, record)

    def reproject_saved_response(self, request):
        """Explicit no-HTTP replay of a previously journaled safe response.

        Parser/schema repairs can rebuild Capture and RAW from the immutable
        received evidence. Never interpret this as an additional Meta request.
        A missing/unsafe payload or missing quota evidence remains blocked.
        """
        from three_api_transport import extract_next_cursor
        from three_api_runtime_contract import ResponseEnvelope
        existing = self.capture.get_capture_for_request(request.request_id)
        if existing is not None:
            if existing.request.request_spec_digest != request.request_spec_digest:
                raise ContractExecutionError("Request identity conflict")
            return commit_raw_and_confirm(self.capture, self.raw, existing)
        evidence = self.capture.get_response_evidence(request.request_id)
        if (evidence is None or evidence["classification"] != "safe"
                or evidence["safe_body"] is None
                or not 200 <= evidence["http_status"] < 300):
            raise ContractExecutionError("No safe successful response to reproject")
        metric = evidence["safe_usage"].get("max_usage_percent")
        headers = ({"x-app-usage": json.dumps({"call_count": metric})}
                   if type(metric) is int and 0 <= metric <= 100 else {})
        try:
            next_cursor = extract_next_cursor(
                evidence["safe_body"], requested_cursor=request.request_spec.after)
        except (ValueError, TypeError):
            next_cursor = None
        received_at = datetime.fromisoformat(evidence["received_at"].replace("Z", "+00:00"))
        response = ResponseEnvelope(CONTRACT_VERSION, request.request_id,
                    evidence["http_status"], evidence["safe_body"],
                    headers, received_at, next_cursor)
        # Validate BEFORE transitioning any intent state. Parser errors do not
        # destroy evidence or accidentally unlock a new transport attempt.
        try:
            record = capture_record_from_response(request, response)
        except ContractError:
            raise ContractExecutionError("Saved response still fails offline validation") from None
        self.capture.permit_local_reprojection(request)
        persist_capture_verified(self.capture, record)
        return commit_raw_and_confirm(self.capture, self.raw, record)

    def recover(self, run_id):
        """Replay durable captures only; unresolved intents remain blocked."""
        receipts = []
        for record in self.capture.list_pending_captures(run_id):
            receipts.append(commit_raw_and_confirm(self.capture, self.raw, record))
        unresolved = self.capture.list_unresolved_intents(run_id)
        for request in unresolved:
            if self.capture.get_intent_outcome(request.request_id) == "pending":
                self.capture.mark_outcome_unknown(request.request_id)
                try:
                    self._record_failure(request, "outcome_unknown", "account",
                        "outcome_unknown", "Restart found unresolved request intent")
                except ValueError:
                    # An equivalent failure may already have been saved.
                    pass
        return {"raw_receipts_recovered": len(receipts),
                "blocked_requests": len(unresolved),
                "request_ids": [item.request_id for item in unresolved]}
