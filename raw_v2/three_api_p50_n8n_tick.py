"""n8n adapter for the existing isolated P50 pilot; never implements collection."""
from __future__ import annotations

import argparse
from datetime import date, datetime, timedelta, timezone
import json
import os
from pathlib import Path
import sqlite3
import sys


KST = timezone(timedelta(hours=9))


def _read_only_db(path: Path):
    if path.is_symlink():
        raise RuntimeError("P50 store symlink refused")
    if not path.exists():
        return None
    uri = path.resolve().as_uri() + "?mode=ro"
    db = sqlite3.connect(uri, uri=True, timeout=3)
    db.row_factory = sqlite3.Row
    return db


def _tables(db):
    return {row[0] for row in db.execute(
        "SELECT name FROM sqlite_master WHERE type='table'")}


def _snapshot(workspace, *, day: date):
    root = Path(workspace).expanduser()
    if root.is_symlink() or root.name.lower() != "p50":
        raise RuntimeError("Dedicated P50 workspace required")
    raw_path = root / "p50_raw.sqlite"
    control_path = root / day.isoformat() / "coordinator.sqlite"
    capture_path = root / day.isoformat() / "capture.sqlite"
    opened = []
    try:
        raw, control, capture = (_read_only_db(p) for p in
                                 (raw_path, control_path, capture_path))
        opened.extend(db for db in (raw, control, capture) if db is not None)
        raw_ids = set()
        contract_ids = set()
        observations = 0
        if raw is not None:
            tables = _tables(raw)
            if "post_observations" not in tables:
                raise RuntimeError("P50 RAW receipt schema unavailable")
            receipt_tables = {"three_api_raw_receipts", "three_api_contract_receipts"}
            if receipt_tables & tables and not receipt_tables <= tables:
                raise RuntimeError("P50 RAW contract receipt schema mismatch")
            if receipt_tables <= tables:
                raw_ids = {row[0] for row in raw.execute(
                    "SELECT capture_id FROM three_api_raw_receipts")}
                contract_ids = {row[0] for row in raw.execute(
                    "SELECT capture_id FROM three_api_contract_receipts")}
            observations = raw.execute(
                "SELECT COUNT(*) FROM post_observations").fetchone()[0]
        done_jobs = 0
        if control is not None:
            if "jobs" not in _tables(control):
                raise RuntimeError("P50 coordinator schema unavailable")
            done_jobs = control.execute(
                "SELECT COUNT(*) FROM jobs WHERE state='done'").fetchone()[0]
        capture_ids = set()
        acked = set()
        conflicts = 0
        if capture is not None:
            if "captures" not in _tables(capture):
                raise RuntimeError("P50 Capture schema unavailable")
            for row in capture.execute(
                    "SELECT capture_id,processed_receipt FROM captures"):
                capture_ids.add(row["capture_id"])
                if row["processed_receipt"] is not None:
                    acked.add(row["capture_id"])
                    if row["processed_receipt"] != "rawv2:" + row["capture_id"]:
                        conflicts += 1
        return {"raw_ids": raw_ids, "observations": observations,
                "contract_ids": contract_ids,
                "done_jobs": done_jobs, "capture_ids": capture_ids,
                "acked_capture_ids": acked, "ack_conflicts": conflicts}
    finally:
        for db in opened:
            db.close()


def _load_pilot():
    engine_root = os.environ.get("OHORA_P50_ENGINE_ROOT")
    if not isinstance(engine_root, str) or not engine_root:
        raise RuntimeError("Pinned P50 engine root is not configured")
    raw_v2_path = Path(engine_root).expanduser().resolve() / "raw_v2"
    if not (raw_v2_path / "three_api_p50_manual_pilot.py").is_file():
        raise RuntimeError("Pinned P50 pilot entrypoint is unavailable")
    raw_v2 = str(raw_v2_path)
    if raw_v2 not in sys.path:
        sys.path.insert(0, raw_v2)
    import three_api_p50_manual_pilot as pilot
    return pilot


def tick(source, workspace, *, live=False, approved=False, credentials_dir=None,
         environment=None, now=None, pilot_runner=None, environment_loader=None):
    """Invoke one existing P50 pilot step and verify its durable write chain."""
    if type(live) is not bool or type(approved) is not bool:
        raise ValueError("Boolean live gate required")
    if live and not approved:
        return {"action": "WAIT_EXPLICIT_LIVE_APPROVAL", "mode": "P50_WAIT",
                "status": None, "committed_pages": 0, "recovered_pages": 0,
                "account_failures": 0, "attempted_steps": 0,
                "live_meta_called": False,
                "storage_verification": {"status": "not_started"}}
    clock = now or datetime.now(timezone.utc)
    day = clock.astimezone(KST).date()
    env = os.environ if environment is None else environment
    if pilot_runner is None or environment_loader is None:
        pilot = _load_pilot()
        pilot_runner = pilot_runner or pilot.run_p50
        environment_loader = environment_loader or pilot.load_three_api_environment
    if live:
        if credentials_dir is None:
            return {"action": "MANUAL_REVIEW", "mode": "P50_CONFIG_ERROR",
                    "status": None, "committed_pages": 0, "recovered_pages": 0,
                    "account_failures": 0, "attempted_steps": 0,
                    "live_meta_called": None,
                    "storage_verification": {"status": "credentials_unavailable"}}
        values = environment_loader(credentials_dir, base_environment=env)
        before = _snapshot(workspace, day=day)
    else:
        values = env
        result = pilot_runner(source, workspace, live=False, approved=False,
                              environment=values, max_pages=1)
        return {"action": "OFFLINE_PREFLIGHT", "mode": result.get("mode", "unknown"),
                "status": result.get("status"), "committed_pages": 0,
                "recovered_pages": 0, "account_failures": 0,
                "attempted_steps": 0, "live_meta_called": False,
                "storage_verification": {"status": "not_required"}}
    try:
        result = pilot_runner(source, workspace, live=True, approved=True,
                              environment=values, max_pages=1)
    except Exception:
        return {"action": "MANUAL_REVIEW", "mode": "P50_RUNNER_ERROR",
                "status": "runner_error", "committed_pages": 0,
                "recovered_pages": 0, "account_failures": 0,
                "attempted_steps": 0, "live_meta_called": None,
                "storage_verification": {"status": "post_call_outcome_unknown"}}
    try:
        after = _snapshot(workspace, day=day)
        committed = result.get("committed_pages", 0)
        recovered = result.get("recovered_pages", 0)
        done_delta = after["done_jobs"] - before["done_jobs"]
        raw_delta = len(after["raw_ids"] - before["raw_ids"])
        new_raw_ids = after["raw_ids"] - before["raw_ids"]
        new_raw_acknowledged = new_raw_ids <= after["acked_capture_ids"]
        pages_done = committed + recovered
        verified = (
            type(committed) is int and committed >= 0 and
            type(recovered) is int and recovered >= 0 and
            done_delta == pages_done and
            committed <= raw_delta <= pages_done and
            new_raw_acknowledged and after["ack_conflicts"] == 0 and
            after["raw_ids"] <= after["contract_ids"]
        )
        storage = {"status": "verified" if verified else "receipt_mismatch",
                   "receipt_delta": raw_delta, "done_job_delta": done_delta,
                   "observation_delta": after["observations"] - before["observations"],
                   "capture_acknowledged": new_raw_acknowledged,
                   "ack_conflicts": after["ack_conflicts"],
                   "durable_receipts": len(after["raw_ids"]),
                   "post_observations": after["observations"]}
    except Exception:
        storage = {"status": "readback_unavailable"}
        verified = False
    status = result.get("last_status")
    called = True if status in ("page_committed", "recovered", "account_failed") else (
        False if status in ("no_ready_work", "serial_busy") else None)
    action = "LIVE_TICK_COMPLETE" if verified else "MANUAL_REVIEW"
    return {"action": action, "mode": "P50_EXPLICIT_SERIAL", "status": status,
            "committed_pages": result.get("committed_pages", 0),
            "recovered_pages": result.get("recovered_pages", 0),
            "account_failures": result.get("account_failures", 0),
            "attempted_steps": result.get("attempted_steps", 0),
            "live_meta_called": called, "storage_verification": storage}


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", type=Path, required=True)
    parser.add_argument("--workspace", type=Path, required=True)
    parser.add_argument("--credentials-dir", type=Path)
    parser.add_argument("--live", action="store_true")
    parser.add_argument("--confirm-owner-approval", action="store_true")
    args = parser.parse_args(argv)
    try:
        result = tick(args.source, args.workspace, live=args.live,
                      approved=args.confirm_owner_approval,
                      credentials_dir=args.credentials_dir)
    except Exception:
        result = {"action": "MANUAL_REVIEW", "mode": "P50_ADAPTER_ERROR",
                  "status": "adapter_error", "committed_pages": 0,
                  "recovered_pages": 0, "account_failures": 0,
                  "attempted_steps": 0, "live_meta_called": None,
                  "storage_verification": {"status": "adapter_error"}}
    print(json.dumps(result, ensure_ascii=False, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
