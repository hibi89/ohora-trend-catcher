"""Opt-in Graph HTTP transport boundary; disabled by default.

Never loads .env, os.environ, or token files. Does not call Meta on import or
construction. Real network calls require BOTH explicit run approval and an
explicitly supplied sender. No production DB or scheduler integration exists.

The caller must use a non-redirecting HTTPS sender for a future authorized run.
Tests inject in-memory senders; they never access the network.
"""
from __future__ import annotations

import json
import re
from urllib.error import HTTPError, URLError
from urllib.request import (
    HTTPRedirectHandler,
    ProxyHandler,
    Request,
    build_opener,
)

from three_api_credentials import validate_identity_value
from three_api_http_preflight import PreparedGraphRequest
from three_api_request_spec import build_request_spec, business_discovery_field

_MAX_BYTES = 512 * 1024
_USAGE_NAMES = frozenset(("x-app-usage", "x-business-use-case-usage"))
_VERSION_RE = re.compile(r"v[0-9]{1,3}\.[0-9]{1,2}\Z")
_ENV_NAME_RE = re.compile(r"[A-Z][A-Z0-9_]{0,127}\Z")


class GraphHTTPBlocked(RuntimeError):
    """Safe exception: never contains a token, URL, cursor, or response body."""


class GraphTransportError(RuntimeError):
    """Safe transport failure; callers must halt their lane for review."""


class _RejectRedirects(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        # Never forward bearer credentials to a different host or new URL.
        return None


def make_strict_sender():
    """Construct a redirect-denying sender; this does not perform a request.

    Only an explicitly authorized runtime may pass its .open method to the
    gated GraphHTTPClient. Proxy use is disabled to avoid implicit rerouting.
    """
    opener = build_opener(ProxyHandler({}), _RejectRedirects())
    return opener.open


def _validate_prepared(prepared):
    if not isinstance(prepared, PreparedGraphRequest):
        raise GraphHTTPBlocked("Invalid Graph request specification")
    if (prepared.lane not in ("A", "B", "C")
            or prepared.method != "GET"
            or not isinstance(prepared.version, str)
            or not _VERSION_RE.fullmatch(prepared.version)
            or not validate_identity_value(prepared.ig_user_id)
            or not isinstance(prepared.token_env_name, str)
            or not _ENV_NAME_RE.fullmatch(prepared.token_env_name)):
        raise GraphHTTPBlocked("Invalid Graph request specification")
    try:
        spec = build_request_spec(prepared.lane, prepared.username,
                                  after=prepared.after, limit=4,
                                  include_carousel_children=prepared.include_carousel_children,
                                  include_account_metrics=prepared.include_account_metrics)
    except (ValueError, TypeError):
        raise GraphHTTPBlocked("Invalid Graph request specification") from None
    if (prepared.after_present != (prepared.after is not None)
            or prepared.fields != business_discovery_field(spec)):
        raise GraphHTTPBlocked("Invalid Graph request specification")
    parts = prepared.unsigned_parts()
    expected_url = (
        "https://graph.facebook.com/" + prepared.version + "/" + prepared.ig_user_id
    )
    if (parts["url"] != expected_url or parts["method"] != "GET"
            or parts["headers"] != {}
            or not isinstance(parts["query_string"], str)
            or "access_token" in parts["query_string"].lower()):
        raise GraphHTTPBlocked("Invalid Graph request specification")
    return parts


def _safe_headers(headers):
    if headers is None or not hasattr(headers, "items"):
        return {}
    # Never return Set-Cookie, OAuth headers or other unreviewed metadata.
    return {
        str(key): str(value)
        for key, value in headers.items()
        if isinstance(key, str) and key.lower() in _USAGE_NAMES
    }


def _read_envelope(response, status):
    if type(status) is not int or not (100 <= status <= 599):
        raise GraphTransportError("Invalid HTTP status")
    try:
        raw = response.read(_MAX_BYTES + 1)
        if not isinstance(raw, bytes) or len(raw) > _MAX_BYTES:
            raise GraphTransportError("Response too large or malformed")
        body = json.loads(raw.decode("utf-8"))
        if not isinstance(body, dict):
            raise GraphTransportError("Invalid JSON response")
        # Error messages may contain credentials. Retain only bounded error
        # classification metadata; a normalizer handles success allowlists.
        if not 200 <= status <= 299 or "error" in body:
            err = body.get("error")
            if isinstance(err, dict):
                err = {k: err[k] for k in ("code", "error_subcode")
                       if type(err.get(k)) is int and 0 <= err[k] <= 1000000000}
            else:
                err = {}
            body = {"error": err}
        return {"status": status, "headers": _safe_headers(response.headers),
                "body": body}
    except (UnicodeError, ValueError, TypeError, AttributeError):
        raise GraphTransportError("Invalid JSON response") from None


class GraphHTTPClient:
    """Transport is inert unless explicitly enabled with a supplied sender.

    No ready-to-run CLI is provided. Supplying approval=True in tests is NOT
    approval for real Meta calls. A future live adapter needs explicit user
    authorization and a documented quota/scheduler review.
    """

    def __init__(self, *, sender=None, approved=False, timeout=15.0):
        if not isinstance(approved, bool):
            raise ValueError("Approval flag must be boolean")
        if type(timeout) not in (int, float) or not 0 < timeout <= 60:
            raise ValueError("Timeout must be within 0..60 seconds")
        if sender is not None and not callable(sender):
            raise TypeError("Sender must be callable")
        self._sender = sender
        self._approved = approved
        self._timeout = float(timeout)

    def __repr__(self):
        return "GraphHTTPClient(active=%s)" % (
            "approved" if self._approved and self._sender is not None else "disabled"
        )

    def fetch(self, prepared, token):
        # Verify the gate BEFORE constructing a request containing credentials.
        if not self._approved or self._sender is None:
            raise GraphHTTPBlocked("Live transport disabled: explicit approval required")
        parts = _validate_prepared(prepared)
        if (not isinstance(token, str) or not 1 <= len(token) <= 4096
                or any(ord(ch) < 33 or ord(ch) > 126 for ch in token)):
            raise GraphHTTPBlocked("Invalid runtime authorization")
        url = parts["url"] + "?" + parts["query_string"]
        req = Request(url=url, method="GET", headers={
            "Authorization": "Bearer " + token,
            "Accept": "application/json",
        })
        try:
            with self._sender(req, timeout=self._timeout) as response:
                return _read_envelope(response, response.status)
        except HTTPError as error:
            try:
                with error:
                    return _read_envelope(error, error.code)
            except GraphTransportError:
                # Rate-limit and auth status must still be passed downstream,
                # even if the body is not JSON. Never include error content.
                return {"status": error.code, "headers": _safe_headers(error.headers),
                        "body": {"error": {}}}
        except GraphTransportError:
            raise
        except Exception:
            # urllib errors can contain the full URL and token; do not chain.
            raise GraphTransportError("HTTP transport failed") from None
