"""Serial offline P50 dispatcher: durable cursor -> request -> fixture -> RAW.

This deliberately has no transport, scheduling, credentials, or live approval.
Fixture responses are supplied explicitly; the runner never synthesizes Meta
responses. Every page is committed through the existing Capture/RAW ingress.
"""
from __future__ import annotations

from datetime import datetime

from three_api_p50_history_batch import ingest_offline_history_batch
from three_api_p50_history_plan import plan_next_history_page
from three_api_p50_history_request import prepare_history_request
from three_api_request_spec import build_request_spec


def run_offline_history_fixture_dispatch(
        store, journal, raw_writer, account_keys, usernames, *,
        latest_complete_keys, fixture_pages, max_pages=50, lane="A",
        lanes_by_account=None):
    """Dispatch up to 50 durable, independent page commits in serial order.

    Each account can use its own fixed A/B/C API lane.
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
    if lanes_by_account is None:
        lanes_by_account = {key: lane for key in keys}
    if (not isinstance(lanes_by_account, dict)
            or set(lanes_by_account) != set(keys)
            or any(value not in ("A", "B", "C")
                   for value in lanes_by_account.values())):
        raise ValueError("Explicit valid A/B/C lane assignment required")
    if not isinstance(usernames, dict):
        raise ValueError("Verified account usernames mapping required")
    complete = frozenset(latest_complete_keys)
    if any(not isinstance(key, str) for key in complete):
        raise ValueError("Invalid latest-complete account key")
    if not complete.issubset(set(keys)) or any(
            not isinstance(usernames.get(k), str) or not usernames[k].strip()
            for k in complete):
        raise ValueError("Invalid latest-complete accounts or usernames")
    if not isinstance(fixture_pages, dict):
        raise ValueError("Explicit offline fixture index required")
    seen_requests = set()
    for entry, page in fixture_pages.items():
        if (not isinstance(entry, tuple) or len(entry) != 2
                or entry[0] not in keys or not isinstance(entry[1], str)
                or not entry[1] or not isinstance(page, dict)
                or set(page) != {"request", "response", "oldest_post_at", "terminal"}):
            raise ValueError("Invalid indexed history fixture")
        request = page["request"]
        if (getattr(request, "account_key", None) != entry[0]
                or getattr(request, "cursor", None) != entry[1]
                or getattr(request, "lane", None) != lanes_by_account[entry[0]]):
            raise ValueError("History fixture index differs from request")
        # Validate every supplied fixture before any page is durably written;
        # a bad later account must not cause partial success in earlier ones.
        if entry[0] not in usernames or not isinstance(usernames[entry[0]], str):
            raise ValueError("Fixture account has no verified username")
        if (getattr(request, "phase", None) != "history"
                or getattr(request, "history_offline_only", None) is not True
                or getattr(request, "username", None) != usernames[entry[0]]
                or getattr(request, "request_spec", None) != build_request_spec(
                    lanes_by_account[entry[0]], usernames[entry[0]],
                    after=entry[1], limit=4)):
            raise ValueError("History fixture differs from approved request spec")
        response = page["response"]
        request_id = getattr(request, "request_id", None)
        if (not isinstance(request_id, str) or not request_id
                or request_id in seen_requests
                or getattr(response, "request_id", None) != request_id
                or type(getattr(response, "http_status", None)) is not int
                or not 200 <= response.http_status < 300):
            raise ValueError("Invalid or duplicate history fixture response")
        seen_requests.add(request_id)
        if (type(page["terminal"]) is not bool
                or (page["oldest_post_at"] is not None and
                    (not isinstance(page["oldest_post_at"], datetime)
                     or page["oldest_post_at"].utcoffset() is None))
                or (page["oldest_post_at"] is None and not page["terminal"])
                or (page["terminal"] and response.next_cursor is not None)
                or (not page["terminal"] and
                    (not isinstance(response.next_cursor, str)
                     or not response.next_cursor
                     or response.next_cursor == entry[1]))):
            raise ValueError("Invalid offline history fixture boundary")
    available = set(complete)
    completed = []
    deferred = []
    while len(completed) < max_pages:
        plan = plan_next_history_page(
            store, keys, latest_complete_keys=available, max_pages=1)
        if not plan["jobs"]:
            break
        key = plan["jobs"][0]["account_key"]
        job = prepare_history_request(
            store, keys, usernames, latest_complete_keys={key},
            lane=lanes_by_account[key])
        if job is None or job["account_key"] != key:
            raise RuntimeError("History lane dispatch differs from persisted plan")
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
