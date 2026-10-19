"""P6 3-API integration contract v0.2 DRAFT (Astra review pending).

Pure data/interface declarations: no network, credentials, retry or database access.
All datetimes must be aware and are normalized to UTC; day_kst remains a date.
The SHA-256 capture digest covers canonical immutable CaptureRecord content,
excluding the digest itself. JSON canonicalization: UTF-8, sorted keys, compact
separators, ensure_ascii=False, UTC times with microseconds and 'Z' suffix.
"""
from __future__ import annotations

from dataclasses import dataclass, field, fields, is_dataclass
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
FailureScope = Literal["account", "lane", "run"]
ErrorCategory = Literal[
    "usage_missing", "rate_limit", "auth", "permission", "transport",
    "http_error", "invalid_response", "outcome_unknown", "capture_io",
    "capture_conflict", "raw_io", "raw_conflict", "cursor_conflict",
    "approval_denied", "budget_expired", "interrupted",
]


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
        if self.request_spec_digest != digest_sha256(self.request_spec):
            raise ValueError("RequestSpec digest mismatch")
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

    def __post_init__(self) -> None:
        if self.contract_version != CONTRACT_VERSION or self.request.contract_version != CONTRACT_VERSION:
            raise ValueError("Contract version mismatch")
        object.__setattr__(self, "observed_at", utc(self.observed_at))
        if self.cursor_state.request_id != self.request.request_id:
            raise ValueError("Cursor source mismatch")
        if self.response_status != "success":
            raise ValueError("Failures are not successful captures")
        if any(v not in ("returned", "null", "absent", "not_tested") for v in self.field_status.values()):
            raise ValueError("Unknown field status")

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

    def mark_outcome_unknown(self, request_id: str) -> None:
        """Persist ambiguous HTTP outcome; raises ContractError."""


class RawWriterPort(Protocol):
    def write_capture(self, record: CaptureRecord) -> RawWriteReceipt:
        """Atomic RAW rows + receipt, idempotent by capture ID+digest; raises ContractError."""

    def get_raw_receipt(self, capture_id: str) -> RawWriteReceipt | None:
        """Lookup receipt for crash recovery, no network; raises ContractError."""


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
