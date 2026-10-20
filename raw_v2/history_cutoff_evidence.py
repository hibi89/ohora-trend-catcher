"""Pure offline history coverage evidence validation; no DB writes or API calls.

A cutoff crossing is not a completion certificate without a complete
rooted chain of immutable receipts and verified publication timestamps.
"""
from datetime import datetime, timezone


def _utc(value):
    if not isinstance(value, datetime) or value.utcoffset() is None:
        raise ValueError("Timezone-aware datetime required")
    return value.astimezone(timezone.utc)


def assess_cutoff_evidence(*, anchor, cutoff, pages, rooted):
    """Return fail-closed assessment of synthetic, ordered page evidence.

    Each page requires cursor_in/out, receipt, and a list of published_at
    datetimes. A null cursor_out represents verified terminal only when
    terminal_verified=True. A crossing requires a timestamp <= cutoff.
    """
    anchor, cutoff = _utc(anchor), _utc(cutoff)
    if type(rooted) is not bool:
        raise ValueError("Explicit rooted evidence required")
    if cutoff >= anchor or not isinstance(pages, (tuple, list)):
        raise ValueError("Invalid history window or pages")
    if not rooted or not pages:
        return {"verified": False, "reason": "missing_root"}
    prior = None
    seen_cursors = set()
    seen_receipts = set()
    seen_media = set()
    oldest = None
    for index, page in enumerate(pages):
        if not isinstance(page, dict) or not page.get("receipt") or not isinstance(page.get("receipt"), str):
            return {"verified": False, "reason": "missing_receipt"}
        if page.get("receipt_verified") is not True:
            return {"verified": False, "reason": "receipt_unverified"}
        if page.get("timestamps_verified") is not True:
            return {"verified": False, "reason": "timestamps_unverified"}
        if page["receipt"] in seen_receipts:
            return {"verified": False, "reason": "duplicate_receipt"}
        seen_receipts.add(page["receipt"])
        incoming = page.get("cursor_in")
        if (index == 0 and incoming != "root") or (index and incoming != prior):
            return {"verified": False, "reason": "cursor_gap"}
        outgoing = page.get("cursor_out")
        if outgoing is not None and (not isinstance(outgoing, str) or not outgoing):
            return {"verified": False, "reason": "invalid_cursor"}
        if index < len(pages) - 1 and not outgoing:
            return {"verified": False, "reason": "cursor_gap"}
        media_ids = page.get("media_ids")
        # Caller may supply publication evidence without an ID inventory;
        # missing IDs are not an independently verified complete page.
        if media_ids is not None:
            if not isinstance(media_ids, (list, tuple)) or any(
                not isinstance(mid, str) or not mid for mid in media_ids
            ) or len(set(media_ids)) != len(media_ids):
                return {"verified": False, "reason": "invalid_media_ids"}
            if seen_media.intersection(media_ids):
                return {"verified": False, "reason": "duplicate_media"}
            seen_media.update(media_ids)
        stamps = page.get("published_at")
        if len(media_ids) != len(stamps or []):
            return {"verified": False, "reason": "media_timestamp_mismatch"}
        if not isinstance(stamps, (list, tuple)) or not stamps:
            return {"verified": False, "reason": "missing_timestamps"}
        try:
            normalized = [_utc(t) for t in stamps]
        except ValueError:
            return {"verified": False, "reason": "invalid_timestamp"}
        if any(t > anchor for t in normalized):
            return {"verified": False, "reason": "future_publication"}
        oldest = min([oldest, *normalized]) if oldest is not None else min(normalized)
        if outgoing is not None and (outgoing == incoming or outgoing in seen_cursors):
            return {"verified": False, "reason": "cursor_cycle"}
        if incoming is not None:
            seen_cursors.add(incoming)
        if index and incoming == "root":
            return {"verified": False, "reason": "cursor_cycle"}
        prior = outgoing
    crossing = oldest <= cutoff
    terminal = prior is None and pages[-1].get("terminal_verified") is True
    return {"verified": bool(crossing or terminal), "reason": (
        "cutoff_crossed" if crossing else "verified_terminal" if terminal else "incomplete"
    )}
