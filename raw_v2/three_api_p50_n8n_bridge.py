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
    states = history.get("states", {})
    daily = latest.get("daily_jobs", [])
    return {
        "summary": {
            "accounts": latest.get("accounts"),
            "unique_posts": latest.get("unique_posts"),
            "post_observations": latest.get("post_observations"),
            "contract_receipts": latest.get("contract_receipts"),
            "history_pages_verified": history.get("history_pages_verified", 0),
            "history_continuation_ready": states.get("continuation_ready", 0),
            "history_window_covered": states.get("window_covered", 0),
            "latest_dates": [row.get("date") for row in daily],
            "latest_jobs_done": sum(row.get("jobs", {}).get("done", 0) for row in daily),
        },
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
    parser.add_argument("--compact", action="store_true", help="Small JSON payload for n8n polling; no per-account details")
    args = parser.parse_args(argv)
    try:
        result = build_bridge_status(args.workspace)
        if args.compact:
            result.pop("history", None)
            result.pop("latest", None)
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
