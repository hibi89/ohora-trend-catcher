"""Prepare one bounded P50 historical request from a verified persisted cursor.

This is an OFFLINE request plan. It cannot authorize Meta transport.
"""
from __future__ import annotations

from three_api_p50_history_plan import plan_next_history_page
from three_api_request_spec import build_request_spec, business_discovery_field


def prepare_history_request(store, account_keys, usernames, *,
                            latest_complete_keys, lane="A"):
    """Return one immutable four-post request spec, never a network call."""
    plan = plan_next_history_page(
        store, account_keys, latest_complete_keys=latest_complete_keys,
        max_pages=1)
    if not plan["jobs"]:
        return None
    job = plan["jobs"][0]
    key = job["account_key"]
    if key not in usernames:
        raise ValueError("Missing verified account username")
    spec = build_request_spec(lane, usernames[key], after=job["cursor"], limit=4)
    return {
        "account_key": key,
        "cursor": job["cursor"],
        "cutoff_day": job["cutoff_day"],
        "verified_pages": job["verified_pages"],
        "lane": lane,
        "spec": spec,
        "field_expression": business_discovery_field(spec),
        "offline_only": True,
        "network_requests": 0,
    }
