"""Read-only recovery planning for immutable pages misclassified as terminal.

This module never writes coordinator, Capture, or RAW stores and never sends
requests. Cursor values are consumed in memory and represented only by SHA-256
fingerprints in its returned plan.
"""
from __future__ import annotations

import hashlib
import json

from three_api_paging import validate_page_edge


def _page_from_safe_evidence(payload):
    """Extract a bounded media page from actual Business Discovery evidence.

    Do not follow or persist paging.next URLs. Both the nested live envelope
    and the flat normalized test representation are accepted.
    """
    if not isinstance(payload, dict):
        raise ValueError("Invalid response evidence")
    if "business_discovery" in payload:
        discovery = payload["business_discovery"]
        if not isinstance(discovery, dict) or not isinstance(discovery.get("media"), dict):
            raise ValueError("Missing Business Discovery media evidence")
        page = discovery["media"]
    else:
        page = payload
    if not isinstance(page.get("data"), list) or len(page["data"]) > 4:
        raise ValueError("Invalid four-post page evidence")
    return page


def build_paging_recovery_plan(rows):
    """Inspect evidence rows and return a redacted, non-mutating repair plan.

    Each row requires stable request_id, safe_body_json, stored_terminal,
    stored_response_cursor, capture_present, raw_receipt_present, and
    capture_raw_digest_match. An eligible row remains a cursor *candidate*;
    it does not prove that a further page exists.
    """
    planned = []
    seen_requests = set()
    for row in rows:
        request_id = row.get("request_id")
        if not isinstance(request_id, str) or not request_id or request_id in seen_requests:
            raise ValueError("Missing or duplicate recovery request identifier")
        seen_requests.add(request_id)
        try:
            payload = json.loads(row["safe_body_json"])
            page = _page_from_safe_evidence(payload)
            edge = validate_page_edge(page)
        except (KeyError, TypeError, ValueError, json.JSONDecodeError):
            planned.append({"request_id": request_id, "status": "evidence_invalid"})
            continue
        after = edge["next_cursor"]
        mismatch = bool(row.get("stored_terminal")) and not row.get("stored_response_cursor") and bool(after)
        immutable_chain_ok = all(row.get(key) is True for key in (
            "capture_present", "raw_receipt_present", "capture_raw_digest_match"))
        if not mismatch:
            status = "no_terminal_cursor_mismatch"
        elif not immutable_chain_ok:
            status = "evidence_chain_incomplete"
        else:
            status = "cursor_candidate_review_required"
        planned.append({
            "request_id": request_id,
            "status": status,
            "records": edge["records"],
            "pagination_status": edge["pagination_status"],
            "stored_terminal": bool(row.get("stored_terminal")),
            "stored_response_cursor_present": bool(row.get("stored_response_cursor")),
            "capture_present": row.get("capture_present") is True,
            "raw_receipt_present": row.get("raw_receipt_present") is True,
            "capture_raw_digest_match": row.get("capture_raw_digest_match") is True,
            "cursor_fingerprint": hashlib.sha256(after.encode("utf-8")).hexdigest() if after else None,
            "mutations": [],
            "network_requests": 0,
        })
    return {
        "plan_version": 1,
        "read_only": True,
        "automatic_apply": False,
        "rows": planned,
        "summary": {
            "total": len(planned),
            "cursor_candidate_review_required": sum(
                item["status"] == "cursor_candidate_review_required" for item in planned),
            "evidence_chain_incomplete": sum(
                item["status"] == "evidence_chain_incomplete" for item in planned),
        },
    }


def plan_cursor_recovery(prior_media_ids, recovery_pages, *, max_pages=3):
    """Plan bounded re-anchoring after a separately confirmed cursor failure.

    Each recovery page is a normalized page dict with ``data`` and optional
    ``paging.cursors.after``. Input order is newest to oldest. Repeated IDs are
    retained in ``observation_ids`` (they represent observations) and removed
    only from ``analysis_media_ids``. A continuation cursor is offered only
    after an overlap with previously captured IDs is found on that page.
    This function performs no I/O, writes, cursor fabrication, or retries.
    """
    if max_pages != 3:
        raise ValueError("Recovery is capped at the frozen three-page limit")
    if not isinstance(prior_media_ids, (list, tuple)) or not prior_media_ids:
        raise ValueError("Existing media IDs are required to re-anchor")
    if any(not isinstance(media_id, str) or not media_id for media_id in prior_media_ids):
        raise ValueError("Invalid existing media ID")
    if not isinstance(recovery_pages, (list, tuple)) or len(recovery_pages) > max_pages:
        raise ValueError("Recovery exceeds the three-page limit")
    if not recovery_pages:
        return {"status": "coverage_gap", "pages_examined": 0,
                "observation_ids": [], "analysis_media_ids": [],
                "resume_cursor": None, "network_requests": 0}

    prior = set(prior_media_ids)
    observations = []
    analysis_ids = []
    analysis_seen = set()
    anchor = None
    anchor_page = None
    resume_cursor = None
    terminal = False
    for page_number, page in enumerate(recovery_pages, 1):
        edge = validate_page_edge(page)
        ids = [item.get("id") for item in page["data"]]
        if any(not isinstance(media_id, str) or not media_id for media_id in ids):
            raise ValueError("Invalid recovery media ID")
        observations.extend(ids)
        for media_id in ids:
            if media_id not in analysis_seen:
                analysis_seen.add(media_id)
                analysis_ids.append(media_id)
        overlaps = [media_id for media_id in ids if media_id in prior]
        if overlaps:
            anchor = overlaps[-1]
            anchor_page = page_number
            resume_cursor = edge["next_cursor"]
        terminal = edge["pagination_status"] == "terminal_verified"

    if anchor is None:
        status = "coverage_gap"
    elif resume_cursor and anchor_page == max_pages:
        # Reaching an anchor at page three does not authorize page four in the
        # same recovery round. Expose the candidate for a later approved round.
        status = "page_limit_reached"
    elif resume_cursor:
        status = "resume_ready"
    elif terminal:
        status = "terminal_verified"
    else:
        # An overlap without a usable cursor does not prove the next older edge.
        status = "coverage_gap"
    return {
        "status": status,
        "pages_examined": len(recovery_pages),
        "anchor_media_id": anchor,
        "anchor_page": anchor_page,
        "observation_ids": observations,
        "analysis_media_ids": analysis_ids,
        "resume_cursor": resume_cursor,
        "network_requests": 0,
        "max_pages": max_pages,
        "deleted_media_ids_confirmed": [],
    }


def cursor_failure_disposition(*, error_category, request_id):
    """Classify a confirmed cursor error without retrying or altering evidence.

    The caller must separately distinguish cursor errors from HTTP 400,
    permission, field, and account errors; this helper intentionally does not
    infer cursor invalidity from status code or message text.
    """
    if error_category == "cursor_invalid":
        return {"status": "resume_required", "request_id": request_id,
                "retry_existing_request": False, "mutations": [],
                "recovery_round_requires_approval": True}
    return {"status": "operator_review", "request_id": request_id,
            "retry_existing_request": False, "mutations": [],
            "recovery_round_requires_approval": True}
