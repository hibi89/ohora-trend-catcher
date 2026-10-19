"""Offline-only additive RAW v2 writer. Requires an already-open isolated SQLite connection.

Caller owns database lifecycle. This module never opens a filesystem path, fetches
Meta data, updates legacy cursors, or acknowledges a source capture.
"""
import hashlib
import json
import sqlite3
from datetime import datetime

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

def write_capture(db: sqlite3.Connection, capture: dict):
    """Atomic single-DB write; input is a validated synthetic capture envelope.

    Envelope is provisional until C's runtime contract is finalized.
    No cross-database atomicity or source acknowledgement is implied.
    """
    if not isinstance(capture, dict):
        raise RawWriteError("Capture object required")
    required = {"capture_id", "account_key", "observed_at", "posts"}
    if not required <= capture.keys() or set(capture) - (required | {"profile", "phase"}):
        raise RawWriteError("Unexpected capture envelope")
    cid, account, clock = capture["capture_id"], capture["account_key"], _clock(capture["observed_at"])
    if not isinstance(cid, str) or not cid or not isinstance(account, str) or not account:
        raise RawWriteError("Capture identity required")
    posts = capture["posts"]
    if not isinstance(posts, list) or any(not isinstance(p, dict) for p in posts):
        raise RawWriteError("Posts must be objects")
    if len({p.get("id") for p in posts}) != len(posts) or any(not isinstance(p.get("id"), str) for p in posts):
        raise RawWriteError("Duplicate or invalid post ID")
    for post in posts:
        if post.get("media_type") == "CAROUSEL_ALBUM":
            children = post.get("children", [])
            if not isinstance(children, list) or any(not isinstance(c, dict) for c in children):
                raise RawWriteError("Invalid carousel children")
            child_ids = [c.get("media_id", c.get("id")) for c in children]
            if any(not isinstance(mid, str) or not mid for mid in child_ids) or len(set(child_ids)) != len(child_ids):
                raise RawWriteError("Invalid or duplicate carousel child ID")
        for field in ("like_count", "comments_count"):
            if field in post and post[field] is not None and (type(post[field]) is not int or post[field] < 0):
                raise RawWriteError("Invalid engagement count")
    digest = hashlib.sha256(canonical(capture).encode()).hexdigest()
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
        previous = db.execute("SELECT * FROM three_api_raw_receipts WHERE capture_id=?", (cid,)).fetchone()
        if previous:
            if tuple(previous) != (cid, account, clock, digest, canonical(capture)):
                raise RawWriteError("Immutable capture receipt conflict")
            db.execute("COMMIT")
            return "already_committed"
        if not db.execute("SELECT 1 FROM accounts WHERE account_key=?", (account,)).fetchone():
            raise RawWriteError("Unknown account")
        run = "three-api-" + cid
        if db.execute("SELECT 1 FROM runs WHERE run_id=?", (run,)).fetchone():
            raise RawWriteError("Run identity conflict")
        db.execute("INSERT INTO runs(run_id,started_at,finished_at,mode,status) VALUES(?,?,?,?,?)",
                   (run, clock, clock, "three_api_capture", "complete"))
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
                if existing and tuple(existing) != (pid,account,role,i):
                    raise RawWriteError("Media identity collision")
                if not existing:
                    db.execute("""INSERT INTO post_media(media_id,post_id,account_key,media_role,item_order,
                        media_type,media_url,thumbnail_url,first_seen_run) VALUES(?,?,?,?,?,?,?,?,?)""",
                        (mid,pid,account,role,i,item.get("media_type"),item.get("media_url"),item.get("thumbnail_url"),run))
            if post.get("media_type") == "CAROUSEL_ALBUM":
                db.execute("""INSERT INTO carousel_status(post_id,account_key,child_rows,has_next_page,coverage,last_request_id)
                    VALUES(?,?,?,?,?,?) ON CONFLICT(post_id) DO NOTHING""",
                    (pid,account,len(media),None,"unknown",cid))
            db.execute("""INSERT INTO post_observations(post_id,observed_at,run_id,like_count,
                comments_count,like_present,comments_present,request_id) VALUES(?,?,?,?,?,?,?,?)""",
                (pid,clock,run,post.get("like_count"),post.get("comments_count"),
                 int("like_count" in post),int("comments_count" in post),cid))
            for field in ("like_count","comments_count","shortcode","view_count"):
                status = ("not_tested" if field in ("shortcode","view_count") else
                          "absent" if field not in post else "null" if post[field] is None else "returned")
                db.execute("""INSERT INTO field_observations(run_id,request_id,account_key,resource_type,
                    media_type,field_name,status,item_id) VALUES(?,?,?,?,?,?,?,?)""",
                    (run,cid,account,"posts",post.get("media_type") or "",field,status,pid))
        profile = capture.get("profile")
        if profile is not None:
            if not isinstance(profile, dict):
                raise RawWriteError("Invalid profile")
            db.execute("""INSERT INTO account_observations(account_key,observed_at,run_id,
                followers_count,follows_count,media_count,request_id) VALUES(?,?,?,?,?,?,?)""",
                (account,clock,run,profile.get("followers_count"),profile.get("follows_count"),
                 profile.get("media_count"),cid))
        db.execute("""INSERT INTO request_log(run_id,request_id,account_key,phase,started_at,finished_at,
            status,records_received) VALUES(?,?,?,?,?,?,?,?)""",
            (run,cid,account,capture.get("phase","front"),clock,clock,"success",len(posts)))
        db.execute("INSERT INTO three_api_raw_receipts VALUES(?,?,?,?,?)",
                   (cid,account,clock,digest,canonical(capture)))
        db.execute("COMMIT")
        return "committed"
    except BaseException:
        db.execute("ROLLBACK")
        raise
