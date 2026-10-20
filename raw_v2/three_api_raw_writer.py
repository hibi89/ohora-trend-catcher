"""Offline-only additive RAW v2 writer. Requires an already-open isolated SQLite connection.

Caller owns database lifecycle. This module never opens a filesystem path, fetches
Meta data, updates legacy cursors, or acknowledges a source capture.
"""
import hashlib
import json
from pathlib import Path
import sqlite3
from datetime import datetime, timezone

class RawWriteError(ValueError):
    pass

def canonical(value):
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False, allow_nan=False)

def _clock(value):
    if not isinstance(value, str):
        raise RawWriteError("Immutable capture time required")
    try:
        dt = datetime.fromisoformat(value.replace("Z", "+00:00"))
        if dt.utcoffset() is None:
            raise ValueError()
    except ValueError:
        raise RawWriteError("Invalid capture clock") from None
    return value

def _validate_schema(db):
    required = {"accounts", "posts", "post_media", "post_observations",
                "account_observations", "carousel_status", "request_log",
                "field_observations", "runs", "slots", "slot_accounts"}
    present = {r[0] for r in db.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    if not required <= present:
        raise RawWriteError("RAW v2 schema required")


def _reject_operational_database(db):
    """Refuse the repository's live RAW filename even when a handle is injected."""
    for _, name, filename in db.execute("PRAGMA database_list"):
        if name == "main" and filename:
            if Path(filename).name.lower() == "collection.sqlite" or (
                    Path(filename).resolve().name.lower() == "collection.sqlite"):
                raise RawWriteError("Operating RAW database is not allowed")

def _contract_record_parts(record):
    from three_api_runtime_contract import CaptureRecord, canonical_bytes
    if not isinstance(record, CaptureRecord):
        raise RawWriteError("Validated contract capture required")
    raw = canonical_bytes(record).decode("utf-8")
    if hashlib.sha256(raw.encode("utf-8")).hexdigest() != record.capture_digest:
        raise RawWriteError("Contract capture digest mismatch")
    return record, raw


def _raw_receipt_id(capture_id):
    return "rawv2:" + capture_id


def _contract_receipt(db, capture_id, *, expected_digest=None, status="already_committed"):
    from three_api_runtime_contract import CONTRACT_VERSION, RawWriteReceipt
    row = db.execute(
        "SELECT capture_digest,record_json,raw_receipt_id,committed_at,row_counts_json "
        "FROM three_api_contract_receipts WHERE capture_id=?", (capture_id,)
    ).fetchone()
    if row is None:
        return None
    digest, record_json, raw_id, committed_at, counts_json = row
    actual = hashlib.sha256(record_json.encode("utf-8")).hexdigest()
    if digest != actual or (expected_digest is not None and digest != expected_digest):
        raise RawWriteError("Contract RAW receipt digest conflict")
    if not isinstance(committed_at, str) or not committed_at:
        raise RawWriteError("Contract RAW receipt is incomplete")
    clock = datetime.fromisoformat(committed_at.replace("Z", "+00:00"))
    counts = json.loads(counts_json)
    return RawWriteReceipt(CONTRACT_VERSION, capture_id, digest, raw_id,
                           status, clock, counts)


def get_contract_raw_receipt(db: sqlite3.Connection, capture_id: str):
    """Read and validate an immutable contract receipt without writing."""
    if db.in_transaction:
        raise RawWriteError("Receipt lookup requires a committed database state")
    exists = db.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name='three_api_contract_receipts'"
    ).fetchone()
    if exists is None:
        return None
    receipt = _contract_receipt(db, capture_id)
    return receipt


def write_capture(db: sqlite3.Connection, capture: dict, *, contract_record=None):
    """Atomic single-DB write; input is a validated synthetic capture envelope.

    Compatibility path for synthetic envelopes, plus the contract adapter
    used by the P6 executor. No cross-database atomicity or source
    acknowledgement is implied.
    """
    if not isinstance(capture, dict):
        raise RawWriteError("Capture object required")
    _reject_operational_database(db)
    required = {"capture_id", "account_key", "observed_at", "posts"}
    optional = {"profile", "phase", "request_id", "run_id", "api_version", "field_status",
                "post_field_status", "carousel_completeness"}
    if not required <= capture.keys() or set(capture) - (required | optional):
        raise RawWriteError("Unexpected capture envelope")
    cid, account, clock = capture["capture_id"], capture["account_key"], _clock(capture["observed_at"])
    if not isinstance(cid, str) or not cid or not isinstance(account, str) or not account:
        raise RawWriteError("Capture identity required")
    request_id = capture.get("request_id", cid)
    run = capture.get("run_id", "three-api-" + cid)
    api_version = capture.get("api_version")
    phase = capture.get("phase", "front")
    if not all(isinstance(value, str) and value for value in (request_id, run)):
        raise RawWriteError("Request and run identities required")
    if phase not in ("front", "catchup"):
        raise RawWriteError("Unsupported capture phase")
    if api_version is not None and (not isinstance(api_version, str) or not api_version):
        raise RawWriteError("Invalid API version")
    posts = capture["posts"]
    if not isinstance(posts, list) or any(not isinstance(p, dict) for p in posts):
        raise RawWriteError("Posts must be objects")
    if any(not isinstance(p.get("id"), str) or not p["id"] for p in posts):
        raise RawWriteError("Invalid post ID")
    if len({p["id"] for p in posts}) != len(posts):
        raise RawWriteError("Duplicate or invalid post ID")
    post_field_status = capture.get("post_field_status")
    field_status = capture.get("field_status")
    carousel_completeness = capture.get("carousel_completeness")
    allowed_states = {"returned", "null", "absent", "not_tested"}
    if post_field_status is not None:
        if not isinstance(post_field_status, dict) or set(post_field_status) != {p["id"] for p in posts}:
            raise RawWriteError("Post field evidence must match captured posts")
        for post in posts:
            states = post_field_status[post["id"]]
            if not isinstance(states, dict) or set(states) != {"like_count", "comments_count"}:
                raise RawWriteError("Post field states are incomplete")
            for key, state in states.items():
                present = key in post
                if (state not in allowed_states or
                    (state == "returned" and (not present or post[key] is None)) or
                    (state == "null" and (not present or post[key] is not None)) or
                    (state in ("absent", "not_tested") and present)):
                    raise RawWriteError("Post field state conflicts with captured value")
    if field_status is not None:
        if not isinstance(field_status, dict) or set(field_status) != {"followers_count", "media_count"}:
            raise RawWriteError("Account field evidence is incomplete")
        if any(state not in allowed_states for state in field_status.values()):
            raise RawWriteError("Invalid account field state")
    if carousel_completeness is not None:
        if not isinstance(carousel_completeness, dict) or set(carousel_completeness) != {p["id"] for p in posts}:
            raise RawWriteError("Carousel evidence must match captured posts")
        for post in posts:
            state = carousel_completeness[post["id"]]
            if post.get("media_type") == "CAROUSEL_ALBUM":
                if state not in ("complete", "partial", "unknown"):
                    raise RawWriteError("Invalid carousel completeness state")
                if state in ("complete", "partial") and not isinstance(post.get("children"), list):
                    raise RawWriteError("Complete carousel requires child rows")
                if state == "unknown" and post.get("children") is not None:
                    raise RawWriteError("Unknown carousel cannot claim child rows")
            elif state != "not_applicable":
                raise RawWriteError("Non-carousel must be not_applicable")
    for post in posts:
        if post.get("media_type") == "CAROUSEL_ALBUM":
            children = post.get("children", [])
            if children is None and (carousel_completeness or {}).get(post["id"]) == "unknown":
                # Unknown means no complete child page was observed; store no
                # child rows while preserving that uncertainty below.
                children = []
            if not isinstance(children, list) or any(not isinstance(c, dict) for c in children):
                raise RawWriteError("Invalid carousel children")
            child_ids = [c.get("media_id", c.get("id")) for c in children]
            if any(not isinstance(mid, str) or not mid for mid in child_ids) or len(set(child_ids)) != len(child_ids):
                raise RawWriteError("Invalid or duplicate carousel child ID")
        elif post.get("children") not in (None, []):
            raise RawWriteError("Non-carousel post cannot contain child media")
        for field in ("like_count", "comments_count"):
            if field in post and post[field] is not None and (type(post[field]) is not int or post[field] < 0):
                raise RawWriteError("Invalid engagement count")
    digest = hashlib.sha256(canonical(capture).encode()).hexdigest()
    contract_json = None
    contract_digest = None
    committed_at = None
    contract_counts = None
    if contract_record is not None:
        contract_record, contract_json = _contract_record_parts(contract_record)
        if (contract_record.capture_id != cid or contract_record.request.request_id != request_id or
                run != "three-api-" + contract_record.request.run_id or
                contract_record.request.account_key != account or
                api_version != contract_record.request.api_version or
                contract_record.observed_at.isoformat(timespec="microseconds").replace("+00:00", "Z") != clock):
            raise RawWriteError("RAW envelope differs from immutable contract capture")
        contract_digest = contract_record.capture_digest
    if db.in_transaction:
        raise RawWriteError("Writer requires exclusive transaction ownership")
    db.execute("PRAGMA foreign_keys=ON")
    db.execute("BEGIN IMMEDIATE")
    try:
        _validate_schema(db)
        db.execute("""CREATE TABLE IF NOT EXISTS three_api_raw_receipts(
            capture_id TEXT PRIMARY KEY, account_key TEXT NOT NULL,
            observed_at TEXT NOT NULL, payload_sha256 TEXT NOT NULL,
            payload_json TEXT NOT NULL)""")
        db.execute("""CREATE TABLE IF NOT EXISTS three_api_contract_receipts(
            capture_id TEXT PRIMARY KEY, capture_digest TEXT NOT NULL,
            record_json TEXT NOT NULL, raw_receipt_id TEXT NOT NULL UNIQUE,
            committed_at TEXT NOT NULL, row_counts_json TEXT NOT NULL)""")
        previous = db.execute("SELECT * FROM three_api_raw_receipts WHERE capture_id=?", (cid,)).fetchone()
        if previous:
            if tuple(previous) != (cid, account, clock, digest, canonical(capture)):
                raise RawWriteError("Immutable capture receipt conflict")
            if contract_record is not None:
                existing_contract = _contract_receipt(
                    db, cid, expected_digest=contract_digest, status="already_committed")
                if existing_contract is None:
                    raise RawWriteError("RAW capture has no verified contract receipt")
                stored = db.execute(
                    "SELECT record_json FROM three_api_contract_receipts WHERE capture_id=?",
                    (cid,)).fetchone()[0]
                if stored != contract_json:
                    raise RawWriteError("Immutable contract capture conflict")
            db.execute("COMMIT")
            return "already_committed"
        if not db.execute("SELECT 1 FROM accounts WHERE account_key=?", (account,)).fetchone():
            raise RawWriteError("Unknown account")
        existing_run = db.execute(
            "SELECT mode,api_version FROM runs WHERE run_id=?", (run,)
        ).fetchone()
        if existing_run is not None and (
                existing_run[0] != "three_api_capture" or
                (api_version is not None and existing_run[1] not in (None, api_version))):
            raise RawWriteError("Run identity conflict")
        if existing_run is None:
            # A page commit does not prove the daily run is complete. Leave the
            # aggregate run open for its eventual orchestrator to finalize.
            db.execute("INSERT INTO runs(run_id,started_at,api_version,mode,status) VALUES(?,?,?,?,?)",
                       (run, clock, api_version, "three_api_capture", "started"))
        media_inserted_count = 0
        for post in posts:
            pid = post["id"]
            old = db.execute("SELECT account_key FROM posts WHERE post_id=?", (pid,)).fetchone()
            if old and old[0] != account:
                raise RawWriteError("Cross-account post collision")
            if not old:
                db.execute("""INSERT INTO posts(post_id,account_key,username,caption,timestamp,
                    permalink,media_type,media_product_type,media_url,thumbnail_url,first_seen_run)
                    VALUES(?,?,?,?,?,?,?,?,?,?,?)""",
                    (pid, account, *(post.get(k) for k in ("username","caption","timestamp",
                     "permalink","media_type","media_product_type","media_url","thumbnail_url")), run))
            media = post.get("children", [])
            if media is None and (carousel_completeness or {}).get(pid, "unknown") == "unknown":
                media = []
            if not isinstance(media, list):
                raise RawWriteError("Invalid carousel children")
            if post.get("media_type") != "CAROUSEL_ALBUM":
                media = [dict(post, media_id=pid)]
            for i, item in enumerate(media):
                mid = item.get("media_id", item.get("id"))
                if not isinstance(mid, str) or not mid:
                    raise RawWriteError("Invalid media ID")
                role = "carousel_child" if post.get("media_type") == "CAROUSEL_ALBUM" else "single"
                existing = db.execute("SELECT post_id,account_key,media_role,item_order FROM post_media WHERE media_id=?", (mid,)).fetchone()
                # Existing media identity belongs to a post and role, but its
                # original item_order is not immutable structure history.
                # A carousel may reorder the SAME child IDs on reobservation.
                # P2 media_structure_members records the ordered state.
                if existing and tuple(existing[:3]) != (pid,account,role):
                    raise RawWriteError("Media identity collision")
                if not existing:
                    db.execute("""INSERT INTO post_media(media_id,post_id,account_key,media_role,item_order,
                        media_type,media_url,thumbnail_url,first_seen_run) VALUES(?,?,?,?,?,?,?,?,?)""",
                        (mid,pid,account,role,i,item.get("media_type"),item.get("media_url"),item.get("thumbnail_url"),run))
                    media_inserted_count += 1
            if post.get("media_type") == "CAROUSEL_ALBUM":
                coverage = (carousel_completeness or {}).get(pid, "unknown")
                db.execute("""INSERT INTO carousel_status(post_id,account_key,child_rows,has_next_page,coverage,last_request_id)
                    VALUES(?,?,?,?,?,?) ON CONFLICT(post_id) DO NOTHING""",
                    (pid,account,len(media),None,coverage,request_id))
            db.execute("""INSERT INTO post_observations(post_id,observed_at,run_id,like_count,
                comments_count,like_present,comments_present,request_id) VALUES(?,?,?,?,?,?,?,?)""",
                (pid,clock,run,post.get("like_count"),post.get("comments_count"),
                 int("like_count" in post),int("comments_count" in post),request_id))
            for field in ("like_count","comments_count","shortcode","view_count"):
                per_post = capture.get("post_field_status", {}).get(pid, {})
                status = (per_post.get(field) if field in ("like_count", "comments_count") else None)
                if status is None:
                    status = ("not_tested" if field in ("shortcode","view_count") else
                              "absent" if field not in post else "null" if post[field] is None else "returned")
                db.execute("""INSERT INTO field_observations(run_id,request_id,account_key,resource_type,
                    media_type,field_name,status,item_id) VALUES(?,?,?,?,?,?,?,?)""",
                    (run,request_id,account,"posts",post.get("media_type") or "",field,status,pid))
        profile = capture.get("profile")
        if profile is not None:
            if not isinstance(profile, dict):
                raise RawWriteError("Invalid profile")
            db.execute("""INSERT INTO account_observations(account_key,observed_at,run_id,
                followers_count,follows_count,media_count,request_id) VALUES(?,?,?,?,?,?,?)""",
                (account,clock,run,profile.get("followers_count"),profile.get("follows_count"),
                 profile.get("media_count"),request_id))
        if field_status is not None:
            for field, state in field_status.items():
                db.execute("""INSERT INTO field_observations(run_id,request_id,account_key,resource_type,
                    media_type,field_name,status,item_id) VALUES(?,?,?,?,?,?,?,?)""",
                    (run,request_id,account,"account","",field,state,account))
        db.execute("""INSERT INTO request_log(run_id,request_id,account_key,phase,started_at,finished_at,
            status,records_received) VALUES(?,?,?,?,?,?,?,?)""",
            (run,request_id,account,phase,clock,clock,"success",len(posts)))
        # P2 projection is in the SAME SQLite transaction as legacy RAW rows,
        # immutable receipts and all request-specific metrics.
        p2_counts = None
        if contract_record is not None:
            from three_api_observation_versions import project_contract_observation
            p2_counts = project_contract_observation(db, contract_record)
        db.execute("INSERT INTO three_api_raw_receipts VALUES(?,?,?,?,?)",
                   (cid,account,clock,digest,canonical(capture)))
        if contract_record is not None:
            committed_at = datetime.now(timezone.utc).isoformat(timespec="microseconds").replace("+00:00", "Z")
            contract_counts = {
                "posts_received": len(posts),
                "posts_observed": db.execute(
                    "SELECT COUNT(*) FROM post_observations WHERE request_id=?", (request_id,)
                ).fetchone()[0],
                "media_inserted": media_inserted_count,
                "account_observations": db.execute(
                    "SELECT COUNT(*) FROM account_observations WHERE request_id=?", (request_id,)
                ).fetchone()[0],
                "field_observations": db.execute(
                    "SELECT COUNT(*) FROM field_observations WHERE request_id=?", (request_id,)
                ).fetchone()[0],
                **(p2_counts or {}),
            }
            db.execute("INSERT INTO three_api_contract_receipts VALUES(?,?,?,?,?,?)",
                (cid, contract_digest, contract_json, _raw_receipt_id(cid),
                 committed_at, canonical(contract_counts)))
        db.execute("COMMIT")
        return "committed"
    except BaseException:
        db.execute("ROLLBACK")
        raise


def write_contract_capture(db: sqlite3.Connection, record):
    """Adapt C's immutable CaptureRecord to the existing RAW v2 tables."""
    record, _ = _contract_record_parts(record)
    profile = {key: record.account_observation[key]
               for key in ("followers_count", "follows_count", "media_count")
               if key in record.account_observation}
    envelope = {
        "capture_id": record.capture_id,
        "request_id": record.request.request_id,
        "run_id": "three-api-" + record.request.run_id,
        "api_version": record.request.api_version,
        "account_key": record.request.account_key,
        "observed_at": record.observed_at.isoformat(timespec="microseconds").replace("+00:00", "Z"),
        "phase": record.request.phase,
        "posts": [dict(post) for post in record.normalized_posts],
        "profile": profile or None,
        "field_status": dict(record.field_status),
        "post_field_status": {key: dict(value) for key, value in (record.post_field_status or {}).items()},
        "carousel_completeness": dict(record.carousel_completeness),
    }
    status = write_capture(db, envelope, contract_record=record)
    receipt = _contract_receipt(db, record.capture_id,
                                expected_digest=record.capture_digest,
                                status=status)
    if receipt is None:
        raise RawWriteError("Committed RAW receipt could not be re-read")
    return receipt


class ContractRawWriter:
    """RawWriterPort over a caller-owned, isolated RAW v2 SQLite connection."""

    def __init__(self, db: sqlite3.Connection):
        if not isinstance(db, sqlite3.Connection):
            raise TypeError("Caller-owned SQLite connection required")
        _reject_operational_database(db)
        self._db = db

    def write_capture(self, record):
        return write_contract_capture(self._db, record)

    def get_raw_receipt(self, capture_id):
        return get_contract_raw_receipt(self._db, capture_id)
