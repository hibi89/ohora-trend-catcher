"""Serial offline P50 dispatcher: durable cursor -> request -> fixture -> RAW.

This deliberately has no transport, scheduling, credentials, or live approval.
Fixture responses are supplied explicitly; the runner never synthesizes Meta
responses. Every page is committed through the existing Capture/RAW ingress.
"""
from __future__ import annotations

from three_api_p50_history_batch import ingest_offline_history_batch
from three_api_p50_history_plan import plan_next_history_page
from three_api_p50_history_request import prepare_history_request


def run_offline_history_fixture_dispatch(
        store, journal, raw_writer, account_keys, usernames, *,
        latest_complete_keys, fixture_pages, max_pages=50, lane="A"):
    """Dispatch up to 50 durable, independent page commits in serial order.

    Index fixtures by (account_key, expected_persisted_cursor). Restarting with
    the same fixture index skips pages whose history cursor already advanced.
    A missing fixture defers its account, not every other runnable account.
    """
    if type(max_pages) is not int or not 1 <= max_pages <= 50:
        raise ValueError("History dispatch page budget must be 1..50")
    if isinstance(account_keys, (dict, set, frozenset)):
        raise ValueError("Ordered P50 roster required")
    keys = tuple(account_keys)
    if len(keys) != 50 or len(set(keys)) != 50:
        raise ValueError("Exactly 50 P50 accounts required")
    if lane not in ("A", "B", "C"):
        raise ValueError("Unknown P50 API lane")
    if not isinstance(usernames, dict):
        raise ValueError("Verified account usernames mapping required")
    complete = frozenset(latest_complete_keys)
    if not complete.issubset(set(keys)) or any(
            not isinstance(usernames.get(k), str) or not usernames[k].strip()
            for k in complete):
        raise ValueError("Invalid latest-complete accounts or usernames")
    if not isinstance(fixture_pages, dict):
        raise ValueError("Explicit offline fixture index required")
    for entry, page in fixture_pages.items():
        if (not isinstance(entry, tuple) or len(entry) != 2
                or entry[0] not in keys or not isinstance(entry[1], str)
                or not entry[1] or not isinstance(page, dict)
                or set(page) != {"request", "response", "oldest_post_at", "terminal"}):
            raise ValueError("Invalid indexed history fixture")
        request = page["request"]
        if (getattr(request, "account_key", None) != entry[0]
                or getattr(request, "cursor", None) != entry[1]
                or getattr(request, "lane", None) != lane):
            raise ValueError("History fixture index differs from request")
    available = set(complete)
    completed = []
    deferred = []
    while len(completed) < max_pages:
        job = prepare_history_request(
            store, keys, usernames, latest_complete_keys=available, lane=lane)
        if job is None:
            break
        key = job["account_key"]
        page = fixture_pages.get((key, job["cursor"]))
        if page is None:
            available.remove(key)
            deferred.append(key)
            continue
        request = page["request"]
        if (request.request_spec != job["spec"]
                or request.username != usernames[key]
                or request.phase != "history"
                or request.history_offline_only is not True):
            raise ValueError("History fixture differs from durable dispatch plan")
        result = ingest_offline_history_batch(
            store, journal, raw_writer, [page], max_pages=1)
        if result["pages_committed"] != 1:
            raise RuntimeError("History fixture page was not committed")
        row = store.read(key)
        if (row is None or row["pages_verified"] != job["verified_pages"] + 1
                or row["cursor"] == job["cursor"]):
            raise RuntimeError("Durable history cursor did not advance")
        completed.extend(result["completed"])
        if row["state"] != "continuation_ready":
            available.remove(key)
    pending = plan_next_history_page(
        store, keys, latest_complete_keys=available, max_pages=1)["jobs"]
    if len(completed) >= max_pages and pending:
        status = "step_budget_exhausted"
    elif deferred:
        status = "awaiting_fixture"
    elif completed:
        status = "completed"
    else:
        status = "no_eligible_cursor"
    return {
        "mode": "P50_HISTORY_OFFLINE_FIXTURE_DISPATCH",
        "status": status,
        "completed": completed,
        "pages_committed": len(completed),
        "deferred_accounts": deferred,
        "next_job": pending[0] if pending else None,
        "network_requests": 0,
    }
