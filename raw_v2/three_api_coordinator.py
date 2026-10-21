"""Offline-safe three-lane coordinator state machine.

This module never calls Meta and never touches the existing collection.sqlite.
It creates its own isolated coordinator DB. Live integration requires a separate
reviewed adapter and explicit user approval.
"""
from __future__ import annotations

import argparse
from datetime import datetime, timedelta, timezone
import json
from pathlib import Path
import sqlite3
import uuid

LANES = ("A", "B", "C")
STOP_USAGE = 85
COOLDOWN_MINUTES = 65
LEASE_MINUTES = 15


def utcnow():
    return datetime.now(timezone.utc)


def stamp(value):
    return value.astimezone(timezone.utc).isoformat()


class Coordinator:
    def __init__(self, path):
        self.path = Path(path)
        if self.path.name == "collection.sqlite":
            raise ValueError("Refusing to use the live collection database")
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.db = sqlite3.connect(self.path, timeout=30, isolation_level=None)
        self.db.row_factory = sqlite3.Row
        self.db.execute("PRAGMA busy_timeout=30000")
        self.db.executescript("""
        CREATE TABLE IF NOT EXISTS lanes (
            lane TEXT PRIMARY KEY CHECK(lane IN ('A','B','C')),
            ready_at TEXT NOT NULL,
            halted INTEGER NOT NULL DEFAULT 0,
            reason TEXT
        );
        CREATE TABLE IF NOT EXISTS jobs (
            id INTEGER PRIMARY KEY,
            account_ordinal INTEGER NOT NULL CHECK(account_ordinal BETWEEN 1 AND 294),
            page INTEGER NOT NULL CHECK(page BETWEEN 1 AND 3),
            lane TEXT NOT NULL CHECK(lane IN ('A','B','C')),
            phase TEXT NOT NULL CHECK(phase IN ('front','catchup')),
            state TEXT NOT NULL DEFAULT 'pending'
                CHECK(state IN ('pending','leased','done')),
            lease_id TEXT,
            leased_until TEXT,
            UNIQUE(account_ordinal,page)
        );
        """)
        for lane in LANES:
            self.db.execute(
                "INSERT OR IGNORE INTO lanes(lane,ready_at) VALUES (?,?)",
                (lane, "1970-01-01T00:00:00+00:00"),
            )

    def seed(self):
        """Seed the 294 x 3 snapshot plan, idempotently."""
        self.db.execute("BEGIN IMMEDIATE")
        try:
            for account in range(1, 295):
                first = account <= 150
                assignments = (
                    ("A", "C", "B") if first else ("B", "A", "C")
                )
                for page, lane in enumerate(assignments, 1):
                    self.db.execute(
                        """INSERT OR IGNORE INTO jobs
                           (account_ordinal,page,lane,phase) VALUES (?,?,?,?)""",
                        (account,page,lane,"front" if page == 1 else "catchup"),
                    )
            self.db.execute("COMMIT")
        except BaseException:
            self.db.execute("ROLLBACK")
            raise

    def lease(self, lane, now=None, *, skip_bound=False, only_bound=False):
        if lane not in LANES:
            raise ValueError("Unknown lane")
        if skip_bound and only_bound:
            raise ValueError("Conflicting binding filters")
        now = now or utcnow()
        current = stamp(now)
        expires = stamp(now + timedelta(minutes=LEASE_MINUTES))
        token = uuid.uuid4().hex
        self.db.execute("BEGIN IMMEDIATE")
        try:
            state = self.db.execute(
                "SELECT * FROM lanes WHERE lane=?", (lane,)
            ).fetchone()
            # Quota gates apply to HTTP dispatch only. Bound recovery never sends HTTP.
            if not only_bound and (state["halted"] or state["ready_at"] > current):
                self.db.execute("COMMIT")
                return None
            # Evidence-aware mode is enabled only after PageEvidenceStore
            # creates its table. Without it, legacy offline coordinator
            # regression behavior remains unchanged.
            evidence_enabled = self.db.execute(
                "SELECT 1 FROM sqlite_master WHERE type='table' "
                "AND name='page_evidence'"
            ).fetchone() is not None
            proof_filter = (
                """AND (jobs.page=1 OR EXISTS (
                    SELECT 1 FROM page_evidence proof
                    WHERE proof.account_ordinal=jobs.account_ordinal
                      AND proof.page=jobs.page-1 AND proof.terminal=0
                      AND proof.response_cursor IS NOT NULL
                ))"""
                if evidence_enabled else ""
            )
            binding_filter = (" AND NOT EXISTS (SELECT 1 FROM job_request_bindings binding "
                              "WHERE binding.job_id=jobs.id)" if skip_bound and self.db.execute(
                                  "SELECT 1 FROM sqlite_master WHERE type='table' "
                                  "AND name='job_request_bindings'").fetchone() else "")
            if only_bound:
                if not self.db.execute(
                    "SELECT 1 FROM sqlite_master WHERE type='table' "
                    "AND name='job_request_bindings'"
                ).fetchone():
                    self.db.execute("COMMIT")
                    return None
                binding_filter = """ AND EXISTS (
                    SELECT 1 FROM job_request_bindings binding
                    WHERE binding.job_id=jobs.id
                )"""
            job = self.db.execute(
                """SELECT * FROM jobs WHERE lane=?
                   AND (state='pending' OR (state='leased' AND leased_until<=?))
                   AND NOT EXISTS (
                       SELECT 1 FROM jobs earlier
                       WHERE earlier.account_ordinal=jobs.account_ordinal
                       AND earlier.page<jobs.page AND earlier.state!='done'
                   )
                   """ + proof_filter + binding_filter + """
                   ORDER BY CASE phase WHEN 'front' THEN 0 ELSE 1 END,
                            page,account_ordinal LIMIT 1""",
                (lane,current),
            ).fetchone()
            if job is None:
                self.db.execute("COMMIT")
                return None
            self.db.execute(
                """UPDATE jobs SET state='leased',lease_id=?,leased_until=?
                   WHERE id=?""", (token,expires,job["id"])
            )
            self.db.execute("COMMIT")
            return dict(job) | {"lease_id":token,"leased_until":expires}
        except BaseException:
            self.db.execute("ROLLBACK")
            raise

    def lease_for_dispatch(self, lane, now=None):
        """Lease only never-bound work; safe candidate for first-time dispatch.

        The caller must still use JobRequestBindingStore.dispatch_once to
        reserve a request BEFORE any transport attempt. This method alone
        never authorizes HTTP.
        """
        return self.lease(lane, now=now, skip_bound=True)

    def lease_for_recovery(self, lane, now=None):
        """Lease bound work only, for capture-only reconciliation.

        Never consumes fresh unbound jobs and never authorizes HTTP.
        """
        return self.lease_bound_for_recovery(lane, now=now)

    def lease_bound_for_recovery(self, lane, now=None):
        """Lease only already-bound work for capture-only recovery.

        This method never authorizes HTTP, and skips unbound work entirely.
        """
        return self.lease(lane, now=now, only_bound=True)

    def complete(self, job_id, lease_id):
        """Bare completion is forbidden if page evidence is enabled."""
        if self.db.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' "
            "AND name='page_evidence'"
        ).fetchone():
            raise RuntimeError(
                "Page evidence enabled; use PageEvidenceStore.commit_page"
            )
        self.db.execute("BEGIN IMMEDIATE")
        try:
            result = self.db.execute(
                """UPDATE jobs SET state='done',lease_id=NULL,leased_until=NULL
                   WHERE id=? AND lease_id=? AND state='leased'""",
                (job_id,lease_id)
            )
            self.db.execute("COMMIT")
            return result.rowcount == 1
        except BaseException:
            self.db.execute("ROLLBACK")
            raise

    def observe_usage(self, lane, usage, now=None):
        """Fail closed on missing usage; persist per-lane cooldown."""
        if lane not in LANES:
            raise ValueError("Unknown lane")
        now = now or utcnow()
        halt = usage is None or usage >= STOP_USAGE
        if usage is not None and not 0 <= usage <= 100:
            raise ValueError("Usage must be 0..100")
        if halt:
            ready = stamp(now + timedelta(minutes=COOLDOWN_MINUTES))
            self.db.execute(
                """UPDATE lanes SET halted=1,reason=?,
                   ready_at=MAX(ready_at,?) WHERE lane=?""",
                ("missing_usage" if usage is None else "usage_limit",ready,lane)
            )
        return halt

    def reset_after_verified_cooldown(self, lane, now=None):
        """Explicit operator action; no automatic quota recovery assumptions."""
        if lane not in LANES:
            raise ValueError("Unknown lane")
        now = now or utcnow()
        result = self.db.execute(
            """UPDATE lanes SET halted=0,reason=NULL
               WHERE lane=? AND ready_at<=?""", (lane,stamp(now))
        )
        return result.rowcount == 1

    def dispatch_diagnostics(self, now=None):
        """Read-only, offline counts for dispatch and recovery planning.

        A bound unfinished job is NEVER eligible for first-time dispatch,
        regardless of lease expiry. The diagnostic does not authorize sends.
        """
        now = now or utcnow()
        current = stamp(now)
        bound_exists = self.db.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' "
            "AND name='job_request_bindings'"
        ).fetchone() is not None
        bound_join = (
            " LEFT JOIN job_request_bindings b ON b.job_id=j.id "
            if bound_exists else ""
        )
        bound_condition = "b.job_id IS NOT NULL" if bound_exists else "0"
        rows = self.db.execute(
            """SELECT j.lane,
               SUM(CASE WHEN j.state='done' THEN 1 ELSE 0 END) AS done,
               SUM(CASE WHEN j.state!='done' AND """ + bound_condition + """
                        THEN 1 ELSE 0 END) AS bound_recovery,
               SUM(CASE WHEN j.state!='done' AND NOT (""" +
            bound_condition + """) THEN 1 ELSE 0 END) AS unbound,
               SUM(CASE WHEN j.state='leased' AND j.leased_until>?
                        THEN 1 ELSE 0 END) AS active_leases,
               SUM(CASE WHEN j.state='leased' AND j.leased_until<=?
                        THEN 1 ELSE 0 END) AS expired_leases
               FROM jobs j""" + bound_join + """
               GROUP BY j.lane ORDER BY j.lane""",
            (current, current),
        ).fetchall()
        return {
            "mode": "OFFLINE_DIAGNOSTIC_NO_DISPATCH",
            "as_of_utc": current,
            "lanes": [
                {"lane": row["lane"],
                 "done": row["done"],
                 "bound_recovery": row["bound_recovery"],
                 "unbound": row["unbound"],
                 "active_leases": row["active_leases"],
                 "expired_leases": row["expired_leases"]}
                for row in rows
            ],
        }

    def dispatch_ready_diagnostics(self, now=None):
        """Read-only exact candidate counts under current lease prerequisites.

        Uses the same per-job state, predecessor, cursor-proof and lane-gate
        predicates as dispatch-only lease selection. These are candidate
        counts, not guarantees that a future HTTP request will succeed.
        """
        now = now or utcnow()
        current = stamp(now)
        tables = {row[0] for row in self.db.execute(
            "SELECT name FROM sqlite_master WHERE type='table'")}
        proof = (
            """ AND (j.page=1 OR EXISTS (
                    SELECT 1 FROM page_evidence proof
                    WHERE proof.account_ordinal=j.account_ordinal
                      AND proof.page=j.page-1 AND proof.terminal=0
                      AND proof.response_cursor IS NOT NULL
                ))""" if "page_evidence" in tables else ""
        )
        bound = (
            """ AND NOT EXISTS (
                    SELECT 1 FROM job_request_bindings b
                    WHERE b.job_id=j.id
                )""" if "job_request_bindings" in tables else ""
        )
        rows = self.db.execute(
            """SELECT lane.lane,
               CASE WHEN lane.halted=0 AND lane.ready_at<=?
                    THEN 1 ELSE 0 END AS lane_ready,
               COUNT(j.id) AS eligible_jobs
               FROM lanes lane
               LEFT JOIN jobs j ON j.lane=lane.lane
                 AND (j.state='pending' OR
                      (j.state='leased' AND j.leased_until<=?))
                 AND NOT EXISTS (
                     SELECT 1 FROM jobs earlier
                     WHERE earlier.account_ordinal=j.account_ordinal
                       AND earlier.page<j.page AND earlier.state!='done'
                 )""" + proof + bound + """
               GROUP BY lane.lane,lane.halted,lane.ready_at
               ORDER BY lane.lane""",
            (current,current),
        ).fetchall()
        return {
            "mode": "OFFLINE_READ_ONLY_NO_DISPATCH",
            "as_of_utc": current,
            "lanes": [
                {"lane": row["lane"],
                 "lane_ready": bool(row["lane_ready"]),
                 "eligible_jobs": row["eligible_jobs"],
                 "dispatchable_now": (
                     row["eligible_jobs"] if row["lane_ready"] else 0)}
                for row in rows
            ],
        }

    def recovery_ready_diagnostics(self, now=None):
        """Read-only counts of bound recovery candidates per lane.

        Mirrors lease_for_recovery eligibility without changing leases.
        Bound work with an active lease, blocked predecessor, or absent
        cursor proof is excluded. Quota-halted lanes still report bound, no-HTTP recovery jobs.
        """
        now = now or utcnow()
        current = stamp(now)
        tables = {row[0] for row in self.db.execute(
            "SELECT name FROM sqlite_master WHERE type='table'")}
        proof = (
            """ AND (j.page=1 OR EXISTS (
                SELECT 1 FROM page_evidence proof
                WHERE proof.account_ordinal=j.account_ordinal
                  AND proof.page=j.page-1 AND proof.terminal=0
                  AND proof.response_cursor IS NOT NULL
            ))""" if "page_evidence" in tables else ""
        )
        bound = (
            """ AND EXISTS (
                SELECT 1 FROM job_request_bindings b WHERE b.job_id=j.id
            )""" if "job_request_bindings" in tables else " AND 0"
        )
        rows = self.db.execute(
            """SELECT lane.lane,
               1 AS lane_ready,
               COUNT(j.id) AS eligible_jobs
               FROM lanes lane
               LEFT JOIN jobs j ON j.lane=lane.lane
                 AND (j.state='pending' OR
                      (j.state='leased' AND j.leased_until<=?))
                 AND NOT EXISTS (
                     SELECT 1 FROM jobs earlier
                     WHERE earlier.account_ordinal=j.account_ordinal
                       AND earlier.page<j.page AND earlier.state!='done'
                 )""" + proof + bound + """
               GROUP BY lane.lane
               ORDER BY lane.lane""",
            (current,),
        ).fetchall()
        return {
            "mode": "OFFLINE_RECOVERY_READ_ONLY_NO_DISPATCH",
            "as_of_utc": current,
            "lanes": [
                {"lane": row["lane"], "lane_ready": bool(row["lane_ready"]),
                 "eligible_jobs": row["eligible_jobs"],
                 "recoverable_now": row["eligible_jobs"] if row["lane_ready"] else 0}
                for row in rows
            ],
        }

    def summary(self):
        return {
            "mode":"OFFLINE_ONLY_NO_META_CALLS",
            "jobs":{row["state"]:row["n"] for row in self.db.execute(
                "SELECT state,COUNT(*) n FROM jobs GROUP BY state")},
            "lanes":[dict(row) for row in self.db.execute(
                "SELECT * FROM lanes ORDER BY lane")],
        }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--state-db",required=True)
    parser.add_argument("--seed",action="store_true")
    args = parser.parse_args()
    coordinator = Coordinator(args.state_db)
    if args.seed:
        coordinator.seed()
    print(json.dumps(coordinator.summary(),indent=2))


if __name__ == "__main__":
    main()
