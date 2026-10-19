"""Transactional page evidence for the offline three-API coordinator.

The caller must first durably persist the collected page in its own staging
store, then supply a non-secret receipt. This DB is NOT the live collection DB.
No Meta requests occur here. Page-2/3 cursor is derived from the preceding
committed edge, never from logical offsets.
"""
from __future__ import annotations

import sqlite3
from three_api_paging import validate_page_edge


class PageEvidenceStore:
    def __init__(self, coordinator):
        self.coordinator = coordinator
        self.db = coordinator.db
        self.db.execute("""CREATE TABLE IF NOT EXISTS page_evidence (
            account_ordinal INTEGER NOT NULL,
            page INTEGER NOT NULL,
            job_id INTEGER NOT NULL UNIQUE,
            receipt TEXT NOT NULL,
            requested_cursor TEXT,
            response_cursor TEXT,
            terminal INTEGER NOT NULL,
            PRIMARY KEY(account_ordinal,page)
        )""")

    def required_cursor(self, account_ordinal, page):
        if page == 1:
            return None
        if page not in (2, 3):
            raise ValueError("Invalid page")
        previous = self.db.execute(
            """SELECT e.response_cursor,e.terminal FROM page_evidence e
               JOIN jobs j ON j.id=e.job_id
               WHERE e.account_ordinal=? AND e.page=? AND j.state='done'""",
            (account_ordinal, page - 1)).fetchone()
        if previous is None or previous["terminal"] or not previous["response_cursor"]:
            raise ValueError("Missing verified predecessor cursor")
        return previous["response_cursor"]

    def commit_page(self, job_id, lease_id, receipt, response, requested_cursor=None):
        """Atomically acknowledge a staged page and record its verified edge.

        receipt is a durable staging-store identifier, not the page payload.
        Never pass credentials or uncommitted staging receipts.
        """
        if not isinstance(receipt, str) or not receipt or len(receipt) > 256:
            raise ValueError("Invalid durable staging receipt")
        self.db.execute("BEGIN IMMEDIATE")
        try:
            job = self.db.execute(
                "SELECT * FROM jobs WHERE id=? AND lease_id=? AND state='leased'",
                (job_id, lease_id)).fetchone()
            if job is None:
                raise ValueError("Stale or missing lease")
            if job["page"] == 1:
                if requested_cursor is not None:
                    raise ValueError("Head page must not use a cursor")
            elif requested_cursor != self.required_cursor(
                    job["account_ordinal"], job["page"]):
                raise ValueError("Cursor does not match predecessor evidence")
            prior = [r[0] for r in self.db.execute(
                """SELECT requested_cursor FROM page_evidence
                   WHERE account_ordinal=? AND requested_cursor IS NOT NULL""",
                (job["account_ordinal"],))]
            edge = validate_page_edge(
                response, requested_cursor=requested_cursor,
                prior_cursors=[c for c in prior if c != requested_cursor])
            self.db.execute(
                """INSERT INTO page_evidence
                   (account_ordinal,page,job_id,receipt,requested_cursor,response_cursor,terminal)
                   VALUES (?,?,?,?,?,?,?)""",
                (job["account_ordinal"],job["page"],job_id,receipt,
                 requested_cursor,edge["next_cursor"],int(edge["terminal"])))
            changed = self.db.execute(
                """UPDATE jobs SET state='done',lease_id=NULL,leased_until=NULL
                   WHERE id=? AND lease_id=? AND state='leased'""",
                (job_id,lease_id))
            if changed.rowcount != 1:
                raise ValueError("Lease changed during commit")
            self.db.execute("COMMIT")
            return edge
        except BaseException:
            self.db.execute("ROLLBACK")
            raise
