"""One-shot 151 recovery in a NEW coordinator/Capture, shared isolated RAW.

Explicit live opt-in. Never opens original coordinator for writing, never retries
jobs 451/454. A/C are disabled ONLY in the new coordinator. No scheduler.
"""
from __future__ import annotations
import argparse
from contextlib import ExitStack
from datetime import datetime, timedelta, timezone
import json
import os
from pathlib import Path
import sqlite3

from collector import connect
from three_api_manual_pilot import (
    _validated_roster, _credentials, _transports, VERSION,
)
from three_api_coordinator import Coordinator
from three_api_capture import CaptureJournal
from three_api_raw_writer import ContractRawWriter
from three_api_serial_bootstrap import build_verified_serial_runner
from three_api_live_serial_step import run_bounded_serial_steps

KST = timezone(timedelta(hours=9))

def run(source, workspace, *, live=False, approved=False):
    accounts, digest = _validated_roster(source)
    if accounts[150]["username"] != "yodeebs":
        raise ValueError("Official ordinal 151 does not match reviewed account")
    root = Path(workspace).expanduser()
    if not root.is_dir() or root.is_symlink():
        raise ValueError("Existing isolated pilot required")
    root = root.resolve()
    day = datetime.now(KST).date()
    original = root / day.isoformat() / "coordinator.sqlite"
    raw_path = root / "p6_pilot_raw.sqlite"
    if not original.is_file() or not raw_path.is_file() or original.is_symlink() or raw_path.is_symlink():
        raise ValueError("Original pilot databases required")
    with sqlite3.connect(original.as_uri()+"?mode=ro",uri=True) as db:
        db.row_factory = sqlite3.Row
        lane = db.execute("SELECT halted,reason,ready_at FROM lanes WHERE lane='B'").fetchone()
        jobs = db.execute(
            "SELECT j.account_ordinal,j.state,j.lane,j.page,"
            "i.outcome FROM jobs j JOIN job_request_bindings b ON b.job_id=j.id "
            "JOIN (SELECT NULL AS outcome) i ON 1=1 WHERE j.id IN (451,454)"
        ).fetchall()
    if lane is None or len(jobs) != 2 or any(
        j["state"] != "leased" or j["lane"] != "B" or j["page"] != 1
        for j in jobs
    ):
        raise ValueError("Original immutable failures not in expected state")
    ready = datetime.fromisoformat(lane["ready_at"])
    lane_safe = ready <= datetime.now(timezone.utc)
    if not live:
        return {"mode":"READ_ONLY_RECOVERY_PREFLIGHT","ordinal":151,
                "source_accounts":len(accounts),"lane_cooldown_elapsed":lane_safe,
                "original_failure_jobs_preserved":True,"meta_called":False}
    if not approved or not lane_safe:
        raise ValueError("Approval or 65-minute lane cooldown missing")
    env = os.environ
    values = _credentials(env)
    version = env.get("P6_META_GRAPH_VERSION","v26.0")
    if not VERSION.fullmatch(version):
        raise ValueError("Invalid API version")
    # Fresh date-scoped namespace; refuse reuse, including partial attempts.
    attempt = root / ("recovery-151-" + day.isoformat())
    if attempt.exists() or attempt.is_symlink():
        raise ValueError("Recovery already attempted; no automatic retry")
    attempt.mkdir(mode=0o700)
    with ExitStack() as stack:
        raw_db = connect(raw_path,accounts,digest)
        stack.callback(raw_db.close)
        raw_db.isolation_level = None
        raw = ContractRawWriter(raw_db)
        coordinator = Coordinator(attempt/"coordinator.sqlite")
        stack.callback(coordinator.db.close)
        coordinator.seed()
        # The original 294x3 frozen plan is retained. Only the fresh
        # recovery coordinator has A/C explicitly held to target B:151.
        coordinator.db.execute(
            "UPDATE lanes SET halted=1,reason='targeted_recovery_only' "
            "WHERE lane IN ('A','C')"
        )
        capture = CaptureJournal(str(attempt/"capture.sqlite"))
        stack.callback(capture.close)
        runner = build_verified_serial_runner(
            coordinator,capture,raw,_transports(values))
        result = run_bounded_serial_steps(
            runner,accounts,max_steps=1,
            run_id="p6-recovery-151-"+day.strftime("%Y%m%d"),
            roster_digest=digest,day_kst=day,api_version=version,
            caller_ids={k:values[k][1] for k in ("A","B","C")},
            approved=True,
        )
        item = result["results"][0]
        if item.get("status") == "page_committed" and (
            item.get("lane") != "B" or item.get("account_ordinal") != 151
        ):
            raise ValueError("Unexpected account dispatched")
    return {"mode":"TARGETED_RECOVERY_ATTEMPT","status":item["status"],
            "lane":item.get("lane"),"ordinal":item.get("account_ordinal"),
            "committed_pages":result["committed_pages"]}

def main():
    p=argparse.ArgumentParser()
    p.add_argument("--source",type=Path,required=True)
    p.add_argument("--workspace",type=Path,required=True)
    p.add_argument("--live",action="store_true")
    p.add_argument("--confirm-owner-approval",action="store_true")
    a=p.parse_args()
    try:
        print(json.dumps(run(a.source,a.workspace,live=a.live,
                             approved=a.confirm_owner_approval),sort_keys=True))
    except (ValueError,RuntimeError,sqlite3.Error,KeyError,TypeError):
        p.exit(2,"Recovery stopped safely; do not rerun live automatically.\n")

if __name__=="__main__":
    main()
