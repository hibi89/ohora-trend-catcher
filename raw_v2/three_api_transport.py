"""Worker A opt-in adapter for C's runtime contract.

This module never reads environment variables or creates an HTTP sender.
The caller must explicitly inject an approved GraphHTTPClient and a token
at execution time. No automatic retry or credential persistence.
"""
from __future__ import annotations

from datetime import datetime, timezone
import json

from three_api_graph_http import GraphHTTPClient, GraphHTTPBlocked, GraphTransportError
from three_api_http_preflight import prepare_graph_request
from three_api_credentials import CredentialReference
def _contract():
    # C's contract is branch-owned until integration; importing this module
    # for offline quota checks must not require merging that draft first.
    from three_api_runtime_contract import CONTRACT_VERSION, TransportFailure
    return CONTRACT_VERSION, TransportFailure


def _fail(request_id, category, scope, message, *, status=None, unknown=False):
    version, failure_type = _contract()
    raise failure_type(version, request_id, category, scope, False,
                       status, message, unknown)


def parse_meta_usage(headers):
    """Conservatively parse Meta's JSON usage headers; never retain raw headers.

    Returns maximum observed percentage. Missing/malformed evidence fails
    closed. Values from distinct apps are not assumed to share quota.
    """
    if not isinstance(headers, dict):
        raise ValueError("Missing usage evidence")
    levels = []
    headers = {str(k).lower(): v for k, v in headers.items()}
    for name in ("x-app-usage", "x-business-use-case-usage"):
        if name not in headers:
            continue
        try:
            parsed = json.loads(headers[name])
        except (TypeError, ValueError):
            raise ValueError("Malformed usage evidence") from None
        entries = [parsed] if name == "x-app-usage" else (
            [item for group in parsed.values() for item in group]
            if isinstance(parsed, dict) and all(isinstance(group, list) and group for group in parsed.values()) else [])
        if not entries:
            raise ValueError("Empty usage evidence")
        for entry in entries:
            if not isinstance(entry, dict):
                raise ValueError("Invalid usage metrics")
            keys = ("call_count", "total_cputime", "total_time") if name == "x-app-usage" else (
                "call_count", "total_cputime", "total_time", "estimated_time_to_regain_access")
            measured = [entry[k] for k in keys[:3] if k in entry]
            if not measured or any(type(v) not in (int, float) or not 0 <= v <= 100 for v in measured):
                raise ValueError("Unverifiable usage percentage")
            levels.extend(measured)
    if not levels:
        raise ValueError("Usage evidence missing")
    return max(levels)


class MetaTransport:
    """One HTTP attempt per send; injected GraphHTTPClient remains the sole sender."""

    def __init__(self, *, client: GraphHTTPClient, credentials: CredentialReference,
                 caller_ig_id: str, token: str, clock=None):
        if not isinstance(client, GraphHTTPClient):
            raise TypeError("Expected GraphHTTPClient")
        if not isinstance(credentials, CredentialReference):
            raise TypeError("Expected credential reference")
        if not isinstance(caller_ig_id, str) or not caller_ig_id:
            raise ValueError("Missing caller identity")
        if not isinstance(token, str) or not token:
            raise ValueError("Missing explicit runtime authorization")
        self._client = client
        self._credentials = credentials
        self._caller_ig_id = caller_ig_id
        self._token = token
        self._clock = clock or (lambda: datetime.now(timezone.utc))

    def send(self, request):
        # Never send a request whose identity differs from its approved envelope.
        version, _ = _contract()
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
        if isinstance(error, dict) and error.get("code") == 4:
            _fail(request.request_id, "rate_limit", "global", "Meta rate limit", status=status)
        if status == 429:
            _fail(request.request_id, "rate_limit", "global", "Rate limit", status=status)
        if status in (401, 403):
            _fail(request.request_id, "auth", "global", "Authentication rejected", status=status)
        if not 200 <= status < 300 or "error" in result["body"]:
            _fail(request.request_id, "http_error", "account", "HTTP rejected", status=status)
        # The current GraphHTTPClient retains only allowlisted usage headers.
        # Never silently accept missing headers; C's final parser must be used
        # before any capture is committed.
        headers = result.get("headers")
        if not isinstance(headers, dict) or not headers:
            _fail(request.request_id, "usage_missing", "global", "Usage evidence missing")
        try:
            usage = parse_meta_usage(headers)
        except ValueError:
            _fail(request.request_id, "usage_missing", "global", "Unverifiable usage")
        if usage >= 85:
            _fail(request.request_id, "usage_limit", "global", "Usage threshold reached")
        # C v0.2 draft expects synthetic numeric header keys rather than
        # Meta's JSON usage format. Until C freezes the translation contract,
        # block capture rather than fabricating a successful response.
        _fail(request.request_id, "usage_missing", "global",
              "Usage contract translation pending approval")
