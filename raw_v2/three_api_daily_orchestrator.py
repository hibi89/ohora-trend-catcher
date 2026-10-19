"""Explicit-step, synthetic-only daily collection orchestrator for 294 accounts.

This is NOT a daemon or a live Meta scheduler. A call to step() processes at
most one fabricated page from a fixed in-memory dictionary. No credentials,
HTTP transport, timer, production RAW database or imported production inputs.
The verified 294-account source/day binding, ordered staging, KST ledger and
isolated RAW mirror are reconciled *before* allowing the next capture.

Phase order across the ENTIRE roster: front pages (up to 3 x 4 media) for
every account; oldest outstanding catch-up; then history eligibility, which
is explicitly held for a future separately reviewed execution path.

Three separate SQLite stores cannot share an atomic commit. Durable source
proof and immutable fixture observation clocks make interruption reparable
without another fixture request. Never claim live delivery or quota validation.
"""
from __future__ import annotations

from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path

from three_api_coordinator import Coordinator, LANES, stamp
from three_api_daily_gap import DailyGapLedger, _day
from three_api_daily_projection import DailyEvidenceProjector
from three_api_evidence import PageEvidenceStore
from three_api_meta_normalizer import normalize_meta_page
from three_api_raw_mirror import OfflineRawMirror, _observation_time
from three_api_raw_projection import OfflineRawEvidenceProjector
from three_api_catchup_offline import OfflineCatchupRunner, OfflineCatchupError
from three_api_catchup_payload import (
    OfflineCatchupPayloadProjector, OfflineCatchupPayloadError,
)


class OfflineDailyScheduleError(ValueError):
    """Sanitized failure: never echo captions, IDs, cursors or credentials."""


def _fixture_digest(response):
    return hashlib.sha256(json.dumps(
        response, sort_keys=True, ensure_ascii=False, separators=(",", ":"),
        allow_nan=False,
    ).encode("utf-8")).hexdigest()


class OfflineDailyOrchestrator:
    """Single manual, offline-only scheduler tick; no autonomous operation."""

    def __init__(self, source_db, ledger_db, mirror_db, accounts, day,
                 front_fixtures: dict, catchup_fixtures: dict):
        if type(front_fixtures) is not dict or type(catchup_fixtures) is not dict:
            raise OfflineDailyScheduleError("In-memory fixture dictionaries required")
        if not isinstance(accounts, (list,tuple)) or len(accounts) != 294:
            raise OfflineDailyScheduleError("Exactly 294 snapshot accounts required")
        day = _day(day)
        paths = [Path(p) for p in (source_db,ledger_db,mirror_db)]
        if (len({p.resolve() for p in paths}) != 3
                or any(p.name.lower() == "collection.sqlite" for p in paths)
                or paths[2].name != "three_api_raw_mirror.sqlite"):
            raise OfflineDailyScheduleError("Three isolated non-production DB files required")

        self.day = day
        self.accounts = tuple(dict(a) for a in accounts)
        self._front_fixtures = front_fixtures
        self._catchup_fixtures = catchup_fixtures
        self.coordinator = None
        self.ledger = None
        self.mirror = None
        try:
            self.coordinator = Coordinator(paths[0])
            self.evidence = PageEvidenceStore(self.coordinator)
            self.ledger = DailyGapLedger(paths[1])
            self.daily = DailyEvidenceProjector(
                self.coordinator,self.ledger,list(self.accounts),day
            )
            self.coordinator.seed()
            self.mirror = OfflineRawMirror(paths[2])
            self.raw = OfflineRawEvidenceProjector(self.daily,self.mirror)
            self.catchup_runner = OfflineCatchupRunner(
                self.ledger,self.coordinator,[a["key"] for a in self.accounts],
                catchup_fixtures
            )
            self.catchup = OfflineCatchupPayloadProjector(
                self.catchup_runner,self.mirror
            )
            self.coordinator.db.execute("""CREATE TABLE IF NOT EXISTS
              offline_fixture_clock (
                job_id INTEGER PRIMARY KEY, observed_at TEXT NOT NULL,
                payload_digest TEXT NOT NULL
              )""")
            for account in self.accounts:
                self.ledger.begin_day(account["key"],day)
        except BaseException:
            self.close()
            raise

    def close(self):
        for name in ("mirror","ledger"):
            obj = getattr(self,name,None)
            if obj is not None:
                obj.close()
                setattr(self,name,None)
        if self.coordinator is not None:
            self.coordinator.db.close()
            self.coordinator = None

    def reconcile(self):
        """Recover every completed page to RAW mirror before any new fetch."""
        self.daily.reconcile_all()
        times={}
        for row in self.coordinator.db.execute(
            """SELECT e.account_ordinal,e.page,c.observed_at
               FROM page_evidence e
               LEFT JOIN offline_fixture_clock c ON e.job_id=c.job_id"""
        ):
            if row["observed_at"] is None:
                raise OfflineDailyScheduleError("Committed page lacks capture evidence")
            times[(row["account_ordinal"],row["page"])]=row["observed_at"]
        self.raw.reconcile_all(times)
        # The catch-up inbox and gap checkpoint commit in separate operations;
        # replay a staged inbox before examining phase decisions.
        self.catchup_runner.reconcile_pending()
        self.catchup.reconcile_all()
        return self.coverage()

    def coverage(self):
        """Readable phase/coverage counters, never a completeness guarantee."""
        self.daily._verify_binding()
        actions=self.ledger.next_actions([x["key"] for x in self.accounts],self.day)
        rows=list(self.ledger.db.execute(
            "SELECT status,front_pages,catch_pages FROM daily_window WHERE day=?",
            (self.day,)
        ))
        statuses={}
        for row in rows:
            statuses[row["status"]]=statuses.get(row["status"],0)+1
        phases={}
        for action in actions:
            phases[action["phase"]]=phases.get(action["phase"],0)+1
        incomplete=sum(row["status"]=="front_open" for row in rows)
        oldest=self.ledger.db.execute(
            """SELECT COUNT(*) FROM daily_window
               WHERE day<=? AND status IN ('catchup_pending','needs_review')""",
            (self.day,)
        ).fetchone()[0]
        return {
            "mode":"SYNTHETIC_OFFLINE_MANUAL_ONLY", "day":self.day,
            "accounts":len(self.accounts),"daily_rows":len(rows),
            "front_incomplete":incomplete,"front_pages":sum(x["front_pages"] for x in rows),
            "unresolved_gap_windows":oldest,"action_counts":phases,
            "day_status_counts":statuses,
            "mirror_posts":self.mirror.summary()["raw_posts"],
            "mirror_pending_pages":self.mirror.summary()["pending_pages"]
        }

    def _release_unused_lease(self, job):
        # Fixture omission is not a successful attempt. Do not wait 15 mins.
        with self.coordinator.db:
            self.coordinator.db.execute(
                """UPDATE jobs SET state='pending', lease_id=NULL, leased_until=NULL
                   WHERE id=? AND state='leased' AND lease_id=?""",
                (job["id"],job["lease_id"])
            )

    def _front_step(self, lane, now):
        job=self.coordinator.lease(lane,now=now)
        if job is None:
            return {"status":"front_lane_unavailable","lane":lane}
        ordinal,page=job["account_ordinal"],job["page"]
        if ordinal not in range(1,295) or page not in (1,2,3):
            self.coordinator.observe_usage(lane,None,now=now)
            return {"status":"front_blocked","lane":lane}
        try:
            cursor=self.evidence.required_cursor(ordinal,page)
            fixture=self._front_fixtures.get((ordinal,page,cursor))
            if fixture is None:
                self._release_unused_lease(job)
                return {"status":"front_fixture_missing","lane":lane,
                        "account_ordinal":ordinal,"page":page}
            if not isinstance(fixture,dict) or set(fixture)!={"payload","observed_at"}:
                raise OfflineDailyScheduleError("Invalid synthetic front fixture")
            # Validate and remove unapproved fields BEFORE durable persistence.
            normalized=normalize_meta_page(fixture["payload"],
                                          requested_cursor=cursor)
            observed=_observation_time(fixture["observed_at"])
            digest=_fixture_digest(normalized)
            db=self.coordinator.db
            db.execute("BEGIN IMMEDIATE")
            try:
                old=db.execute("SELECT observed_at,payload_digest FROM offline_fixture_clock "
                               "WHERE job_id=?",(job["id"],)).fetchone()
                if old is not None and tuple(old)!=(observed,digest):
                    raise OfflineDailyScheduleError("Immutable capture attempt changed")
                db.execute(
                    "INSERT OR IGNORE INTO offline_fixture_clock VALUES (?,?,?)",
                    (job["id"],observed,digest)
                )
                db.execute("COMMIT")
            except BaseException:
                db.execute("ROLLBACK")
                raise
            self.evidence.commit_staged_page(
                job["id"],job["lease_id"],normalized,
                requested_cursor=cursor,now=now
            )
            self.daily.project_account(ordinal)
            self.raw.project_page(ordinal,page,observed_at=observed)
            return {"status":"front_committed","lane":lane,
                    "account_ordinal":ordinal,"page":page,
                    "observed_posts":len(normalized["data"])}
        except Exception:
            # Leave the durable failed attempt for inspection; halt only lane.
            self.coordinator.observe_usage(lane,None,now=now)
            return {"status":"front_blocked","lane":lane}

    def step(self, lane, *, now, stage_only=False):
        """One explicit fixture-only step; never runs autonomously or live.

        stage_only applies only to catch-up; front replay safety is provided
        by the durable capture clock and source-page proof.
        """
        if lane not in LANES or not isinstance(now,datetime) or now.tzinfo is None:
            raise OfflineDailyScheduleError("Lane and timezone-aware time required")
        if type(stage_only) is not bool:
            raise OfflineDailyScheduleError("stage_only must be boolean")
        now=now.astimezone(timezone.utc)
        try:
            status=self.reconcile()
        except Exception:
            return {"status":"recovery_blocked","lane":lane}
        acts=self.ledger.next_actions([a["key"] for a in self.accounts],self.day)
        if any(a["phase"]=="front" for a in acts):
            if stage_only:
                return {"status":"front_stage_only_unsupported"}
            return self._front_step(lane,now)
        if any(a["phase"] in ("catchup","reanchor","needs_review")
               for a in acts):
            if any(a["phase"]=="catchup" for a in acts):
                out=self.catchup.run_one(lane,self.day,now=now,stage_only=stage_only)
                return {"status":out["status"],"phase":"catchup",
                        "gap_status":out.get("gap_status")}
            return {"status":"manual_gap_review_required"}
        # History is only an eligibility decision. This scheduler never
        # quietly sends history requests, even if spare API budget exists.
        if any(a["phase"].startswith("history") for a in acts):
            return {"status":"history_held_for_review",
                    "eligible_accounts":len(acts)}
        return {"status":"no_eligible_work"}
