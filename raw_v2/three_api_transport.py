"""Worker A opt-in adapter for C's runtime contract.

This module never reads environment variables or creates an HTTP sender.
The caller must explicitly inject an approved GraphHTTPClient and a token
at execution time. No automatic retry or credential persistence.
"""
from __future__ import annotations

from datetime import datetime, timezone

from three_api_graph_http import GraphHTTPClient, GraphHTTPBlocked, GraphTransportError
from three_api_http_preflight import prepare_graph_request
from three_api_credentials import CredentialReference
from three_api_runtime_contract import (
    CONTRACT_VERSION, ResponseEnvelope, TransportFailure,
)


def _fail(request_id, category, scope, message, *, status=None, unknown=False):
    raise TransportFailure(CONTRACT_VERSION, request_id, category, scope, False,
                           status, message, unknown)


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
        if request.contract_version != CONTRACT_VERSION:
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
        status = result["status"]
        if status == 429:
            _fail(request.request_id, "rate_limit", "global", "Rate limit", status=status)
        if status in (401, 403):
            _fail(request.request_id, "auth", "global", "Authentication rejected", status=status)
        if not 200 <= status < 300 or "error" in result["body"]:
            error = result["body"].get("error", {})
            if isinstance(error, dict) and error.get("code") == 4:
                _fail(request.request_id, "rate_limit", "global", "Meta rate limit", status=status)
            _fail(request.request_id, "http_error", "account", "HTTP rejected", status=status)
        # The current GraphHTTPClient retains only allowlisted usage headers.
        # Never silently accept missing headers; C's final parser must be used
        # before any capture is committed.
        headers = result["headers"]
        if not headers:
            _fail(request.request_id, "usage_missing", "global", "Usage evidence missing")
        # Quota evidence parsing is NOT finalized. Fail closed rather than
        # interpreting opaque Meta header JSON as a percentage incorrectly.
        _fail(request.request_id, "usage_missing", "global",
              "Quota parser pending contract approval")
