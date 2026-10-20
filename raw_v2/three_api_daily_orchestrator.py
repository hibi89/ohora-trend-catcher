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
from three_api_raw_mirror import (
    OfflineRawMirror, OfflineRawMirrorError, _observation_time,
    _normalize_post, _canonical,
)
from three_api_raw_projection import OfflineRawProjectionError
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
        # The caller only needs recovery to finish before dispatch. Avoid a
        # second roster-wide coverage scan on every manual scheduler tick;
        # coverage() remains available explicitly for reporting and tests.
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

    def audit_storage(self):
        """Read-only cross-DB proof audit for the synthetic offline P4 boundary.

        This does not reconcile, fetch, promote, or mark work complete.
        It checks immutable source/ledger proof, capture clock, staged RAW
        digest, committed observations, and parent/child media identity.
        Failure counters intentionally never disclose posts or cursors.
        """
        self.daily._verify_binding()
        counters = {
            "verified_pages": 0, "missing_clock": 0,
            "unverified_source": 0, "missing_receipt": 0,
            "staged_receipts": 0, "receipt_mismatch": 0,
            "observation_mismatch": 0, "orphan_front_receipts": 0,
        }
        pages = list(self.coordinator.db.execute(
            "SELECT account_ordinal,page,job_id FROM page_evidence "
            "ORDER BY account_ordinal,page"
        ))
        known = set()
        for page in pages:
            ordinal, sequence, job_id = (
                page["account_ordinal"], page["page"], page["job_id"]
            )
            receipt_id = f"front.{self.day}.{ordinal:03d}.p{sequence}"
            known.add(receipt_id)
            clock = self.coordinator.db.execute(
                "SELECT observed_at FROM offline_fixture_clock WHERE job_id=?",
                (job_id,)
            ).fetchone()
            if clock is None:
                counters["missing_clock"] += 1
                continue
            try:
                account, posts = self.raw._validated_page(ordinal, sequence)
                normalized = [_normalize_post(post) for post in posts]
                payload = _canonical(normalized)
                digest = hashlib.sha256(payload.encode("utf-8")).hexdigest()
                observed = _observation_time(clock["observed_at"])
            except (OfflineRawProjectionError, OfflineRawMirrorError,
                    TypeError, ValueError):
                counters["unverified_source"] += 1
                continue
            receipt = self.mirror.db.execute(
                "SELECT account_key,observed_at,payload_sha256,payload_json,state "
                "FROM page_receipts WHERE request_id=?", (receipt_id,)
            ).fetchone()
            if receipt is None:
                counters["missing_receipt"] += 1
                continue
            if (receipt["account_key"] != account or
                    receipt["observed_at"] != observed or
                    receipt["payload_sha256"] != digest or
                    receipt["payload_json"] != payload):
                counters["receipt_mismatch"] += 1
                continue
            if receipt["state"] == "staged":
                counters["staged_receipts"] += 1
                continue
            if receipt["state"] != "committed":
                counters["receipt_mismatch"] += 1
                continue
            observations = list(self.mirror.db.execute(
                "SELECT post_id,observed_at,like_count,comments_count,"
                "like_present,comments_present FROM raw_post_observations "
                "WHERE request_id=?", (receipt_id,)
            ))
            expected_ids = {post["id"] for post in normalized}
            got_ids = {row["post_id"] for row in observations}
            verified = len(observations) == len(expected_ids) and got_ids == expected_ids
            if verified:
                by_id = {post["id"]: post for post in normalized}
                for observation in observations:
                    post = by_id[observation["post_id"]]
                    if (observation["observed_at"] != observed or
                            observation["like_count"] != post.get("like_count") or
                            observation["comments_count"] != post.get("comments_count") or
                            observation["like_present"] != int("like_count" in post) or
                            observation["comments_present"] != int("comments_count" in post)):
                        verified = False
                        break
                    stored = self.mirror.db.execute(
                        "SELECT account_key FROM raw_posts WHERE post_id=?",
                        (post["id"],)
                    ).fetchone()
                    if stored is None or stored["account_key"] != account:
                        verified = False
                        break
                    media = list(self.mirror.db.execute(
                        "SELECT media_id,account_key,media_role,item_order "
                        "FROM raw_post_media WHERE post_id=?", (post["id"],)
                    ))
                    expected_media = {
                        (post["id"],account,"parent",0),
                        *((child["media_id"],account,"child",child["item_order"])
                          for child in post["children"]),
                    }
                    if {tuple(m) for m in media} != expected_media or len(media) != len(expected_media):
                        verified = False
                        break
            if verified:
                counters["verified_pages"] += 1
            else:
                counters["observation_mismatch"] += 1
        other_receipts = self.mirror.db.execute(
            "SELECT request_id FROM page_receipts WHERE request_id LIKE ?",
            (f"front.{self.day}.%",)
        )
        counters["orphan_front_receipts"] = sum(
            row["request_id"] not in known for row in other_receipts
        )
        pending = len(pages) - counters["verified_pages"]
        return {
            "mode": "SYNTHETIC_OFFLINE_READ_ONLY_AUDIT",
            "day": self.day, "source_pages": len(pages),
            **counters, "pages_requiring_attention": pending,
            # This certifies existing source pages only, never 294-account
            # collection coverage; an empty source is not a successful audit.
            "storage_verified": bool(pages) and pending == 0 and
                                counters["orphan_front_receipts"] == 0,
            "history_dispatch_enabled": False,
            "unattended_execution_enabled": False,
        }

    def operator_report(self, *, now, include_storage_audit=False):
        """Read-only, bounded operational diagnosis of this synthetic snapshot.

        Never repairs or dispatches. Counts express *stored evidence*, not
        verified live Meta coverage. Detailed receipt digest checks still
        require reconcile(); this method intentionally has zero writes.
        """
        if not isinstance(now, datetime) or now.tzinfo is None:
            raise OfflineDailyScheduleError("Timezone-aware report instant required")
        if type(include_storage_audit) is not bool:
            raise OfflineDailyScheduleError("include_storage_audit must be boolean")
        now = now.astimezone(timezone.utc)
        self.daily._verify_binding()
        utc_time = stamp(now)
        actions = self.ledger.next_actions(
            [account["key"] for account in self.accounts], self.day
        )
        windows = list(self.ledger.db.execute(
            "SELECT account_key,status,front_pages FROM daily_window WHERE day=?",
            (self.day,)
        ))
        if (len(windows) != 294
                or {row["account_key"] for row in windows}
                   != {account["key"] for account in self.accounts}):
            raise OfflineDailyScheduleError("Incomplete daily roster ledger")
        source_pages = list(self.coordinator.db.execute(
            "SELECT account_ordinal,page,job_id FROM page_evidence"
        ))
        first_page = {row["account_ordinal"] for row in source_pages
                      if row["page"] == 1}
        missing_page1_keys = [
            account["key"] for account in self.accounts
            if account["ordinal"] not in first_page
        ]
        # Ledger closure can occur without a source page proof; it is not
        # evidence that a post was captured or promoted.
        ordinal_by_key = {
            account["key"]: account["ordinal"] for account in self.accounts
        }
        closed_without_page1 = sum(
            row["status"] != "front_open"
            and ordinal_by_key[row["account_key"]] not in first_page
            for row in windows
        )
        clocks = {row["job_id"] for row in self.coordinator.db.execute(
            "SELECT job_id FROM offline_fixture_clock"
        )}
        receipts = {
            row["request_id"]: row["state"]
            for row in self.mirror.db.execute(
                "SELECT request_id,state FROM page_receipts"
            )
        }
        source_missing_clock = sum(
            row["job_id"] not in clocks for row in source_pages
        )
        source_missing_mirror = sum(
            f"front.{self.day}.{row['account_ordinal']:03d}.p{row['page']}"
            not in receipts for row in source_pages
        )
        staged_front = sum(
            receipts.get(f"front.{self.day}.{row['account_ordinal']:03d}.p{row['page']}")
            == "staged" for row in source_pages
        )
        statuses = {}
        for row in windows:
            statuses[row["status"]] = statuses.get(row["status"], 0) + 1
        missing_limit = 20
        lane_rows = []
        for row in self.coordinator.db.execute(
            "SELECT lane,halted,reason,ready_at FROM lanes ORDER BY lane"
        ):
            try:
                ready = datetime.fromisoformat(row["ready_at"])
                if ready.tzinfo is None:
                    raise ValueError
                ready = ready.astimezone(timezone.utc)
                ready_at = stamp(ready)
            except (ValueError, TypeError, OverflowError):
                ready = None
                ready_at = None
            halted = row["halted"] == 1
            leased = self.coordinator.db.execute(
                "SELECT leased_until FROM jobs WHERE lane=? AND state='leased'",
                (row["lane"],)
            ).fetchall()
            expired = sum(
                isinstance(job["leased_until"], str)
                and job["leased_until"] <= utc_time for job in leased
            )
            lane_rows.append({
                "lane": row["lane"],
                "halted": halted,
                "reason": (row["reason"] if row["reason"] in
                           ("missing_usage", "usage_limit") else
                           "unknown" if halted else None),
                "ready_at_utc": ready_at if halted else None,
                "requires_manual_reset": halted,
                "reset_time_reached": halted and ready is not None and now >= ready,
                "inflight_leases": len(leased),
                "expired_leases": expired,
            })
        phases = {action["phase"] for action in actions}
        # These counts are derived from the same roster-scoped action list,
        # never from global DB rows or unverified RAW completeness.
        history_eligible_count=sum(
            action["phase"].startswith("history") for action in actions
        )
        manual_review_count=sum(
            action["phase"] in ("reanchor","needs_review")
            for action in actions
        )
        if "front" in phases:
            next_phase = "front"
        elif "catchup" in phases:
            next_phase = "catchup"
        elif "reanchor" in phases or "needs_review" in phases:
            next_phase = "manual_review"
        elif any(phase.startswith("history") for phase in phases):
            next_phase = "history_held"
        else:
            next_phase = "none"
        pending = self.mirror.db.execute(
            "SELECT COUNT(*) FROM page_receipts WHERE state='staged'"
        ).fetchone()[0]
        recovery_attention = bool(
            source_missing_clock or source_missing_mirror or staged_front or pending
        )
        if closed_without_page1:
            completion_warning = (
                "수집 윈도우가 종료됐지만 첫 페이지 확정 증거가 없는 계정이 "
                "있습니다. 수집 완료로 표시하지 말고 증거를 점검하세요."
            )
        elif missing_page1_keys:
            completion_warning = (
                "첫 페이지 확정 증거가 없는 계정이 있습니다. "
                "실제 수집 완료가 아닙니다."
            )
        elif statuses.get("front_open", 0):
            completion_warning = (
                "첫 페이지 증거가 있어도 열린 수집 윈도우가 있습니다. "
                "일일 수집 완료가 아닙니다."
            )
        else:
            completion_warning = (
                "합성 수집 윈도우와 첫 페이지 증거가 확인됐습니다. "
                "실제 Meta 수집과 전체 무결성은 미검증입니다."
            )
        result = {
            "mode": "SYNTHETIC_OFFLINE_MANUAL_ONLY",
            "read_only": True,
            "integrity": "not_fully_revalidated",
            "live_meta_verified": False,
            "day": self.day,
            "evaluated_at_utc": utc_time,
            "daily_front": {
                "roster_accounts": 294,
                "first_page_committed": len(first_page),
                "first_page_missing": len(missing_page1_keys),
                "first_page_missing_sample": missing_page1_keys[:missing_limit],
                "first_page_missing_sample_truncated":
                    len(missing_page1_keys) > missing_limit,
                "front_window_open": statuses.get("front_open", 0),
                "front_window_closed": 294 - statuses.get("front_open", 0),
                "first_page_proof_gap_in_closed_windows": closed_without_page1,
                "completion_warning": completion_warning,
                "source_front_page_proofs": len(source_pages),
            },
            "gaps": {
                "catchup_pending_windows": self.ledger.db.execute(
                    "SELECT COUNT(*) FROM daily_window "
                    "WHERE day<=? AND status='catchup_pending'",
                    (self.day,)
                ).fetchone()[0],
                "manual_review_windows": self.ledger.db.execute(
                    "SELECT COUNT(*) FROM daily_window "
                    "WHERE day<=? AND status='needs_review'",
                    (self.day,)
                ).fetchone()[0],
            },
            "lanes": lane_rows,
            "storage": {
                "source_pages_without_capture_clock": source_missing_clock,
                "source_pages_without_raw_receipt": source_missing_mirror,
                "source_pages_with_staged_raw_receipt": staged_front,
                "raw_staged_receipts": pending,
                "raw_posts": self.mirror.db.execute(
                    "SELECT COUNT(*) FROM raw_posts"
                ).fetchone()[0],
                "raw_observations": self.mirror.db.execute(
                    "SELECT COUNT(*) FROM raw_post_observations"
                ).fetchone()[0],
                "raw_media": self.mirror.db.execute(
                    "SELECT COUNT(*) FROM raw_post_media"
                ).fetchone()[0],
                "recovery_attention": recovery_attention,
            },
            "policy": {
                "next_phase_if_storage_verified": next_phase,
                "must_reconcile_before_dispatch": recovery_attention,
                "manual_review_accounts": manual_review_count,
                "history_eligible_accounts": history_eligible_count,
                "history_dispatch_enabled": False,
                "unattended_execution_enabled": False,
            },
        }
        if include_storage_audit:
            # Explicit operator opt-in: expensive full source/ledger/RAW
            # evidence audit never runs on ordinary reports or scheduler ticks.
            result["storage_integrity"] = self.audit_storage()
        return result

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

    def step_serial(self, *, now, lane_order=("A", "B", "C")):
        """Run at most one synthetic page, trying eligible lanes sequentially.

        Preserve the frozen per-job A/B/C assignments. A halted lane can be
        skipped without spawning parallel requests or resetting its quota.
        A missing fixture or recovery failure is NOT a license to try another
        lane: the failure remains visible for operator review.
        """
        if (not isinstance(lane_order, (tuple, list))
                or len(lane_order) != len(LANES)
                or set(lane_order) != set(LANES)):
            raise OfflineDailyScheduleError("Exactly three unique lanes required")
        if not isinstance(now, datetime) or now.tzinfo is None:
            raise OfflineDailyScheduleError("Timezone-aware time required")
        for lane in lane_order:
            result = self.step(lane, now=now)
            if result["status"] == "front_lane_unavailable":
                continue
            return {"selected_lane": lane, **result}
        return {"status": "all_lanes_unavailable", "selected_lane": None}

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
            recovered_coverage=self.reconcile()
        except Exception:
            return {"status":"recovery_blocked","lane":lane}
        # Reuse the phase counts already computed by reconcile(). This avoids
        # an additional full-roster ledger scan on each synthetic tick.
        phases=recovered_coverage["action_counts"]
        if phases.get("front",0):
            if stage_only:
                return {"status":"front_stage_only_unsupported"}
            return self._front_step(lane,now)
        # Eligibility is a read-only decision, not a history dispatch. Review
        # exceptions are account-scoped: preserve their count while making
        # unrelated eligible history accounts visible to the operator.
        history_count=sum(count for phase,count in phases.items()
                          if phase.startswith("history"))
        review_count=sum(phases.get(phase,0) for phase in
                         ("reanchor","needs_review"))
        if phases.get("catchup",0):
            out=self.catchup.run_one(lane,self.day,now=now,stage_only=stage_only)
            return {"status":out["status"],"phase":"catchup",
                    "gap_status":out.get("gap_status")}
        if review_count:
            return {"status":"manual_gap_review_required",
                    "review_accounts":review_count,
                    "history_eligible_accounts":history_count,
                    "history_dispatch_enabled":False}
        # History is only an eligibility decision. This scheduler never
        # quietly sends history requests, even if spare API budget exists.
        if history_count:
            return {"status":"history_held_for_review",
                    "eligible_accounts":history_count}
        return {"status":"no_eligible_work"}
