"""P6 3-API integration contract v0.2 DRAFT (Astra review pending).

Pure data/interface declarations: no network, credentials, retry or database access.
All datetimes must be aware and are normalized to UTC; day_kst remains a date.
The SHA-256 capture digest covers canonical immutable CaptureRecord content,
excluding the digest itself. JSON canonicalization: UTF-8, sorted keys, compact
separators, ensure_ascii=False, UTC times with microseconds and 'Z' suffix.
"""
from __future__ import annotations

from dataclasses import dataclass, fields, is_dataclass
from datetime import date, datetime, timezone
from enum import Enum
from hashlib import sha256
import json
from typing import Any, Literal, Mapping, Protocol, Sequence

from three_api_request_spec import RequestSpec

CONTRACT_VERSION = "0.2-draft"
Lane = Literal["A", "B", "C"]
Phase = Literal["front", "catchup", "history"]
FieldStatus = Literal["returned", "null", "absent", "not_tested"]
RawStatus = Literal["committed", "already_committed"]
FailureScope = Literal["account", "lane", "global"]
ErrorCategory = Literal[
    "usage_missing", "rate_limit", "auth", "permission", "transport",
    "http_error", "invalid_response", "outcome_unknown", "capture_io",
    "capture_conflict", "raw_io", "raw_conflict", "cursor_conflict",
    "approval_denied", "budget_expired", "interrupted", "usage_limit",
]


# Fixed conservative blocking scopes. "retryable" never grants another HTTP attempt.
CATEGORY_SCOPE: Mapping[str, FailureScope] = {
    "usage_missing": "global", "usage_limit": "global", "rate_limit": "global",
    "auth": "global", "permission": "account", "transport": "account",
    "http_error": "account", "invalid_response": "account",
    "outcome_unknown": "account", "capture_io": "global",
    "capture_conflict": "global", "raw_io": "global",
    "raw_conflict": "global", "cursor_conflict": "lane",
    "approval_denied": "global", "budget_expired": "global",
    "interrupted": "global",
}
_ACCOUNT_FIELDS = frozenset(("id", "username", "followers_count", "media_count"))
_POST_FIELDS = frozenset(("id", "caption", "media_type", "media_url",
    "permalink", "timestamp", "like_count", "comments_count", "thumbnail_url",
    "children"))
_CHILD_FIELDS = frozenset(("id", "media_type", "media_url", "thumbnail_url"))
_RECORD_REQUEST_FIELDS = frozenset(("api_version", "target_ig_user_id", "lane",
    "username", "limit", "media_fields", "cursor", "method", "endpoint",
    "include_carousel_children"))
_FORBIDDEN = frozenset(("access_token", "token", "authorization",
    "client_secret", "app_secret", "paging.next", "next", "password",
    "secret", "cookie", "set-cookie", "headers", "usage_headers"))
_FIELD_STATES = frozenset(("returned", "null", "absent", "not_tested"))
_CAROUSEL_STATES = frozenset(("complete", "unknown", "not_applicable"))


def assert_safe_tree(value: Any, *, top_keys: frozenset[str] | None = None) -> None:
    """Reject credential-bearing keys/URLs anywhere in data being persisted."""
    if isinstance(value, Mapping):
        if top_keys is not None and not set(value).issubset(top_keys):
            raise ValueError("Unallowlisted persisted field")
        for key, val in value.items():
            if not isinstance(key, str) or key.lower() in _FORBIDDEN:
                raise ValueError("Forbidden persisted key")
            if key.lower().startswith("x-") or "token" in key.lower() or "secret" in key.lower():
                raise ValueError("Potential credential field")
            assert_safe_tree(val)
    elif isinstance(value, (list, tuple)):
        for item in value:
            assert_safe_tree(item)
    elif isinstance(value, str):
        lower = value.lower()
        if "access_token=" in lower or "authorization:" in lower or (
            lower.startswith(("http://", "https://")) and
            ("paging" in lower or "token" in lower)):
            raise ValueError("Potential credential-bearing value")
    else:
        _canonical(value)


def sanitize_meta_response(payload: Mapping[str, Any]) -> dict[str, Any]:
    """Only Business Discovery safe fields; never preserve paging URLs/headers."""
    if not isinstance(payload, Mapping):
        raise ValueError("Invalid Meta response")
    source = payload.get("business_discovery", payload)
    if not isinstance(source, Mapping):
        raise ValueError("Invalid Business Discovery envelope")
    account = {k: source[k] for k in _ACCOUNT_FIELDS if k in source}
    media = source.get("media", source)
    if not isinstance(media, Mapping) or not isinstance(media.get("data"), list):
        raise ValueError("Missing media.data")
    posts = []
    for item in media["data"]:
        if not isinstance(item, Mapping):
            raise ValueError("Invalid post")
        post = {k: item[k] for k in _POST_FIELDS - {"children"} if k in item}
        if "children" in item:
            children = item["children"]
            if not isinstance(children, Mapping) or not isinstance(children.get("data"), list):
                raise ValueError("Invalid carousel children")
            if children.get("paging", {}).get("next") or children.get("paging", {}).get("has_next"):
                raise ValueError("Incomplete carousel children")
            post["children"] = {"data": [
                {k: child[k] for k in _CHILD_FIELDS if k in child}
                for child in children["data"]]}
        posts.append(post)
    clean = {"account": account, "media": {"data": posts}}
    assert_safe_tree(clean)
    return clean


def safe_usage_metrics(headers: Mapping[str, str]) -> dict[str, int]:
    """Keep only parsed numeric usage, not header names or header bodies."""
    if not isinstance(headers, Mapping):
        raise ValueError("Usage headers missing")
    result: dict[str, int] = {}
    for name in ("x-app-usage-call-count", "x-business-use-case-usage-call-count"):
        if name in headers:
            value = headers[name]
            if not isinstance(value, str) or not value.isdecimal():
                raise ValueError("Unparseable usage metric")
            result[name.removeprefix("x-").replace("-", "_")] = int(value)
    if not result:
        raise ValueError("Usage evidence missing")
    return result


def validate_field_status(values: Mapping[str, Any], states: Mapping[str, str],
                          required: frozenset[str]) -> None:
    """Use all four states explicitly; null is NOT absent and not_tested is NOT absent."""
    if set(states) != set(required):
        raise ValueError("Incomplete field-status evidence")
    for key, status in states.items():
        if status not in _FIELD_STATES:
            raise ValueError("Invalid field state")
        if status == "returned" and (key not in values or values[key] is None):
            raise ValueError("returned must have non-null value")
        if status == "null" and (key not in values or values[key] is not None):
            raise ValueError("null must be explicitly present")
        if status in ("absent", "not_tested") and key in values:
            raise ValueError("absent/not_tested fields must not contain values")


def field_status_for(values: Mapping[str, Any], fields_tested: frozenset[str],
                     field_names: frozenset[str]) -> dict[str, FieldStatus]:
    if not set(fields_tested).issubset(field_names):
        raise ValueError("Invalid tested fields")
    return {name: ("not_tested" if name not in fields_tested else
                   "absent" if name not in values else
                   "null" if values[name] is None else "returned")
            for name in sorted(field_names)}


def requested_fields(request: "RequestEnvelope") -> dict[str, Any]:
    spec = request.request_spec
    return {"api_version": request.api_version,
            "target_ig_user_id": request.target_ig_user_id,
            "lane": spec.lane, "username": spec.username, "limit": spec.limit,
            "media_fields": spec.media_fields, "cursor": spec.after,
            "method": spec.method, "endpoint": spec.endpoint,
            "include_carousel_children": spec.include_carousel_children}


def validate_category_scope(category: str, scope: str) -> None:
    if category not in CATEGORY_SCOPE or scope != CATEGORY_SCOPE[category]:
        raise ValueError("Incorrect category/scope combination")


def utc(value: datetime) -> datetime:
    if not isinstance(value, datetime) or value.tzinfo is None or value.utcoffset() is None:
        raise ValueError("timezone-aware datetime required")
    return value.astimezone(timezone.utc)


def _canonical(value: Any) -> Any:
    if isinstance(value, datetime):
        return utc(value).isoformat(timespec="microseconds").replace("+00:00", "Z")
    if isinstance(value, date):
        return value.isoformat()
    if isinstance(value, Enum):
        return value.value
    if is_dataclass(value) and not isinstance(value, type):
        return {f.name: _canonical(getattr(value, f.name)) for f in fields(value)}
    if isinstance(value, Mapping):
        if not all(isinstance(k, str) for k in value):
            raise ValueError("JSON mapping keys must be strings")
        return {k: _canonical(v) for k, v in value.items()}
    if isinstance(value, (tuple, list)):
        return [_canonical(v) for v in value]
    if value is None or type(value) in (str, int, float, bool):
        if isinstance(value, float) and (value != value or abs(value) == float("inf")):
            raise ValueError("Non-finite float forbidden")
        return value
    raise TypeError(f"Unsupported canonical type: {type(value).__name__}")


def canonical_bytes(value: Any) -> bytes:
    return json.dumps(_canonical(value), sort_keys=True, ensure_ascii=False,
                      separators=(",", ":"), allow_nan=False).encode("utf-8")


def digest_sha256(value: Any) -> str:
    return sha256(canonical_bytes(value)).hexdigest()


@dataclass(frozen=True)
class RequestEnvelope:
    contract_version: str
    request_id: str
    run_id: str
    roster_digest: str
    day_kst: date
    account_ordinal: int
    account_key: str
    username: str
    lane: Lane
    phase: Phase
    page: int
    cursor: str | None
    requested_at: datetime
    request_spec: RequestSpec
    request_spec_digest: str
    api_version: str = "UNSPECIFIED"
    target_ig_user_id: str = "UNSPECIFIED"
    retry_of: str | None = None

    def __post_init__(self) -> None:
        if self.contract_version != CONTRACT_VERSION:
            raise ValueError("Unsupported contract version")
        object.__setattr__(self, "requested_at", utc(self.requested_at))
        if self.phase == "history":
            raise ValueError("History lane is on hold")
        if self.lane not in ("A", "B", "C") or self.request_spec.lane != self.lane:
            raise ValueError("Lane mismatch")
        if self.request_spec.username != self.username or self.request_spec.after != self.cursor:
            raise ValueError("RequestSpec mismatch")
        if self.request_spec_digest != digest_sha256({"api_version": self.api_version, "target_ig_user_id": self.target_ig_user_id, "spec": self.request_spec}):
            raise ValueError("RequestSpec digest mismatch")
        if not self.api_version or self.api_version == "UNSPECIFIED" or not self.target_ig_user_id or self.target_ig_user_id == "UNSPECIFIED":
            raise ValueError("API version and target IG user ID required")
        if self.page < 1 or self.account_ordinal < 1:
            raise ValueError("Invalid page or account ordinal")
        if not isinstance(self.day_kst, date) or isinstance(self.day_kst, datetime):
            raise ValueError("day_kst must be date")


@dataclass(frozen=True)
class ResponseEnvelope:
    contract_version: str
    request_id: str
    http_status: int
    payload: Mapping[str, Any]  # VOLATILE ONLY: never persist directly
    usage_headers: Mapping[str, str]  # VOLATILE ONLY: sanitize before persistence
    received_at: datetime
    next_cursor: str | None

    def __post_init__(self) -> None:
        if self.contract_version != CONTRACT_VERSION:
            raise ValueError("Unsupported contract version")
        object.__setattr__(self, "received_at", utc(self.received_at))


@dataclass(frozen=True)
class TransportFailure(Exception):
    contract_version: str
    request_id: str
    category: ErrorCategory
    scope: FailureScope
    retryable: bool
    http_status: int | None
    safe_message: str
    outcome_unknown: bool = False

    def __post_init__(self) -> None:
        if self.contract_version != CONTRACT_VERSION:
            raise ValueError("Unsupported contract version")
        validate_category_scope(self.category, self.scope)
        if self.category == "outcome_unknown" and not self.outcome_unknown:
            raise ValueError("Unknown outcome must be explicit")


@dataclass(frozen=True)
class CursorState:
    contract_version: str
    request_id: str
    source_digest: str
    current_cursor: str | None
    next_cursor: str | None
    page: int
    # Evidence only; NOT an independently advancing progress pointer.

    def __post_init__(self) -> None:
        if self.contract_version != CONTRACT_VERSION or self.page < 1:
            raise ValueError("Invalid cursor evidence")


@dataclass(frozen=True)
class CaptureRecord:
    contract_version: str
    capture_id: str
    request: RequestEnvelope
    actual_request_fields: Mapping[str, Any]  # API version, target, fields, cursor, limit; no secrets
    sanitized_response: Mapping[str, Any]  # allowlist only; no paging URLs
    normalized_posts: Sequence[Mapping[str, Any]]
    account_observation: Mapping[str, Any]
    field_status: Mapping[str, FieldStatus]
    carousel_completeness: Mapping[str, str]
    cursor_state: CursorState
    observed_at: datetime  # EXACT response received_at; immutable on replay
    response_status: Literal["success"] = "success"
    response_http_status: int = 200
    response_received_at: datetime | None = None
    usage_metrics: Mapping[str, int] | None = None
    post_field_status: Mapping[str, Mapping[str, FieldStatus]] | None = None

    def __post_init__(self) -> None:
        if self.contract_version != CONTRACT_VERSION or self.request.contract_version != CONTRACT_VERSION:
            raise ValueError("Contract version mismatch")
        object.__setattr__(self, "observed_at", utc(self.observed_at))
        if self.cursor_state.request_id != self.request.request_id:
            raise ValueError("Cursor source mismatch")
        if self.response_status != "success" or not 200 <= self.response_http_status < 300:
            raise ValueError("Failures are not successful captures")
        if self.response_received_at is None or utc(self.response_received_at) != self.observed_at:
            raise ValueError("observation must equal actual HTTP received_at")
        object.__setattr__(self, "response_received_at", utc(self.response_received_at))
        if self.actual_request_fields != requested_fields(self.request):
            raise ValueError("Request evidence differs from HTTP intent")
        if self.cursor_state.page != self.request.page or self.cursor_state.current_cursor != self.request.cursor:
            raise ValueError("Cursor/page mismatch")
        assert_safe_tree(self.actual_request_fields, top_keys=_RECORD_REQUEST_FIELDS)
        assert_safe_tree(self.sanitized_response, top_keys=frozenset(("account", "media")))
        assert_safe_tree(self.normalized_posts)
        assert_safe_tree(self.account_observation, top_keys=_ACCOUNT_FIELDS)
        if self.usage_metrics is None or not self.usage_metrics or any(type(v) is not int or v < 0 for v in self.usage_metrics.values()):
            raise ValueError("Missing safe usage evidence")
        validate_field_status(self.account_observation, self.field_status,
                              frozenset(("followers_count", "media_count")))
        if len(self.normalized_posts) != len(self.sanitized_response["media"]["data"]):
            raise ValueError("Post count inconsistency")
        if len(self.carousel_completeness) != len(self.normalized_posts):
            raise ValueError("Incomplete per-post carousel evidence")
        if self.post_field_status is None or set(self.post_field_status) != {
                p.get("id") for p in self.normalized_posts}:
            raise ValueError("Per-post field states are required")
        for post in self.normalized_posts:
            validate_field_status(post, self.post_field_status[post["id"]],
                                  frozenset(("like_count", "comments_count")))
            if not isinstance(post, Mapping) or not isinstance(post.get("id"), str):
                raise ValueError("Invalid normalized post")
            id_ = post["id"]
            status = self.carousel_completeness.get(id_)
            if status not in _CAROUSEL_STATES:
                raise ValueError("Missing carousel completeness")
            if post.get("media_type") != "CAROUSEL_ALBUM" and status != "not_applicable":
                raise ValueError("Non-carousel must be not_applicable")
            if post.get("media_type") == "CAROUSEL_ALBUM":
                children = post.get("children")
                if status == "complete" and not isinstance(children, list):
                    raise ValueError("Complete carousel requires children")
                if status == "unknown" and children is not None:
                    raise ValueError("Unknown carousel cannot claim children")

    @property
    def capture_digest(self) -> str:
        return digest_sha256(self)


@dataclass(frozen=True)
class CaptureReceipt:
    contract_version: str
    capture_id: str
    request_id: str
    account_key: str
    lane: Lane
    captured_at: datetime
    capture_digest: str
    storage_key: str
    durable: bool

    def __post_init__(self) -> None:
        if self.contract_version != CONTRACT_VERSION:
            raise ValueError("Contract version mismatch")
        object.__setattr__(self, "captured_at", utc(self.captured_at))
        if self.durable is not True or len(self.capture_digest) != 64 or not self.storage_key:
            raise ValueError("Receipt only issued after commit+reread+digest verification")


@dataclass(frozen=True)
class RawWriteReceipt:
    contract_version: str
    capture_id: str
    capture_digest: str
    raw_receipt_id: str
    status: RawStatus
    committed_at: datetime
    row_counts: Mapping[str, int]

    def __post_init__(self) -> None:
        if self.contract_version != CONTRACT_VERSION:
            raise ValueError("Contract version mismatch")
        object.__setattr__(self, "committed_at", utc(self.committed_at))
        if self.status not in ("committed", "already_committed"):
            raise ValueError("Invalid RAW receipt status")
        if len(self.capture_digest) != 64 or not self.raw_receipt_id:
            raise ValueError("Invalid RAW receipt identity")
        if any(type(v) is not int or v < 0 for v in self.row_counts.values()):
            raise ValueError("Invalid row counts")


@dataclass(frozen=True)
class ContractError(Exception):
    contract_version: str
    category: ErrorCategory
    scope: FailureScope
    safe_message: str
    request_id: str | None = None
    capture_id: str | None = None
    retryable: bool = False

    def __post_init__(self) -> None:
        if self.contract_version != CONTRACT_VERSION:
            raise ValueError("Contract version mismatch")
        validate_category_scope(self.category, self.scope)


@dataclass(frozen=True)
class FailureEvidence:
    """Persisted FAILED/UNKNOWN outcome; never passed to RAW writer."""
    contract_version: str
    request_id: str
    run_id: str
    category: ErrorCategory
    scope: FailureScope
    outcome: Literal["failed", "outcome_unknown"]
    recorded_at: datetime
    safe_message: str
    http_status: int | None = None

    def __post_init__(self) -> None:
        if self.contract_version != CONTRACT_VERSION:
            raise ValueError("Contract version mismatch")
        validate_category_scope(self.category, self.scope)
        object.__setattr__(self, "recorded_at", utc(self.recorded_at))
        if self.outcome == "outcome_unknown" and self.category != "outcome_unknown":
            raise ValueError("Unknown outcome category mismatch")
        assert_safe_tree({"safe_message": self.safe_message})


def validate_capture_receipt(record: CaptureRecord, receipt: CaptureReceipt) -> None:
    if receipt.durable is not True or receipt.capture_digest != record.capture_digest or (
        receipt.capture_id, receipt.request_id, receipt.account_key, receipt.lane) != (
        record.capture_id, record.request.request_id, record.request.account_key, record.request.lane):
        raise ValueError("Unverified Capture receipt")


def validate_raw_receipt(record: CaptureRecord, receipt: RawWriteReceipt) -> None:
    if receipt.capture_id != record.capture_id or receipt.capture_digest != record.capture_digest:
        raise ValueError("Conflicting RAW receipt")


class TransportPort(Protocol):
    def send(self, request: RequestEnvelope) -> ResponseEnvelope:
        """Only after explicit approval; raises TransportFailure. Never auto-retry."""


class CapturePort(Protocol):
    def save_intent(self, request: RequestEnvelope) -> None:
        """Durably persist request_id before HTTP; raises ContractError."""

    def save_capture(self, record: CaptureRecord) -> CaptureReceipt:
        """Commit, reread and digest-verify before durable=True; raises ContractError."""

    def get_capture(self, capture_id: str) -> CaptureRecord | None:
        """Return unchanged persisted record; raises ContractError."""

    def list_pending_captures(self, run_id: str) -> Sequence[CaptureRecord]:
        """Unconfirmed captures for recovery, no network; raises ContractError."""

    def confirm_processed(self, capture_id: str, raw_receipt_id: str) -> None:
        """Persist verified RAW receipt acknowledgement; raises ContractError."""

    def save_failure(self, failure: FailureEvidence) -> None:
        """Persist non-2xx, missing usage, or ambiguous outcome, never RAW; ContractError."""

    def get_failure(self, request_id: str) -> FailureEvidence | None:
        """Read persisted failed/unknown intent; raises ContractError."""

    def list_unresolved_intents(self, run_id: str) -> Sequence[RequestEnvelope]:
        """Intents without durable success or terminal reconciliation; ContractError."""

    def mark_outcome_unknown(self, request_id: str) -> None:
        """Persist ambiguous HTTP outcome before manual reconciliation; ContractError."""

    def reconcile_request(self, request_id: str, resolution: Literal[
            "failed_no_retry", "manual_retry_approved"]) -> None:
        """Operator-driven recovery only; any retry gets new request ID/retry_of."""


class RawWriterPort(Protocol):
    def write_capture(self, record: CaptureRecord) -> RawWriteReceipt:
        """Atomic RAW rows + receipt, idempotent by capture ID+digest; raises ContractError."""

    def get_raw_receipt(self, capture_id: str) -> RawWriteReceipt | None:
        """Lookup receipt for crash recovery, no network; raises ContractError."""


def capture_from_response(
    request: RequestEnvelope, response: ResponseEnvelope, *,
    capture_id: str, normalized_posts: Sequence[Mapping[str, Any]],
    account_observation: Mapping[str, Any],
    account_fields_tested: frozenset[str],
    carousel_completeness: Mapping[str, str],
    source_digest: str,
) -> CaptureRecord:
    """Construct success evidence using *actual* response receipt time.
    Non-2xx/missing usage raises ContractError; caller must save FailureEvidence.
    """
    if request.request_id != response.request_id:
        raise ContractError(CONTRACT_VERSION, "invalid_response", "account",
                            "Response request ID mismatch", request.request_id)
    if not 200 <= response.http_status < 300:
        raise ContractError(CONTRACT_VERSION, "http_error", "account",
                            "HTTP failure cannot become success capture", request.request_id)
    try:
        sanitized = sanitize_meta_response(response.payload)
        metrics = safe_usage_metrics(response.usage_headers)
        states = field_status_for(account_observation, account_fields_tested,
                    frozenset(("followers_count", "media_count")))
        cursor_state = CursorState(CONTRACT_VERSION, request.request_id,
                source_digest, request.cursor, response.next_cursor, request.page)
        return CaptureRecord(CONTRACT_VERSION, capture_id, request,
                requested_fields(request), sanitized, normalized_posts,
                account_observation, states, carousel_completeness,
                cursor_state, response.received_at,
                response_http_status=response.http_status,
                response_received_at=response.received_at, usage_metrics=metrics,
                post_field_status={
                    post["id"]: field_status_for(post,
                        frozenset(("like_count", "comments_count")),
                        frozenset(("like_count", "comments_count")))
                    for post in normalized_posts})
    except (ValueError, TypeError, KeyError) as exc:
        category = "usage_missing" if "usage" in str(exc).lower() else "invalid_response"
        raise ContractError(CONTRACT_VERSION, category, CATEGORY_SCOPE[category],
                            "Response rejected by capture validation",
                            request.request_id) from exc

def persist_capture_verified(port: CapturePort, record: CaptureRecord) -> CaptureReceipt:
    """Verifier for A implementation; no actual database is built here.
    A must atomically commit before returning; this function rereads record
    to ensure receipt identity/digest. Never infer durability from an in-memory flag.
    """
    receipt = port.save_capture(record)
    validate_capture_receipt(record, receipt)
    restored = port.get_capture(record.capture_id)
    if restored is None or restored.capture_digest != record.capture_digest:
        raise ContractError(CONTRACT_VERSION, "capture_conflict", "global",
                            "Capture verification failed", record.request.request_id,
                            record.capture_id)
    return receipt


def commit_raw_and_confirm(capture: CapturePort, raw: RawWriterPort,
                           record: CaptureRecord) -> RawWriteReceipt:
    """Crash-safe sequence after durable capture. Prefer existing RAW receipt.
    B's implementation must atomically commit RAW rows and receipt.
    """
    receipt = raw.get_raw_receipt(record.capture_id)
    if receipt is None:
        receipt = raw.write_capture(record)
    validate_raw_receipt(record, receipt)
    capture.confirm_processed(record.capture_id, receipt.raw_receipt_id)
    return receipt


# Coordination rules:
# 1. save_intent -> send -> save_capture -> write_capture -> confirm processed.
# 2. Response payload/headers are volatile; A allowlists stored data before save.
# 3. Source cursor evidence may be saved early but NEVER authorizes next HTTP
#    before RAW receipt is committed and progress is confirmed.
# 4. Unknown HTTP outcome blocks; never auto-repeat. Approved retries create
#    a fresh request_id with retry_of pointing to the previous request.
# 5. Identical capture_id+digest returns prior receipt; different digest raises
#    capture_conflict/raw_conflict. Separate observations retain separate IDs.
# 6. No G0-G6 boolean, retryable flag or stored cursor grants runtime approval.
