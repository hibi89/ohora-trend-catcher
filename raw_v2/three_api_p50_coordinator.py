"""Isolated 50-account coordinator seed plan; no Meta calls or DB changes on import.

Balances each account's first page across A/B/C; preserves all quota gates.
Use only with a dedicated P50 coordinator database, never the P6/294 store.
"""
from __future__ import annotations

from pathlib import Path
from datetime import timedelta
import uuid

from three_api_coordinator import LANES, LEASE_MINUTES, stamp, utcnow

from three_api_coordinator import Coordinator


class P50Coordinator(Coordinator):
    def __init__(self, path):
        target = Path(path)
        if "p50" not in {part.lower() for part in target.parts}:
            raise ValueError("P50 coordinator requires a dedicated p50 directory")
        super().__init__(target)

    def seed(self):
        """Seed 50 accounts × 3 sequential pages, balanced across three APIs.

        Account 1 uses A/C/B, account 2 B/A/C, account 3 C/B/A.
        Each account's later pages depend on predecessor RAW evidence.
        Existing stores with another assignment fail closed, not overwritten.
        """
        patterns = (("A", "C", "B"), ("B", "A", "C"), ("C", "B", "A"))
        expected = [
            (account, page, patterns[(account - 1) % 3][page - 1],
             "front" if page == 1 else "catchup")
            for account in range(1, 51)
            for page in range(1, 4)
        ]
        self.db.execute("BEGIN IMMEDIATE")
        try:
            for account, page, lane, phase in expected:
                self.db.execute(
                    """INSERT OR IGNORE INTO jobs
                       (account_ordinal,page,lane,phase) VALUES (?,?,?,?)""",
                    (account, page, lane, phase),
                )
            actual = [
                tuple(row) for row in self.db.execute(
                    "SELECT account_ordinal,page,lane,phase FROM jobs "
                    "ORDER BY account_ordinal,page"
                ).fetchall()
            ]
            if [(a,p,phase) for a,p,lane,phase in actual] != [
                (a,p,phase) for a,p,lane,phase in expected]:
                raise ValueError("P50 job identity mismatch; preserve original store")
            # Existing completed and durably bound jobs retain their original lane.
            # Only unbound pending work may be reassigned by the P50 dispatcher.
            self.db.execute("COMMIT")
        except BaseException:
            self.db.execute("ROLLBACK")
            raise

    def lease_dynamic_p50(self, now=None):
        """A-first, then B, then C; atomically reassign only unbound eligible jobs.

        No HTTP occurs here. Bound requests and completed pages never change lanes.
        """
        now = now or utcnow()
        current = stamp(now)
        self.db.execute("BEGIN IMMEDIATE")
        try:
            for lane in LANES:
                gate = self.db.execute(
                    "SELECT halted,ready_at FROM lanes WHERE lane=?", (lane,)
                ).fetchone()
                if gate["halted"] or gate["ready_at"] > current:
                    continue
                job = self.db.execute("""
                    SELECT j.* FROM jobs j
                    WHERE (j.state='pending' OR
                           (j.state='leased' AND j.leased_until<=?))
                      AND NOT EXISTS (
                          SELECT 1 FROM job_request_bindings b WHERE b.job_id=j.id)
                      AND NOT EXISTS (
                          SELECT 1 FROM jobs earlier
                          WHERE earlier.account_ordinal=j.account_ordinal
                            AND earlier.page<j.page AND earlier.state!='done')
                      AND (j.page=1 OR EXISTS (
                          SELECT 1 FROM page_evidence e
                          WHERE e.account_ordinal=j.account_ordinal
                            AND e.page=j.page-1 AND e.terminal=0
                            AND e.response_cursor IS NOT NULL))
                    ORDER BY j.page,j.account_ordinal LIMIT 1
                """, (current,)).fetchone()
                if job is None:
                    continue
                token = uuid.uuid4().hex
                self.db.execute(
                    "UPDATE jobs SET lane=?,state='leased',lease_id=?,leased_until=? "
                    "WHERE id=?",
                    (lane, token, stamp(now+timedelta(minutes=LEASE_MINUTES)), job["id"]))
                self.db.execute("COMMIT")
                return dict(job) | {"lane": lane, "lease_id": token,
                                    "leased_until": stamp(now+timedelta(minutes=LEASE_MINUTES))}
            self.db.execute("COMMIT")
            return None
        except BaseException:
            self.db.execute("ROLLBACK")
            raise
