"""Explicit, bounded P50 A/B/C pilot. Offline by default; no scheduler.

The original 294-account entrypoint and its stores are never used. Meta HTTP
requires BOTH --live and --confirm-owner-approval. No automatic retry.
"""
from __future__ import annotations

import argparse
from contextlib import ExitStack
from datetime import datetime, timedelta, timezone
import json
import os
from pathlib import Path

from collector import connect
from three_api_capture import CaptureJournal
from three_api_manual_pilot import (
    _credentials, _transports, _inherit_latest_lane_gates,
)
from three_api_p50_coordinator import P50Coordinator
from three_api_p50_roster import load_p50_roster, validate_p50_roster
from three_api_raw_writer import ContractRawWriter
from three_api_serial_bootstrap import build_verified_serial_runner
from three_api_live_serial_step import run_bounded_serial_steps
from three_api_env_loader import load_three_api_environment

LANES = ("A", "B", "C")


def run_p50(source, workspace, *, live=False, approved=False, environment=None,
            max_pages=1):
    if type(max_pages) is not int or not 1 <= max_pages <= 150:
        raise ValueError("P50 page bound must be 1..150")
    accounts, digest = load_p50_roster(source)
    validate_p50_roster(accounts)
    root = Path(workspace).expanduser()
    if root.is_symlink() or root.name.lower() != "p50":
        raise ValueError("Dedicated p50 workspace required")
    root = root.resolve()
    if not live:
        return {"mode": "P50_OFFLINE", "accounts": 50, "lanes": list(LANES),
                "max_pages": max_pages, "live_meta_called": False}
    if approved is not True:
        raise ValueError("Separate owner approval required")
    if environment is None:
        environment = os.environ
    values = _credentials(environment)
    version = environment.get("P6_META_GRAPH_VERSION", "v26.0")
    from three_api_manual_pilot import VERSION
    if not isinstance(version, str) or not VERSION.fullmatch(version):
        raise ValueError("Invalid Graph API version")
    day = datetime.now(timezone(timedelta(hours=9))).date()
    raw_path = root / "p50_raw.sqlite"
    control = root / day.isoformat() / "coordinator.sqlite"
    capture_path = root / day.isoformat() / "capture.sqlite"
    if any(path.is_symlink() for path in (raw_path, control, capture_path)):
        raise ValueError("Symlink store forbidden")
    if len({raw_path, control, capture_path}) != 3:
        raise ValueError("Independent P50 stores required")
    with ExitStack() as stack:
        raw_db = connect(raw_path, accounts, digest)
        stack.callback(raw_db.close)
        raw_db.isolation_level = None
        raw = ContractRawWriter(raw_db)
        coordinator = P50Coordinator(control)
        stack.callback(coordinator.db.close)
        existing_jobs = coordinator.db.execute(
            "SELECT 1 FROM jobs LIMIT 1").fetchone() is not None
        if not existing_jobs:
            _inherit_latest_lane_gates(
                coordinator, root, day.isoformat(), now=datetime.now(timezone.utc))
        coordinator.seed()
        capture = CaptureJournal(str(capture_path))
        stack.callback(capture.close)
        runner = build_verified_serial_runner(
            coordinator, capture, raw, _transports(values))
        result = run_bounded_serial_steps(
            runner, accounts, max_steps=max_pages,
            run_id="three-api-p50-" + day.strftime("%Y%m%d"),
            roster_digest=digest, day_kst=day,
            api_version=version,
            caller_ids={lane: values[lane][1] for lane in LANES},
            approved=True,
        )
    return {"mode": "P50_EXPLICIT_SERIAL", "status": result["last_status"],
            "committed_pages": result["committed_pages"],
            "recovered_pages": result["recovered_pages"],
            "account_failures": result["account_failures"],
            "attempted_steps": result["attempted_steps"]}


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", type=Path, required=True)
    parser.add_argument("--workspace", type=Path, required=True)
    parser.add_argument("--max-pages", type=int, default=1)
    parser.add_argument("--live", action="store_true")
    parser.add_argument("--confirm-owner-approval", action="store_true")
    parser.add_argument("--credentials-dir", type=Path)
    args = parser.parse_args(argv)
    try:
        env = None
        if args.credentials_dir is not None:
            if not args.live or not args.confirm_owner_approval:
                raise ValueError("Credential files require explicit live approval")
            env = load_three_api_environment(
                args.credentials_dir, base_environment=os.environ)
        result = run_p50(
            args.source, args.workspace, live=args.live,
            approved=args.confirm_owner_approval,
            environment=env, max_pages=args.max_pages)
    except KeyboardInterrupt:
        parser.exit(130, "P50 interrupted; durable receipts and cursor state retained.\n")
    except (ValueError, TypeError, RuntimeError, OSError):
        parser.exit(2, "P50 stopped safely; inspect local configuration and logs.\n")
    print(json.dumps(result, ensure_ascii=False, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
