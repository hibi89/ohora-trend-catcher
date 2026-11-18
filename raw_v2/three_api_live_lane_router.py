"""Explicit A/B/C Meta transport router for a future approved serial runner.

No credentials, network sender, coordinator lease, or scheduler is created here.
A caller must inject three independently configured MetaTransport instances.
This module does not authorize Meta calls by itself.
"""
from __future__ import annotations

from three_api_transport import MetaTransport


class LaneTransportConfigurationError(RuntimeError):
    """Safe configuration denial; no secret or identity values in messages."""


class ThreeLaneMetaTransport:
    """Route exactly one already-validated request to its configured lane."""

    def __init__(self, transports):
        if type(transports) is not dict or set(transports) != {"A", "B", "C"}:
            raise LaneTransportConfigurationError("Exactly A/B/C transports required")
        if any(type(transports[lane]) is not MetaTransport for lane in ("A", "B", "C")):
            raise LaneTransportConfigurationError("Each lane requires an explicit MetaTransport")
        if any(transports[lane]._credentials.lane != lane for lane in ("A", "B", "C")):
            raise LaneTransportConfigurationError("Transport credential lane mismatch")
        if len({id(transports[lane]) for lane in ("A", "B", "C")}) != 3:
            raise LaneTransportConfigurationError("Transport instances must be distinct")
        if len({transports[lane]._caller_ig_id for lane in ("A", "B", "C")}) != 3:
            raise LaneTransportConfigurationError("Caller identities must be distinct")
        if len({transports[lane]._token for lane in ("A", "B", "C")}) != 3:
            raise LaneTransportConfigurationError("Lane tokens must be distinct")
        self._lanes = dict(transports)

    def send(self, request):
        from three_api_runtime_contract import RequestEnvelope
        if not isinstance(request, RequestEnvelope):
            raise LaneTransportConfigurationError("Validated request required")
        if request.phase not in ("front", "catchup"):
            raise LaneTransportConfigurationError("History transport is not authorized")
        transport = self._lanes.get(request.lane)
        if transport is None or request.target_ig_user_id != transport._caller_ig_id:
            raise LaneTransportConfigurationError("Request caller identity mismatch")
        return transport.send(request)
