"""Explicit, guarded B-lane resume after manual verification. No Meta calls.

Preserves the immutable failed request and never retries it. Operator must
supply --approve-b-resume; otherwise this is read-only. Does not dispatch work.
"""
from __future__ import annotations
import argparse
from contextlib import closing
from datetime import datetime, timezone
import json
from pathlib import Path
import sqlite3
import sys

def run(workspace: Path, day: str, approve: bool, *, now=None,
        verified_usage_percent=None, evidence_observed_at=None):
    root = workspace.expanduser()
    control = root / day / "coordinator.sqlite"
    capture = root / day / "capture.sqlite"
    if (not root.is_dir() or root.is_symlink() or not control.is_file()
            or not capture.is_file() or control.is_symlink() or capture.is_symlink()):
        raise ValueError("Existing isolated pilot stores required")
    uri = control.resolve().as_uri() + ("?mode=rw" if approve else "?mode=ro")
    with closing(sqlite3.connect(uri, uri=True, timeout=15, isolation_level=None)) as db:
        db.row_factory = sqlite3.Row
        if approve:
            db.execute("BEGIN IMMEDIATE")
        try:
            lane = db.execute(
                "SELECT halted,reason,ready_at FROM lanes WHERE lane='B'"
            ).fetchone()
            candidate = db.execute(
                "SELECT j.id,j.state,j.lane,j.page,"
                "EXISTS(SELECT 1 FROM job_request_bindings b WHERE b.job_id=j.id) AS bound "
                "FROM jobs j WHERE j.lane='B' AND j.page=1 AND j.state='pending' "
                "AND NOT EXISTS (SELECT 1 FROM job_request_bindings b WHERE b.job_id=j.id) "
                "ORDER BY j.account_ordinal LIMIT 1"
            ).fetchone()
            if lane is None or candidate is None:
                raise ValueError("Required B-lane evidence missing")
            db.execute("ATTACH DATABASE ? AS capture_ro",
                       (capture.resolve().as_uri()+"?mode=ro",))
            evidence_rows = db.execute(
                "SELECT r.received_at,r.http_status,r.safe_usage_json "
                "FROM capture_ro.response_evidence r "
                "JOIN capture_ro.intents i USING(request_id) "
                "JOIN capture_ro.captures cap USING(request_id) "
                "JOIN job_request_bindings b USING(request_id) "
                "JOIN jobs j ON j.id=b.job_id "
                "WHERE j.lane='B' AND j.state='done' AND i.outcome='captured' "
                "AND cap.processed_receipt IS NOT NULL "
                "AND r.classification='safe' AND r.http_status BETWEEN 200 AND 299 "
                "ORDER BY r.received_at DESC"
            ).fetchall()
            now = now or datetime.now(timezone.utc)
            ready = datetime.fromisoformat(lane["ready_at"])
            fresh_safe_usage = None
            for evidence in evidence_rows:
                try:
                    observed_at = datetime.fromisoformat(
                        evidence["received_at"].replace("Z", "+00:00"))
                    metrics = json.loads(evidence["safe_usage_json"])
                    usage = metrics.get("max_usage_percent")
                except (ValueError, TypeError, AttributeError):
                    continue
                if (observed_at.tzinfo is not None and observed_at >= ready
                        and type(usage) is int and 0 <= usage < 85):
                    fresh_safe_usage = usage
                    evidence_source = "capture_response"
                    break
            if verified_usage_percent is not None or evidence_observed_at is not None:
                if (type(verified_usage_percent) is not int
                        or not isinstance(evidence_observed_at, str)):
                    raise ValueError("Complete operator quota evidence required")
                try:
                    manual_observed_at = datetime.fromisoformat(evidence_observed_at)
                except ValueError:
                    raise ValueError("Invalid operator quota evidence time") from None
                if (manual_observed_at.tzinfo is None
                        or manual_observed_at.utcoffset() is None
                        or verified_usage_percent < 0
                        or verified_usage_percent > 100):
                    raise ValueError("Invalid operator quota evidence")
                if (manual_observed_at >= ready and manual_observed_at <= now
                        and verified_usage_percent < 85):
                    fresh_safe_usage = verified_usage_percent
                    evidence_source = "operator_attested_meta_dashboard"
            safe = (
                fresh_safe_usage is not None
                and candidate["lane"] == "B"
                and candidate["page"] == 1
                and candidate["state"] == "pending"
                and candidate["bound"] == 0
                and lane["halted"] == 1
                and lane["reason"] == "live_pending_usage"
                and ready <= now
            )
            if not safe:
                raise ValueError("Guarded B-lane resume conditions not met")
            if approve:
                if evidence_source == "operator_attested_meta_dashboard":
                    db.execute("""CREATE TABLE IF NOT EXISTS lane_resume_evidence(
                        evidence_id TEXT PRIMARY KEY,
                        lane TEXT NOT NULL CHECK(lane='B'),
                        source TEXT NOT NULL CHECK(source='operator_attested_meta_dashboard'),
                        observed_at TEXT NOT NULL,
                        usage_percent INTEGER NOT NULL CHECK(usage_percent BETWEEN 0 AND 84),
                        recorded_at TEXT NOT NULL
                    )""")
                    evidence_id = "b-resume-" + manual_observed_at.astimezone(
                        timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")
                    db.execute(
                        "INSERT INTO lane_resume_evidence VALUES(?,?,?,?,?,?)",
                        (evidence_id, "B", evidence_source,
                         manual_observed_at.isoformat(), fresh_safe_usage,
                         now.isoformat()))
                changed = db.execute(
                    "UPDATE lanes SET halted=0,reason=NULL "
                    "WHERE lane='B' AND halted=1 AND reason='live_pending_usage' "
                    "AND ready_at<=?",
                    (now.isoformat(),)
                ).rowcount
                if changed != 1:
                    raise ValueError("B lane state changed")
                db.execute("COMMIT")
            return {
                "mode": "MANUAL_B_RESUME" if approve else "READ_ONLY_B_RESUME_PREFLIGHT",
                "safe_conditions": True,
                "lane_resumed": bool(approve),
                "fresh_safe_usage_percent": fresh_safe_usage,
                "usage_evidence_source": evidence_source,
                "failed_requests_replayed": False,
                "candidate_job_unbound": True,
                "meta_called": False,
            }
        except BaseException:
            if approve:
                db.execute("ROLLBACK")
            raise

def main():
    p = argparse.ArgumentParser()
    p.add_argument("--workspace", type=Path, required=True)
    p.add_argument("--day", required=True)
    p.add_argument("--approve-b-resume", action="store_true")
    p.add_argument("--verified-usage-percent", type=int,
                   help="Operator-verified Meta dashboard usage for lane B")
    p.add_argument("--evidence-observed-at",
                   help="ISO-8601 timestamp with timezone from the Meta usage evidence")
    a = p.parse_args()
    try:
        print(json.dumps(run(
            a.workspace, a.day, a.approve_b_resume,
            verified_usage_percent=a.verified_usage_percent,
            evidence_observed_at=a.evidence_observed_at), sort_keys=True))
    except (ValueError,sqlite3.Error,OverflowError):
        sys.exit("B resume refused: safety conditions not met.")

if __name__ == "__main__":
    main()
