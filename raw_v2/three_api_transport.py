"""Worker A opt-in adapter for C's runtime contract.

This module never reads environment variables or creates an HTTP sender.
The caller must explicitly inject an approved GraphHTTPClient and a token
at execution time. No automatic retry or credential persistence.
"""
from __future__ import annotations

from datetime import datetime, timezone

from three_api_graph_http import GraphHTTPClient, GraphHTTPBlocked, GraphTransportError
from three_api_http_preflight import prepare_graph_request
from three_api_credentials import CredentialReference, validate_identity_value
from three_api_paging import validate_page_edge
def _contract():
    # C's contract is branch-owned until integration; importing this module
    # for offline quota checks must not require merging that draft first.
    from three_api_runtime_contract import (
        CONTRACT_VERSION, ResponseEnvelope, TransportFailure,
    )
    return CONTRACT_VERSION, ResponseEnvelope, TransportFailure


def _fail(request_id, category, scope, message, *, status=None, unknown=False):
    version, _, failure_type = _contract()
    raise failure_type(version, request_id, category, scope, False,
                       status, message, unknown)


def parse_meta_usage(headers):
    """Use the repository's single Meta quota parser; never retain raw headers."""
    from three_api_usage import parse_usage
    value = parse_usage(headers)
    if value is None:
        raise ValueError("Usage evidence missing or malformed")
    return value


def _safe_usage_suffix(headers):
    """Expose only a bounded numeric quota observation from an error response."""
    from math import ceil
    from three_api_usage import parse_usage
    observed = parse_usage(headers)
    if observed is None:
        return ""
    return f" usage_pct={ceil(observed)}"


def extract_next_cursor(payload, *, requested_cursor=None):
    """Validate Business Discovery paging without following or storing next URL."""
    if not isinstance(payload, dict):
        raise ValueError("Invalid response")
    account = payload.get("business_discovery")
    if not isinstance(account, dict):
        raise ValueError("Missing Business Discovery")
    media = account.get("media")
    if not isinstance(media, dict):
        raise ValueError("Missing media")
    return validate_page_edge(media, requested_cursor=requested_cursor)["next_cursor"]


class MetaTransport:
    """One HTTP attempt per send; injected GraphHTTPClient remains the sole sender."""

    def __init__(self, *, client: GraphHTTPClient, credentials: CredentialReference,
                 caller_ig_id: str, token: str, clock=None):
        if not isinstance(client, GraphHTTPClient):
            raise TypeError("Expected GraphHTTPClient")
        if not isinstance(credentials, CredentialReference):
            raise TypeError("Expected credential reference")
        if not validate_identity_value(caller_ig_id):
            raise ValueError("Missing caller identity")
        if not isinstance(token, str) or not token:
            raise ValueError("Missing explicit runtime authorization")
        self._client = client
        self._credentials = credentials
        self._caller_ig_id = caller_ig_id
        self._token = token
        self._clock = clock or (lambda: datetime.now(timezone.utc))
        self._halt_reason = None  # Local stop; persistent lane gate remains coordinator-owned.

    def send(self, request):
        # The sole HTTP sender is the existing gated GraphHTTPClient. Its
        # default is disabled; no token is read from disk or environment here.
        version, response_type, _ = _contract()
        # Defence in depth: offline HISTORY records must never gain Graph
        # transport authorization by constructing a valid RequestEnvelope.
        if (getattr(request, "phase", None) == "history"
                and getattr(request, "history_live_approved", False) is not True):
            _fail(request.request_id, "approval_denied", "global",
                  "Offline history cannot use Graph transport")
        if self._halt_reason is not None:
            _fail(request.request_id, self._halt_reason, "global",
                  "Further transport requests are halted")
        if request.contract_version != version:
            _fail(request.request_id, "approval_denied", "global", "Contract mismatch")
        if request.lane != self._credentials.lane:
            _fail(request.request_id, "approval_denied", "global", "Lane mismatch")
        if request.target_ig_user_id != self._caller_ig_id:
            _fail(request.request_id, "approval_denied", "global", "Caller identity mismatch")
        try:
            prepared = prepare_graph_request(
                request.request_spec, self._credentials, self._caller_ig_id,
                version=request.api_version,
            )
        except (ValueError, TypeError):
            _fail(request.request_id, "approval_denied", "global", "Invalid request")
        try:
            result = self._client.fetch(prepared, self._token)
        except GraphHTTPBlocked:
            _fail(request.request_id, "approval_denied", "global", "Transport disabled")
        except GraphTransportError:
            _fail(request.request_id, "outcome_unknown", "account",
                  "HTTP outcome unknown; manual reconciliation required", unknown=True)
        if not isinstance(result, dict) or type(result.get("status")) is not int or not isinstance(result.get("body"), dict):
            _fail(request.request_id, "outcome_unknown", "account",
                  "Malformed transport outcome; manual reconciliation required", unknown=True)
        status = result["status"]
        error = result["body"].get("error")
        code = error.get("code") if isinstance(error, dict) else None
        subcode = error.get("error_subcode") if isinstance(error, dict) else None
        if (status == 400 and code == 100 and isinstance(error, dict)
                and error.get("cursor_invalid") is True):
            _fail(request.request_id, "cursor_invalid", "account",
                  "Meta explicitly rejected the supplied cursor"
                  + _safe_usage_suffix(result.get("headers")), status=status)
        # GraphHTTPClient already strips all free-form Meta text and tokens.
        # Retain only bounded integer diagnostics for *future* failures.
        safe_codes = []
        if type(code) is int and 0 <= code <= 1000000:
            safe_codes.append(f"code={code}")
        if type(subcode) is int and 0 <= subcode <= 1000000:
            safe_codes.append(f"subcode={subcode}")
        diagnostic = " (" + ", ".join(safe_codes) + ")" if safe_codes else ""
        if code in (4, 17, 32, 613) or status == 429:
            _fail(request.request_id, "rate_limit", "global",
                  "Meta rate limit" + diagnostic
                  + _safe_usage_suffix(result.get("headers")), status=status)
        if status == 401:
            _fail(request.request_id, "auth", "global",
                  "Authentication rejected" + _safe_usage_suffix(result.get("headers")),
                  status=status)
        if status == 403 or code in (10, 200):
            _fail(request.request_id, "permission", "account",
                  "Permission rejected" + diagnostic
                  + _safe_usage_suffix(result.get("headers")), status=status)
        # Meta code 110 / subcode 2207013 denotes an unavailable target user.
        # Classify the immutable failed request only; quota remains held because
        # error responses do not carry verified usage evidence in this contract.
        if status == 400 and code == 110 and subcode == 2207013:
            # Error responses can contain trustworthy per-lane usage headers.
            # Persist only the conservative integer percentage, never raw
            # headers/body. Missing/malformed usage remains a held lane.
            _fail(request.request_id, "http_error", "account",
                  "Target account unavailable code=110 subcode=2207013"
                  + _safe_usage_suffix(result.get("headers")), status=status)
        if not 200 <= status < 300 or "error" in result["body"]:
            _fail(request.request_id, "http_error", "account",
                  "HTTP rejected" + diagnostic
                  + _safe_usage_suffix(result.get("headers")), status=status)
        # Keep the successfully received body available for P1's durable
        # response-evidence journal, even at >=85% usage or malformed paging.
        # The following request is blocked; a received response is not lost.
        headers = result.get("headers")
        headers = headers if isinstance(headers, dict) else {}
        try:
            usage = parse_meta_usage(headers)
        except ValueError:
            usage = None
        if usage is None:
            self._halt_reason = "usage_missing"
        elif usage >= 85:
            self._halt_reason = "usage_limit"
        try:
            next_cursor = extract_next_cursor(
                result["body"], requested_cursor=request.request_spec.after)
        except (ValueError, TypeError):
            # Executor validates the cursor AFTER response evidence persists.
            next_cursor = None
        return response_type(
            version, request.request_id, status, result["body"], headers,
            self._clock(), next_cursor)
