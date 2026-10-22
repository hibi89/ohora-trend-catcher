"""No-network Graph request preflight and synthetic response transport.

This module NEVER performs HTTP or reads a token. Prepared requests carry only
an authorization *reference*, not a value. A real transport is deliberately
absent; adding one requires separate security review and user approval.
"""
from __future__ import annotations

from copy import deepcopy
from dataclasses import dataclass
import re
from urllib.parse import urlencode

from three_api_credentials import CredentialReference, validate_identity_value
from three_api_request_spec import RequestSpec, build_request_spec, business_discovery_field

_VERSION = re.compile(r"^v[0-9]{1,3}\.[0-9]{1,2}$")
_ENV = re.compile(r"^[A-Z][A-Z0-9_]{0,127}$")
_HOST = "https://graph.facebook.com"


@dataclass(frozen=True, repr=False)
class PreparedGraphRequest:
    lane: str
    username: str
    version: str
    ig_user_id: str
    fields: str
    after_present: bool
    after: str | None
    token_env_name: str
    method: str = "GET"
    include_carousel_children: bool = False
    include_account_metrics: bool = False

    def __repr__(self):
        return "PreparedGraphRequest(%r)" % self.safe_summary()

    def safe_summary(self) -> dict:
        """Safe for status reports: no IDs, secret names, raw cursor or URL."""
        return {"lane": self.lane, "method": self.method,
                "host": _HOST, "version": self.version,
                "page_size": 4, "after_present": self.after_present,
                "carousel_children_opt_in": self.include_carousel_children,
                "account_metrics_opt_in": self.include_account_metrics,
                "authenticated": False, "mode": "OFFLINE_PREFLIGHT"}

    def unsigned_parts(self) -> dict:
        """Transport-independent parts, excluding all authorization headers."""
        return {"method": self.method,
                "url": f"{_HOST}/{self.version}/{self.ig_user_id}",
                "query_string": urlencode({"fields": self.fields}),
                "headers": {}}


def prepare_graph_request(spec: RequestSpec, credentials: CredentialReference,
                          caller_ig_id: str, *, version: str = "v26.0") -> PreparedGraphRequest:
    """Produce a validated unsigned request; do not obtain actual credentials."""
    if not isinstance(spec, RequestSpec) or not isinstance(credentials, CredentialReference):
        raise TypeError("Expected validated request and credential references")
    # Validate again: callers could construct a dataclass without using the validator.
    canonical = build_request_spec(spec.lane, spec.username,
                                   after=spec.after, limit=spec.limit,
                                   include_carousel_children=spec.include_carousel_children,
                                   include_account_metrics=("followers_count" in spec.account_fields))
    if spec != canonical:
        raise ValueError("Untrusted request specification")
    if spec.lane != credentials.lane:
        raise ValueError("Credential reference lane mismatch")
    if (not _ENV.fullmatch(credentials.token_env_name)
            or not _ENV.fullmatch(credentials.ig_user_id_env_name)
            or credentials.token_env_name == credentials.ig_user_id_env_name):
        raise ValueError("Invalid credential reference names")
    if not validate_identity_value(caller_ig_id):
        raise ValueError("Invalid caller identity")
    if not isinstance(version, str) or not _VERSION.fullmatch(version):
        raise ValueError("Invalid Graph API version")
    return PreparedGraphRequest(
        lane=spec.lane, username=spec.username, version=version,
        ig_user_id=caller_ig_id, fields=business_discovery_field(spec),
        after_present=spec.after is not None, after=spec.after,
        token_env_name=credentials.token_env_name,
        include_carousel_children=spec.include_carousel_children,
        include_account_metrics=("followers_count" in spec.account_fields),
    )


class OfflineFixtureTransport:
    """Data-only fixture registry; cannot make a network request."""

    def __init__(self, fixtures: dict):
        if not isinstance(fixtures, dict):
            raise TypeError("Fixture registry must be a dictionary")
        self._fixtures = deepcopy(fixtures)

    def fetch(self, request: PreparedGraphRequest) -> dict:
        if not isinstance(request, PreparedGraphRequest):
            raise TypeError("Only prepared Graph requests are allowed")
        # Use only the validated cursor bound to the prepared request.
        # This fixture key is never logged, persisted or sent over HTTP.
        key = (request.lane, request.username, request.after)
        if key not in self._fixtures:
            raise KeyError("Missing offline fixture")
        value = self._fixtures[key]
        if not isinstance(value, dict):
            raise ValueError("Offline fixture response must be a dictionary")
        return deepcopy(value)
