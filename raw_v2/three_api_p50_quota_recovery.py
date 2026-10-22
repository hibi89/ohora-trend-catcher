"""Operator-initiated, bounded P50 quota recovery using live Meta usage evidence.

One minimal IG caller request per selected lane. Never retries, logs credentials,
changes RAW/Capture/cursors, or runs collection. Applying requires three explicit
flags; a missing/malformed/scoped-out usage header cannot open a lane.
"""
from __future__ import annotations

import argparse
from contextlib import closing
from datetime import datetime, timezone
import json
from pathlib import Path
import sqlite3
from urllib.error import HTTPError
from urllib.request import Request

from three_api_env_loader import load_three_api_environment
from three_api_graph_http import make_strict_sender
from three_api_p50_cycle import _paths
from three_api_serial_lock import serial_tick_lock
from three_api_usage import parse_usage

LANES = ("A", "B", "C")


def quota_from_caller_headers(headers, caller_id):
    """Accept only the current caller's BUC metrics, never an unrelated app."""
    raw = headers.get("x-business-use-case-usage")
    if not isinstance(raw, str):
        return None
    try:
        payload = json.loads(raw)
    except (ValueError, TypeError):
        return None
    if not isinstance(payload, dict) or caller_id not in payload:
        return None
    scoped = payload[caller_id]
    if not isinstance(scoped, list) or not scoped:
        return None
    for record in scoped:
        if (not isinstance(record, dict) or record.get("type") != "instagram"
                or record.get("estimated_time_to_regain_access") != 0):
            return None
    return parse_usage({"x-business-use-case-usage": json.dumps(scoped)})


def probe_lane(lane, environment, *, sender=None):
    """One Meta request; return safe counters only, not response contents."""
    if lane not in LANES:
        raise ValueError("Invalid lane")
    caller = environment["P6_META_IG_ID_" + lane]
    token = environment["P6_META_TOKEN_" + lane]
    version = environment["P6_META_GRAPH_VERSION"]
    request = Request(
        "https://graph.facebook.com/" + version + "/" + caller + "?fields=id",
        headers={"Authorization": "Bearer " + token, "Accept": "application/json"},
        method="GET",
    )
    opener = sender or make_strict_sender()
    try:
        with opener(request, timeout=15) as response:
            status = response.status
            headers = {k.lower(): v for k, v in response.headers.items()}
            # Do not retain the response body; only rate-limit telemetry is used.
    except HTTPError as error:
        status = error.code
        headers = {k.lower(): v for k, v in error.headers.items()}
        error.close()
    usage = quota_from_caller_headers(headers, caller) if status == 200 else None
    return {"lane": lane, "http_status": status,
            "usage_percent": usage,
            "quota_verified": usage is not None and 0 <= usage < 85}


def release_verified_lane(path, lane, observation, *, now=None):
    """Release a usage_limit hold when Meta explicitly verifies recovery.

    Caller must hold P50's serial operator lock. A successful probe is not
    permission to clear other reasons or restart an unknown HTTP request.
    A successful scoped Meta BUC probe with zero regain time supersedes the
    locally estimated cooldown, but never an explicit Meta regain hold.
    """
    now = now or datetime.now(timezone.utc)
    if (not isinstance(now, datetime) or now.utcoffset() is None
            or lane not in LANES or observation.get("lane") != lane
            or observation.get("http_status") != 200
            or observation.get("quota_verified") is not True
            or type(observation.get("usage_percent")) not in (int, float)
            or not 0 <= observation["usage_percent"] < 85):
        return False
    with closing(sqlite3.connect(str(path), timeout=15, isolation_level=None)) as db:
        db.execute("BEGIN IMMEDIATE")
        try:
            result = db.execute(
                "UPDATE lanes SET halted=0,reason=NULL,ready_at=? "
                "WHERE lane=? AND halted=1 AND reason='usage_limit'",
                (now.astimezone(timezone.utc).isoformat(), lane),
            )
            db.execute("COMMIT")
            return result.rowcount == 1
        except BaseException:
            db.execute("ROLLBACK")
            raise


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--lane", choices=LANES, required=True)
    parser.add_argument("--workspace", type=Path, default=_paths()[1])
    parser.add_argument("--credentials-dir", type=Path, required=True)
    parser.add_argument("--live", action="store_true")
    parser.add_argument("--confirm-owner-approval", action="store_true")
    parser.add_argument("--apply", action="store_true",
                        help="Explicitly release only verified below-85 usage_limit hold")
    args = parser.parse_args(argv)
    if not (args.live and args.confirm_owner_approval):
        parser.error("Live probe requires both explicit approval flags")
    root = args.workspace.expanduser()
    if not root.is_dir() or root.is_symlink() or root.name.lower() != "p50":
        parser.error("Existing P50 workspace required")
    candidates = sorted(root.glob("????-??-??/coordinator.sqlite"))
    if not candidates:
        parser.error("No existing P50 coordinator")
    path = candidates[-1]
    if path.is_symlink() or path.parent.is_symlink():
        parser.error("Unsafe P50 coordinator path")
    try:
        environment = load_three_api_environment(args.credentials_dir)
        with serial_tick_lock(root / "operator"):
            observation = probe_lane(args.lane, environment)
            observation["released"] = (
                release_verified_lane(path, args.lane, observation)
                if args.apply else False
            )
            observation["applied"] = args.apply
        print(json.dumps(observation, sort_keys=True))
        return 0 if observation["quota_verified"] else 2
    except Exception as exc:
        # Never print raw exceptions: URLs and authorization may be included.
        print(json.dumps({"status": "probe_failed",
                          "error_type": type(exc).__name__,
                          "released": False}))
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
