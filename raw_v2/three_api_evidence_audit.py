"""Read-only audit of synthetic three-API coordinator page evidence.

This diagnostic does not open a database, send HTTP, authorize Meta, or alter
coordinator state. The caller supplies an already-open SQLite connection.
"""
from __future__ import annotations

import re

_RECEIPT = re.compile(r"[A-Za-z0-9_.:-]{1,128}\Z")


def audit_page_evidence(connection):
    """Return redacted integrity findings, never post IDs or opaque cursors.

    This is a consistency check, not proof that a RAW receipt exists in an
    external database. Live approval must NEVER depend on this alone.
    """
    rows = connection.execute(
        "SELECT e.account_ordinal,e.page,e.job_id,e.receipt,"
        "e.requested_cursor,e.response_cursor,e.terminal,"
        "j.id AS linked_job,j.account_ordinal AS job_ordinal,"
        "j.page AS job_page,j.state AS job_state "
        "FROM page_evidence e LEFT JOIN jobs j ON j.id=e.job_id "
        "ORDER BY e.account_ordinal,e.page"
    ).fetchall()
    findings = []
    seen_receipts = set()
    by_account = {}
    for row in rows:
        account = row["account_ordinal"]
        page = row["page"]
        receipt = row["receipt"]
        if (row["linked_job"] is None or row["job_ordinal"] != account
                or row["job_page"] != page or row["job_state"] != "done"):
            findings.append("job_evidence_mismatch")
        if not isinstance(receipt, str) or not _RECEIPT.fullmatch(receipt):
            findings.append("invalid_receipt")
        elif receipt in seen_receipts:
            findings.append("duplicate_receipt")
        seen_receipts.add(receipt)
        if page not in (1, 2, 3) or type(account) is not int or not 1 <= account <= 294:
            findings.append("invalid_page_identity")
            continue
        if row["terminal"] not in (0, 1):
            findings.append("invalid_terminal")
        if page == 1 and row["requested_cursor"] is not None:
            findings.append("head_has_cursor")
        previous = by_account.get((account, page - 1))
        if page > 1:
            if previous is None:
                findings.append("missing_predecessor")
            elif (previous["terminal"] or not previous["response_cursor"]
                  or row["requested_cursor"] != previous["response_cursor"]):
                findings.append("cursor_chain_mismatch")
        by_account[(account, page)] = row
    return {
        "mode": "OFFLINE_READ_ONLY",
        "pages_checked": len(rows),
        "valid": not findings,
        "findings": sorted(set(findings)),
        "finding_count": len(findings),
        "external_raw_receipts_verified": False,
        "runtime_authorized": False,
    }
