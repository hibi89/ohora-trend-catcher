"""Opt-in, offline-only runtime credential resolution and HTTP handoff simulation.

No os.environ reads, .env parsing, sockets, urllib, HTTP clients, or production
database operations are performed here. The caller explicitly supplies an
environment-like mapping of secret VALUES. Credentials are read just before
the synthetic request, never written to a fixture, database or result.

IMPORTANT: Simulated bearer-header construction is NOT live Meta authentication.
A real transport is deliberately not implemented and requires user approval.
"""
from __future__ import annotations

from collections.abc import Mapping
from urllib.parse import parse_qs

from three_api_credentials import CredentialReference, make_references
from three_api_http_preflight import (
    OfflineFixtureTransport,
    PreparedGraphRequest,
    prepare_graph_request,
)
from three_api_request_spec import build_request_spec


class OfflineRuntimeError(ValueError):
    """Safe operational failure: never embed credential values or IDs."""


class _MemoryOnlyWireTransport:
    """Mock wire receiver; has no method for making network requests."""

    def __init__(self, fixtures: dict):
        self._fixtures = OfflineFixtureTransport(fixtures)

    def deliver(self, prepared: PreparedGraphRequest, wire: dict) -> dict:
        """Check the ephemeral request shape and return synthetic data."""
        if not isinstance(prepared, PreparedGraphRequest) or not isinstance(wire, dict):
            raise OfflineRuntimeError("Invalid simulated wire request")
        headers = wire.get("headers")
        if not isinstance(headers, dict):
            raise OfflineRuntimeError("Invalid simulated request headers")
        auth = headers.get("Authorization")
        if (not isinstance(auth, str) or not auth.startswith("Bearer ")
                or not auth[7:] or len(headers) != 2
                or headers.get("Accept") != "application/json"):
            raise OfflineRuntimeError("Invalid simulated authorization")
        unsigned = prepared.unsigned_parts()
        if (wire.get("method") != "GET"
                or wire.get("url") != unsigned["url"]
                or wire.get("query_string") != unsigned["query_string"]
                or "access_token" in unsigned["query_string"].lower()
                or "access_token" in unsigned["url"].lower()):
            raise OfflineRuntimeError("Invalid simulated Graph request")
        # No storage/logging of headers, bearer token, or wire request.
        return self._fixtures.fetch(prepared)


class OfflineCredentialedFetcher:
    """Callable for OfflineThreeApiRunner(..., envelope_fixture=True).

    Inject a mapping such as a synthetic dict in tests. No ambient secrets
    are read: a future *approved* entrypoint must explicitly supply them.
    """

    def __init__(self, references: Mapping[str, CredentialReference],
                 environment: Mapping[str, str], fixtures: dict,
                 *, version: str = "v26.0"):
        if not isinstance(references, Mapping) or set(references) != {"A", "B", "C"}:
            raise OfflineRuntimeError("Exactly three credential references required")
        if not isinstance(environment, Mapping):
            raise OfflineRuntimeError("Explicit environment mapping required")
        mapping = {}
        for lane in ("A", "B", "C"):
            ref = references[lane]
            if not isinstance(ref, CredentialReference) or ref.lane != lane:
                raise OfflineRuntimeError("Invalid lane credential reference")
            mapping[lane] = {
                "token_env_name": ref.token_env_name,
                "ig_user_id_env_name": ref.ig_user_id_env_name,
            }
        try:
            self._refs = make_references(mapping)
        except (ValueError, TypeError) as exc:
            raise OfflineRuntimeError("Invalid credential reference mapping") from None
        self._environment = environment
        self._transport = _MemoryOnlyWireTransport(fixtures)
        self._version = version

    def __repr__(self):
        return "OfflineCredentialedFetcher(mode='SYNTHETIC_ONLY')"

    def __call__(self, intent: dict, cursor: str | None) -> dict:
        if not isinstance(intent, dict):
            raise OfflineRuntimeError("Invalid offline work intent")
        lane = intent.get("lane")
        if (lane not in ("A", "B", "C")
                or intent.get("mode") != "OFFLINE_INTENT_NO_API_CALLS"
                or intent.get("meta_request_enabled") is not False
                or type(intent.get("post_limit")) is not int
                or intent["post_limit"] != 4
                or type(intent.get("logical_page")) is not int
                or intent["logical_page"] not in (1, 2, 3)
                or (intent["logical_page"] == 1 and cursor is not None)
                or (intent["logical_page"] > 1 and cursor is None)):
            raise OfflineRuntimeError("Unsafe offline work intent")
        ref = self._refs[lane]
        token = self._environment.get(ref.token_env_name)
        caller = self._environment.get(ref.ig_user_id_env_name)
        # Explicit fail closed. Never echo bad values into errors or repr.
        if (not isinstance(token, str) or not (1 <= len(token) <= 4096)
                or any(ord(ch) < 33 or ord(ch) > 126 for ch in token)):
            raise OfflineRuntimeError("Lane credentials unavailable or malformed")
        try:
            spec = build_request_spec(lane, intent.get("username"),
                                      after=cursor, limit=intent["post_limit"])
            prepared = prepare_graph_request(
                spec, ref, caller, version=self._version,
            )
        except (ValueError, TypeError) as exc:
            raise OfflineRuntimeError("Invalid runtime request configuration") from None
        unsigned = prepared.unsigned_parts()
        # This dictionary exists only for the duration of this mock call.
        # Never persist, log, return, or raise it from this function.
        wire = {
            "method": unsigned["method"],
            "url": unsigned["url"],
            "query_string": unsigned["query_string"],
            "headers": {
                "Authorization": "Bearer " + token,
                "Accept": "application/json",
            },
        }
        try:
            return self._transport.deliver(prepared, wire)
        except (ValueError, KeyError, TypeError):
            # Ensure detailed lower-layer errors never expose sensitive inputs.
            raise OfflineRuntimeError("Synthetic transport failed") from None
        finally:
            wire["headers"].clear()
