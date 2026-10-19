"""Guarded bridge from leased OHORA jobs to a Graph HTTP client.

OFFLINE INTEGRATION ONLY. No CLI, scheduler, environment/file reader, or sender
is installed. The default GraphHTTPClient() is blocked. Tests may inject an
approved fake sender, but this class does not authorize live Meta requests.

A failed request halts its lane and leaves its lease unacknowledged, preserving
cursor and staging evidence. Recovery is an explicit operator action after
the persisted cooldown; there is no automatic retry or recovery.
"""
from __future__ import annotations

from collections.abc import Mapping
from pathlib import Path

from three_api_credentials import CredentialReference, make_references
from three_api_graph_http import GraphHTTPClient, GraphHTTPBlocked, GraphTransportError
from three_api_http_preflight import prepare_graph_request
from three_api_offline_runner import OfflineThreeApiRunner
from three_api_request_spec import build_request_spec


class GuardedExecutionError(ValueError):
    """Sanitized configuration failure; never carries a token or cursor."""


class _GuardedPageFetcher:
    def __init__(self, references, environment, client, version):
        if not isinstance(references, Mapping) or set(references) != {"A", "B", "C"}:
            raise GuardedExecutionError("Exactly three credential references required")
        if not isinstance(environment, Mapping):
            raise GuardedExecutionError("Explicit credentials mapping required")
        if not isinstance(client, GraphHTTPClient):
            raise GuardedExecutionError("A gated GraphHTTPClient is required")
        mapping = {}
        for lane in ("A", "B", "C"):
            ref = references[lane]
            if not isinstance(ref, CredentialReference) or ref.lane != lane:
                raise GuardedExecutionError("Invalid lane reference")
            mapping[lane] = {
                "token_env_name": ref.token_env_name,
                "ig_user_id_env_name": ref.ig_user_id_env_name,
            }
        try:
            self._references = make_references(mapping)
        except (ValueError, TypeError):
            raise GuardedExecutionError("Invalid lane reference mapping") from None
        self._environment = environment
        self._client = client
        self._version = version

    def __repr__(self):
        return "_GuardedPageFetcher(mode='OFFLINE_INTEGRATION_ONLY')"

    def __call__(self, intent, cursor):
        if not isinstance(intent, dict):
            raise GuardedExecutionError("Invalid work intent")
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
            raise GuardedExecutionError("Unsafe work intent")
        ref = self._references[lane]
        token = self._environment.get(ref.token_env_name)
        caller = self._environment.get(ref.ig_user_id_env_name)
        if (not isinstance(token, str) or not 1 <= len(token) <= 4096
                or any(ord(ch) < 33 or ord(ch) > 126 for ch in token)):
            raise GuardedExecutionError("Lane credential unavailable")
        try:
            spec = build_request_spec(lane, intent.get("username"),
                                      after=cursor, limit=4)
            prepared = prepare_graph_request(
                spec, ref, caller, version=self._version
            )
        except (ValueError, TypeError):
            raise GuardedExecutionError("Invalid Graph request configuration") from None
        # No token in exceptions, cursor logs, DB or result; HTTP client also
        # sanitizes its failures and response metadata.
        return self._client.fetch(prepared, token)


class GuardedThreeApiExecutor:
    """Isolated coordinator + fail-closed bridge, never a production collector."""

    def __init__(self, state_db, accounts, references, environment,
                 *, http_client=None, version="v26.0"):
        # A disabled transport is always the default.
        client = GraphHTTPClient() if http_client is None else http_client
        fetcher = _GuardedPageFetcher(references, environment, client, version)
        self._runner = OfflineThreeApiRunner(
            Path(state_db), accounts, fetcher, envelope_fixture=True,
        )

    def close(self):
        self._runner.close()

    def seed(self):
        self._runner.seed()

    def summary(self):
        return self._runner.coordinator.summary()

    def run_one(self, lane, *, now=None):
        """One attempt, never retry. Any exception halts only this lane.

        On failure the leased job remains unacknowledged. After the 65-minute
        cooldown and the 15-minute lease expiry, an operator must separately
        verify conditions and call reset_after_verified_cooldown.
        """
        if lane not in ("A", "B", "C"):
            raise GuardedExecutionError("Invalid lane")
        try:
            return self._runner.run_one(lane, now=now)
        except (GraphHTTPBlocked, GraphTransportError):
            reason = "transport_unavailable"
        except Exception:
            # Defensive fail closed for unexpected parser/checkpoint/fixture
            # failures without leaking exception messages or credentials.
            reason = "execution_failure"
        self._runner.coordinator.observe_usage(lane, None, now=now)
        return {"status": "blocked", "lane": lane, "reason": reason}

    def reset_after_verified_cooldown(self, lane, *, now=None):
        """Manual-only release. No automatic retry, quota or app validation."""
        if lane not in ("A", "B", "C"):
            raise GuardedExecutionError("Invalid lane")
        return self._runner.coordinator.reset_after_verified_cooldown(lane, now=now)
