"""Manual single-page P6 collection pilot. OFFLINE PREFLIGHT BY DEFAULT.

This command uses the existing official 294-account CSV parser and the
repository's additive RAW v2 schema. It never contacts Meta unless BOTH
--live and --confirm-owner-approval are supplied by the operator. Source
CSV and any existing operating collection.sqlite remain untouched.

By default only ONE page (limit=4) is attempted per invocation. An explicit
--max-pages bound permits serial multi-page dispatch, never hidden retries.
No scheduler or background looping. Three caller credentials must be in process
environment, never in command arguments, files or Git. Do not use this
entrypoint until the repository owner separately authorizes a Meta pilot.
"""
from __future__ import annotations

import argparse
from contextlib import ExitStack, closing
from datetime import datetime, timezone, timedelta
import json
import os
from pathlib import Path
import re
import sqlite3

from collector import connect, load_accounts
from three_api_coordinator import Coordinator
from three_api_capture import CaptureJournal
from three_api_credentials import make_references, validate_identity_value
from three_api_graph_http import GraphHTTPClient, make_strict_sender
from three_api_live_lane_router import ThreeLaneMetaTransport
from three_api_raw_writer import ContractRawWriter
from three_api_roster_preflight import validate_official_roster
from three_api_serial_bootstrap import build_verified_serial_runner
from three_api_live_serial_step import run_bounded_serial_steps
from three_api_transport import MetaTransport

LANES = ("A", "B", "C")
VERSION = re.compile(r"v[0-9]{1,3}\.[0-9]{1,2}\Z")


class PilotConfigurationError(ValueError):
    """Sanitized failure; never includes tokens, file contents or account IDs."""


def _validated_roster(source):
    # The original collector enforces 294 rows in the official KR/US/JP
    # ordering and returns the immutable original CSV fingerprint.
    accounts, digest = load_accounts(source)
    validate_official_roster(accounts)
    return accounts, digest


def _credentials(environment):
    mapping = {
        lane: {
            "token_env_name": f"P6_META_TOKEN_{lane}",
            "ig_user_id_env_name": f"P6_META_IG_ID_{lane}",
        }
        for lane in LANES
    }
    refs = make_references(mapping)
    values = {}
    for lane in LANES:
        ref = refs[lane]
        token = environment.get(ref.token_env_name)
        caller = environment.get(ref.ig_user_id_env_name)
        if (not isinstance(token, str) or not 1 <= len(token) <= 4096
                or any(ord(c) < 33 or ord(c) > 126 for c in token)
                or not validate_identity_value(caller)):
            raise PilotConfigurationError("Three lane credentials are not configured")
        values[lane] = (ref, caller, token)
    if (len({values[lane][1] for lane in LANES}) != 3
            or len({values[lane][2] for lane in LANES}) != 3):
        raise PilotConfigurationError("Three independent caller configurations required")
    return values


def _transports(values):
    # The strict HTTPS opener denies redirects and implicit proxy routing.
    # It is CONSTRUCTED only within the separately approved --live path.
    client = GraphHTTPClient(sender=make_strict_sender(), approved=True)
    return ThreeLaneMetaTransport({
        lane: MetaTransport(
            client=client, credentials=values[lane][0],
            caller_ig_id=values[lane][1], token=values[lane][2],
        ) for lane in LANES
    })


def run_manual_pilot(source, workspace, *, live=False,
                     confirm_owner_approval=False, environment=None,
                     max_pages=1):
    """Read-only preflight, or one explicitly authorized page in a NEW store.

    Never opens a preexisting operational DB. A private work directory holds
    isolated coordinator and Capture databases and a dedicated RAW v2 DB.
    """
    if type(max_pages) is not int or not 1 <= max_pages <= 294:
        raise PilotConfigurationError("Manual page bound must be 1..294")
    accounts, digest = _validated_roster(source)
    if live is not True:
        return {
            "mode": "OFFLINE_ROSTER_PREFLIGHT",
            "official_accounts": len(accounts),
            "api_lanes": len(LANES),
            "post_limit": 4,
            "max_pages": max_pages,
            "live_meta_called": False,
        }
    if confirm_owner_approval is not True:
        raise PilotConfigurationError("Separate owner approval is required for Meta HTTP")

    env = os.environ if environment is None else environment
    values = _credentials(env)
    version = env.get("P6_META_GRAPH_VERSION", "v26.0")
    if not isinstance(version, str) or not VERSION.fullmatch(version):
        raise PilotConfigurationError("Invalid Graph API version")

    root = Path(workspace).expanduser()
    if root.is_symlink() or root.name.lower() == "collection.sqlite":
        raise PilotConfigurationError("Dedicated pilot directory required")
    root = root.resolve()
    day = datetime.now(timezone(timedelta(hours=9))).date()
    raw_path = root / "p6_pilot_raw.sqlite"
    control = root / day.isoformat() / "coordinator.sqlite"
    capture_path = root / day.isoformat() / "capture.sqlite"

    # Explicit port checks are done before any source is touched.
    if (len({raw_path, control, capture_path}) != 3
            or any(path.is_symlink() for path in (raw_path, control, capture_path))):
        raise PilotConfigurationError("Independent non-symlink P6 databases required")

    with ExitStack() as stack:
        raw_db = connect(raw_path, accounts, digest)
        stack.callback(raw_db.close)
        # The RAW contract writer explicitly owns BEGIN/COMMIT transactions.
        raw_db.isolation_level = None
        raw = ContractRawWriter(raw_db)
        coordinator = Coordinator(control)
        stack.callback(coordinator.db.close)
        coordinator.seed()
        capture = CaptureJournal(str(capture_path))
        stack.callback(capture.close)
        runner = build_verified_serial_runner(
            coordinator, capture, raw, _transports(values),
        )
        result = run_bounded_serial_steps(
            runner, accounts, max_steps=max_pages,
            run_id="p6-" + day.strftime("%Y%m%d"),
            roster_digest=digest, day_kst=day,
            api_version=version,
            caller_ids={lane: values[lane][1] for lane in LANES},
            approved=True,
        )
    return {
        "mode": "EXPLICIT_BOUNDED_SERIAL_ATTEMPT",
        "status": result["last_status"],
        "committed_pages": result["committed_pages"],
        "recovered_pages": result["recovered_pages"],
        "attempted_steps": result["attempted_steps"],
        "new_http_attempt_limit": max_pages,
    }



def read_pilot_status(workspace, *, day=None):
    """Inspect existing isolated pilot SQLite stores without creating or editing.

    No credentials, HTTP client, official roster file, coordinator construction,
    or scheduler are involved. Missing stores fail closed rather than silently
    producing an empty database. SQLite is opened in URI read-only mode.
    """
    root = Path(workspace).expanduser()
    if not root.is_dir() or root.is_symlink():
        raise PilotConfigurationError("Existing dedicated pilot directory required")
    root = root.resolve()
    if day is None:
        day = datetime.now(timezone(timedelta(hours=9))).date().isoformat()
    if not re.fullmatch(r"20[0-9]{2}-[01][0-9]-[0-3][0-9]", day):
        raise PilotConfigurationError("Invalid pilot date")
    raw_path = root / "p6_pilot_raw.sqlite"
    control_path = root / day / "coordinator.sqlite"
    if (not raw_path.is_file() or not control_path.is_file()
            or raw_path.is_symlink() or control_path.is_symlink()
            or (root / day).is_symlink()):
        raise PilotConfigurationError("Pilot RAW or coordinator store not found")
    def readonly(path):
        # URI mode=ro prevents SQLite from creating a missing file.
        return sqlite3.connect(path.as_uri() + "?mode=ro", uri=True)
    try:
        # sqlite3.Connection.__exit__ commits/rolls back but does NOT close.
        # Explicit closing is essential for Windows file-lock cleanup.
        with closing(readonly(raw_path)) as raw, closing(readonly(control_path)) as control:
            posts = raw.execute("SELECT COUNT(*) FROM posts").fetchone()[0]
            observations = raw.execute(
                "SELECT COUNT(*) FROM post_observations").fetchone()[0]
            receipts = raw.execute(
                "SELECT COUNT(*) FROM three_api_contract_receipts").fetchone()[0]
            lanes = {
                lane: {"done": 0, "pending": 0, "leased": 0}
                for lane in LANES
            }
            for lane, state, count in control.execute(
                    "SELECT lane,state,COUNT(*) FROM jobs GROUP BY lane,state"):
                if lane not in lanes or state not in lanes[lane]:
                    raise PilotConfigurationError("Unexpected pilot job state")
                lanes[lane][state] = count
    except sqlite3.Error as exc:
        raise PilotConfigurationError("Pilot read-only status unavailable") from exc
    return {
        "mode": "READ_ONLY_PILOT_STATUS", "day_kst": day,
        "posts": posts, "post_observations": observations,
        "durable_receipts": receipts, "lanes": lanes,
        "live_meta_called": False, "db_writes": 0,
    }



def read_pilot_failures(workspace, *, day=None):
    """Read-only, credential-free blocked-job diagnostics.

    Never returns request IDs, target usernames, payloads, cursors, tokens,
    or raw Meta response bodies. No transport or coordinator is instantiated.
    """
    root = Path(workspace).expanduser()
    if not root.is_dir() or root.is_symlink():
        raise PilotConfigurationError("Existing dedicated pilot directory required")
    root = root.resolve()
    if day is None:
        day = datetime.now(timezone(timedelta(hours=9))).date().isoformat()
    if not re.fullmatch(r"20[0-9]{2}-[01][0-9]-[0-3][0-9]", day):
        raise PilotConfigurationError("Invalid pilot date")
    control = root / day / "coordinator.sqlite"
    capture = root / day / "capture.sqlite"
    if (not control.is_file() or not capture.is_file()
            or control.is_symlink() or capture.is_symlink()
            or (root / day).is_symlink()):
        raise PilotConfigurationError("Pilot coordinator or Capture store not found")
    try:
        with closing(sqlite3.connect(control.as_uri() + "?mode=ro", uri=True)) as jobs, \
             closing(sqlite3.connect(capture.as_uri() + "?mode=ro", uri=True)) as records:
            bound = jobs.execute(
                "SELECT j.id,j.lane,j.state,j.account_ordinal,j.page,b.request_id "
                "FROM jobs j JOIN job_request_bindings b ON b.job_id=j.id "
                "WHERE j.state!='done' ORDER BY j.id"
            ).fetchall()
            result = []
            for job_id, lane, state, ordinal, page, request_id in bound:
                intent = records.execute(
                    "SELECT outcome FROM intents WHERE request_id=?",
                    (request_id,)).fetchone()
                failure = records.execute(
                    "SELECT payload FROM failures WHERE request_id=?",
                    (request_id,)).fetchone()
                capture_count = records.execute(
                    "SELECT COUNT(*) FROM captures WHERE request_id=?",
                    (request_id,)).fetchone()[0]
                response_count = records.execute(
                    "SELECT COUNT(*) FROM response_evidence WHERE request_id=?",
                    (request_id,)).fetchone()[0]
                data = json.loads(failure[0]) if failure else {}
                # Strict allowlist; do not expose even sanitized free-form text.
                category = data.get("category")
                scope = data.get("scope")
                status = data.get("http_status")
                result.append({
                    "job_id": job_id, "lane": lane, "state": state,
                    "account_ordinal": ordinal, "page": page,
                    "intent_outcome": intent[0] if intent else "missing",
                    "failure_category": category if category in (
                        "http_error", "permission", "auth", "rate_limit",
                        "outcome_unknown", "invalid_response", "capture_io",
                        "approval_denied", "usage_missing", "usage_limit") else "other",
                    "failure_scope": scope if scope in ("account", "lane", "global") else "unknown",
                    "http_status": status if type(status) is int and 100 <= status <= 599 else None,
                    "has_capture": bool(capture_count),
                    "has_response_evidence": bool(response_count),
                    "automatic_retry_allowed": False,
                })
    except (sqlite3.Error, ValueError, TypeError, KeyError) as exc:
        raise PilotConfigurationError("Read-only failure inspection unavailable") from None
    return {"mode": "READ_ONLY_FAILURE_DIAGNOSTIC", "day_kst": day,
            "blocked_jobs": result, "live_meta_called": False, "db_writes": 0}


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", type=Path,
                        help="Official 294-account account_list.csv (read-only)")
    parser.add_argument("--workspace", type=Path, required=True,
                        help="Dedicated isolated P6 pilot directory")
    parser.add_argument("--failures", action="store_true",
                        help="Inspect bound unfinished jobs without Meta or DB writes")
    parser.add_argument("--status", action="store_true",
                        help="Read existing pilot SQLite status only; no credentials/Meta")
    parser.add_argument("--day", type=str, default=None,
                        help="KST date YYYY-MM-DD for --status (default today)")
    parser.add_argument("--max-pages", type=int, default=1,
                        help="Explicit serial page cap, 1..294 (default 1)")
    parser.add_argument("--live", action="store_true",
                        help="Enable bounded Meta pages; requires separate owner approval")
    parser.add_argument("--confirm-owner-approval", action="store_true",
                        help="Explicitly confirm the separate owner authorization")
    options = parser.parse_args(argv)
    try:
        if options.status and options.failures:
            raise PilotConfigurationError("Choose one read-only mode")
        if options.failures:
            if options.live or options.confirm_owner_approval or options.source is not None or options.day is not None and not isinstance(options.day, str):
                raise PilotConfigurationError("Failure inspection cannot enable Meta")
            result = read_pilot_failures(options.workspace, day=options.day)
        elif options.status:
            if options.live or options.confirm_owner_approval:
                raise PilotConfigurationError("Status mode cannot enable Meta")
            result = read_pilot_status(options.workspace, day=options.day)
        else:
            if options.source is None or options.day is not None:
                raise PilotConfigurationError("Source required; day is status-only")
            result = run_manual_pilot(
                options.source, options.workspace,
                live=options.live,
                confirm_owner_approval=options.confirm_owner_approval,
                max_pages=options.max_pages,
            )
    except (ValueError, TypeError, RuntimeError, sqlite3.Error):
        # Preserve the fail-closed exit and never print credentials or exception
        # strings. On live failure, show verified, read-only durable progress so
        # operators can distinguish a partial batch from zero committed pages.
        if options.live and not options.status and not options.failures:
            try:
                progress = read_pilot_status(options.workspace)
                print(json.dumps({
                    "mode": "LIVE_STOPPED_SAFE_PROGRESS",
                    "status": "stopped_safely",
                    "durable_receipts": progress["durable_receipts"],
                    "posts": progress["posts"],
                    "lanes": progress["lanes"],
                    "live_meta_called": "unknown",
                    "db_writes": 0,
                }, ensure_ascii=False, sort_keys=True))
            except (ValueError, TypeError, RuntimeError, sqlite3.Error):
                pass
        parser.exit(2, "P6 pilot stopped safely; check local input/configuration.\n")
    print(json.dumps(result, ensure_ascii=False, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
