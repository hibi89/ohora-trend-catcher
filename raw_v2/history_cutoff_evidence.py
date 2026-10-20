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
    if cutoff >= anchor or not isinstance(pages, (tuple, list)):
        raise ValueError("Invalid history window or pages")
    if not rooted or not pages:
        return {"verified": False, "reason": "missing_root"}
    prior = None
    seen_cursors = set()
    oldest = None
    for index, page in enumerate(pages):
        if not isinstance(page, dict) or not page.get("receipt") or not isinstance(page.get("receipt"), str):
            return {"verified": False, "reason": "missing_receipt"}
        if page.get("receipt_verified") is not True:
            return {"verified": False, "reason": "receipt_unverified"}
        if page.get("timestamps_verified") is not True:
            return {"verified": False, "reason": "timestamps_unverified"}
        incoming = page.get("cursor_in")
        if (index == 0 and incoming != "root") or (index and incoming != prior):
            return {"verified": False, "reason": "cursor_gap"}
        outgoing = page.get("cursor_out")
        if outgoing is not None and (not isinstance(outgoing, str) or not outgoing):
            return {"verified": False, "reason": "invalid_cursor"}
        if index < len(pages) - 1 and not outgoing:
            return {"verified": False, "reason": "cursor_gap"}
        stamps = page.get("published_at")
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
        prior = outgoing
    crossing = oldest <= cutoff
    terminal = prior is None and pages[-1].get("terminal_verified") is True
    return {"verified": bool(crossing or terminal), "reason": (
        "cutoff_crossed" if crossing else "verified_terminal" if terminal else "incomplete"
    )}
