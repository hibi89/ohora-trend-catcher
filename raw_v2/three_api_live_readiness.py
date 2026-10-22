"""Offline-only G0-G6 readiness evidence evaluator.

This function NEVER grants runtime permission to call Meta or write production
data. All gates are evidence indicators; user approval is a separate action.
"""
from collections.abc import Mapping

GATES = tuple(f"G{i}" for i in range(7))


def _snapshot_evidence(evidence):
    """Copy a mapping once; custom mappings may be stateful or raise."""
    try:
        if not isinstance(evidence, Mapping):
            return None
        snapshot = evidence.copy() if type(evidence) is dict else dict(evidence.items())
        if any(type(key) is not str for key in snapshot):
            return None
        return snapshot
    except Exception:
        # Malformed or unstable evidence remains blocked and is never echoed.
        return None


def evaluate_live_readiness(evidence):
    """Return a fail-closed, redacted diagnostic without side effects.

    Only literal True is accepted as a gate's evidence; truthy strings,
    numbers, missing keys and malformed input are rejected. Unknown fields
    are rejected rather than interpreted as approval. The supplied mapping
    is snapshotted once so stateful mappings cannot change between checks.
    """
    snapshot = _snapshot_evidence(evidence)
    try:
        valid = (
            snapshot is not None
            and set(snapshot) == set(GATES)
            and all(type(snapshot[gate]) is bool for gate in GATES)
        )
    except Exception:
        valid = False
        snapshot = None
    verified = tuple(g for g in GATES if valid and snapshot[g] is True)
    missing = tuple(g for g in GATES if g not in verified)
    return {
        "mode": "OFFLINE_READINESS_EVIDENCE_ONLY",
        "valid_input": valid,
        "verified_gates": list(verified),
        "blocked_gates": list(missing),
        "all_evidence_present": valid and not missing,
        "runtime_authorized": False,
        "live_meta_verified": False,
        "unattended_execution_enabled": False,
        "reason": (
            "EVIDENCE_COMPLETE_APPROVAL_STILL_REQUIRED"
            if valid and not missing else
            "INVALID_EVIDENCE" if not valid else
            "MISSING_EVIDENCE"
        ),
    }
