"""Transactional cursor evidence for the offline three-API coordinator.

Only a *separate, verified* staging writer may supply the opaque receipt after
its collected page has been durably saved. This module does not independently
verify the external writer, and MUST NOT be used for live calls as-is.

Once the evidence table exists, Coordinator.lease enforces valid preceding
edges and Coordinator.complete is disabled. An evidence row and its job's
'done' transition commit atomically. No Meta calls or production DB writes.
"""
from __future__ import annotations

import re

from three_api_coordinator import stamp, utcnow
from three_api_paging import validate_page_edge

# Opaque non-secret reference, never a token, URL, raw response, or credential.
RECEIPT_RE = re.compile(r"[A-Za-z0-9_.:-]{1,128}\\Z")


class PageEvidenceStore:
    def __init__(self, coordinator):
        self.coordinator = coordinator
        self.db = coordinator.db
        self.db.execute("""CREATE TABLE IF NOT EXISTS page_evidence (
            account_ordinal INTEGER NOT NULL,
            page INTEGER NOT NULL CHECK(page BETWEEN 1 AND 3),
            job_id INTEGER NOT NULL UNIQUE,
            receipt TEXT NOT NULL,
            requested_cursor TEXT,
            response_cursor TEXT,
            terminal INTEGER NOT NULL CHECK(terminal IN (0,1)),
            PRIMARY KEY(account_ordinal,page)
        )""")
        self.db.execute("""CREATE UNIQUE INDEX IF NOT EXISTS
            page_evidence_receipt_unique ON page_evidence(receipt)""")

    def required_cursor(self, account_ordinal, page):
        if page == 1:
            return None
        if page not in (2, 3):
            raise ValueError("Invalid page")
        previous = self.db.execute(
            """SELECT e.response_cursor,e.terminal FROM page_evidence e
               JOIN jobs j ON j.id=e.job_id
               WHERE e.account_ordinal=? AND e.page=? AND j.state='done'""",
            (account_ordinal, page - 1),
        ).fetchone()
        if previous is None or previous["terminal"] or not previous["response_cursor"]:
            raise ValueError("Missing verified predecessor cursor")
        return previous["response_cursor"]

    def commit_page(self, job_id, lease_id, receipt, response,
                    requested_cursor=None, now=None):
        """Commit a validated cursor edge and the lease completion together.

        The caller must FIRST commit collected posts in a staging DB and
        verify that receipt. We do not attest cross-database atomicity here.
        """
        if not isinstance(receipt, str) or not RECEIPT_RE.fullmatch(receipt):
            raise ValueError("Invalid non-secret durable staging receipt")
        now = now or utcnow()
        self.db.execute("BEGIN IMMEDIATE")
        try:
            job = self.db.execute(
                """SELECT * FROM jobs
                   WHERE id=? AND lease_id=? AND state='leased'
                     AND leased_until>?""",
                (job_id, lease_id, stamp(now)),
            ).fetchone()
            if job is None:
                raise ValueError("Missing, stale, or expired lease")
            if job["page"] == 1:
                if requested_cursor is not None:
                    raise ValueError("Head page must not use a cursor")
            elif requested_cursor != self.required_cursor(
                job["account_ordinal"], job["page"]
            ):
                raise ValueError("Cursor does not match predecessor evidence")
            prior = [
                row["response_cursor"]
                for row in self.db.execute(
                    """SELECT response_cursor FROM page_evidence
                       WHERE account_ordinal=? AND page<?
                       ORDER BY page""",
                    (job["account_ordinal"], job["page"] - 1),
                )
                if row["response_cursor"]
            ]
            edge = validate_page_edge(
                response, requested_cursor=requested_cursor, prior_cursors=prior,
            )
            if edge["next_cursor"] and edge["records"] == 0:
                raise ValueError("Empty nonterminal page cannot advance cursor")
            self.db.execute(
                """INSERT INTO page_evidence
                   (account_ordinal,page,job_id,receipt,requested_cursor,
                    response_cursor,terminal) VALUES (?,?,?,?,?,?,?)""",
                (job["account_ordinal"], job["page"], job_id, receipt,
                 requested_cursor, edge["next_cursor"], int(edge["terminal"])),
            )
            changed = self.db.execute(
                """UPDATE jobs SET state='done',lease_id=NULL,leased_until=NULL
                   WHERE id=? AND lease_id=? AND state='leased'""",
                (job_id, lease_id),
            )
            if changed.rowcount != 1:
                raise ValueError("Lease changed during commit")
            self.db.execute("COMMIT")
            return edge
        except BaseException:
            self.db.execute("ROLLBACK")
            raise
