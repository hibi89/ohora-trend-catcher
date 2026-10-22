"""Read-only, credential-free P50 status contract for an n8n polling bridge.

Prints exactly one JSON object. Never calls Meta, alters SQLite, starts jobs,
or clears quota holds. This is a LOCAL CLI, not a network endpoint.
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path

from three_api_p50_cycle import _paths, history_status, quota_status
from three_api_p50_status import inspect_p50


def build_bridge_status(workspace):
    latest = inspect_p50(workspace)
    history = history_status(workspace)
    quota = quota_status(workspace)
    lanes = quota.get("quota_lanes", [])
    holds = [lane["lane"] for lane in lanes if lane["halted"]]
    # A missing or malformed quota state must never be represented as ready.
    ready = len(lanes) == 3 and not holds and quota.get("quota") == "read_only"
    return {
        "schema": "ohora.p50.bridge.status.v1",
        "source": "local_read_only",
        "meta_calls": 0,
        "writes": 0,
        "collection_authorized": False,
        "all_lanes_ready": ready,
        "held_lanes": holds,
        "quota_recovery_verified": False,
        "quota": quota,
        "latest": latest,
        "history": history,
    }


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--workspace", type=Path, default=_paths()[1])
    args = parser.parse_args(argv)
    try:
        result = build_bridge_status(args.workspace)
    except (OSError, ValueError, RuntimeError, TypeError) as exc:
        print(json.dumps({
            "schema": "ohora.p50.bridge.status.v1",
            "status": "unavailable",
            "error_type": type(exc).__name__,
            "collection_authorized": False,
            "meta_calls": 0,
            "writes": 0,
        }, sort_keys=True))
        return 2
    print(json.dumps(result, ensure_ascii=False, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
