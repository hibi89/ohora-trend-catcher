"""Offline-only G0-G6 readiness evidence evaluator.

This function NEVER grants runtime permission to call Meta or write production
data. All gates are evidence indicators; user approval is a separate action.
"""
from collections.abc import Mapping

GATES = tuple(f"G{i}" for i in range(7))


def evaluate_live_readiness(evidence):
    """Return a fail-closed, redacted diagnostic without side effects.

    Only literal True is accepted as a gate's evidence; truthy strings,
    numbers, missing keys and malformed input are rejected. Unknown fields
    are rejected rather than interpreted as approval.
    """
    valid = (
        isinstance(evidence, Mapping)
        and set(evidence) == set(GATES)
        and all(type(evidence[gate]) is bool for gate in GATES)
    )
    verified = tuple(g for g in GATES if valid and evidence[g] is True)
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
