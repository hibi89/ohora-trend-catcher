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


def audit_raw_receipt_links(coordinator_db, raw_port, capture_port):
    """Cross-check committed coordinator receipts against immutable RAW records.

    Read-only. Both ports must be supplied by the caller; this function never
    opens files or creates a network connection. A receipt string by itself is
    insufficient: every row needs an original capture and a persisted RAW
    receipt that validates against the capture's immutable digest.
    """
    from three_api_runtime_contract import validate_raw_receipt

    if not callable(getattr(raw_port, "get_raw_receipt", None)):
        raise ValueError("Read-only RAW receipt port required")
    if not callable(getattr(capture_port, "get_capture_for_request", None)):
        raise ValueError("Read-only capture port required")
    base = audit_page_evidence(coordinator_db)
    # A missing binding table is a migration/preflight failure, not a crash.
    table = coordinator_db.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' "
        "AND name='job_request_bindings'"
    ).fetchone()
    if table is None:
        return {
            "mode": "OFFLINE_READ_ONLY",
            "pages_checked": base["pages_checked"],
            "verified_raw_receipts": 0,
            "valid": False,
            "findings": sorted(set(base["findings"]) | {"missing_binding_table"}),
            "external_raw_receipts_verified": False,
            "runtime_authorized": False,
        }
    rows = coordinator_db.execute(
        "SELECT e.receipt,b.request_id FROM page_evidence e "
        "LEFT JOIN job_request_bindings b ON b.job_id=e.job_id"
    ).fetchall()
    findings = set(base["findings"])
    verified = 0
    for row in rows:
        if row["request_id"] is None:
            findings.add("missing_request_binding")
            continue
        try:
            record = capture_port.get_capture_for_request(row["request_id"])
            if record is None or record.request.request_id != row["request_id"]:
                findings.add("missing_durable_capture")
                continue
            persisted = raw_port.get_raw_receipt(record.capture_id)
            if persisted is None:
                findings.add("missing_raw_receipt")
                continue
            validate_raw_receipt(record, persisted)
            if (persisted.raw_receipt_id != row["receipt"]
                    or persisted.status not in ("committed", "already_committed")):
                findings.add("raw_receipt_mismatch")
                continue
            verified += 1
        except (AttributeError, TypeError, ValueError, KeyError):
            findings.add("invalid_raw_receipt")
    return {
        "mode": "OFFLINE_READ_ONLY",
        "pages_checked": len(rows),
        "verified_raw_receipts": verified,
        "valid": not findings and verified == len(rows),
        "findings": sorted(findings),
        "external_raw_receipts_verified": not findings and verified == len(rows) and bool(rows),
        "runtime_authorized": False,
    }
