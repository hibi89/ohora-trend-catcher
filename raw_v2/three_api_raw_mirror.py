"""Offline RAW-shaped SQLite mirror for synthetic 3-API page payloads.

NOT the RAW v2 production schema or migration. No credentials, HTTP client,
environment loader, background scheduler, or import from production collector.
Only a file named three_api_raw_mirror.sqlite may be opened. A durable staged
receipt is promoted atomically to posts, parent/child media and observations;
after interruption, reconcile_pending() promotes it without refetching.
"""
from __future__ import annotations

from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import re
import sqlite3

_ID = re.compile(r"[0-9]{1,64}\Z")
_ACCOUNT = re.compile(r"[A-Za-z0-9_:-]{1,128}\Z")
_RECEIPT = re.compile(r"[A-Za-z0-9_.:-]{1,128}\Z")
_TEXT_FIELDS = ("username", "caption", "timestamp", "permalink",
                "media_type", "media_product_type", "media_url", "thumbnail_url")
_CHILD_FIELDS = ("media_type", "media_url", "thumbnail_url")
_SCHEMA_MARK = "OHORA_SYNTHETIC_RAW_MIRROR_V1"


class OfflineRawMirrorError(ValueError):
    """Sanitized failure; does not echo payload, path, token or post ID."""


def _validated_text(value, *, maximum=65536):
    if value is not None and (not isinstance(value, str) or len(value) > maximum):
        raise OfflineRawMirrorError("Invalid synthetic metadata field")
    return value


def _post_id(value):
    if not isinstance(value, str) or not _ID.fullmatch(value):
        raise OfflineRawMirrorError("Invalid numeric media identifier")
    return value


def _normalize_post(post):
    if not isinstance(post, dict):
        raise OfflineRawMirrorError("Expected a synthetic media object")
    result = {"id": _post_id(post.get("id"))}
    for field in _TEXT_FIELDS:
        if field in post:
            result[field] = _validated_text(
                post[field], maximum=2048 if field.endswith("url") or field == "permalink" else 65536
            )
    for field in ("like_count", "comments_count"):
        if field in post:
            n = post[field]
            if type(n) is not int or n < 0:
                raise OfflineRawMirrorError("Invalid engagement counter")
            result[field] = n
    children = post.get("children", [])
    if not isinstance(children, list) or len(children) > 20:
        raise OfflineRawMirrorError("Invalid synthetic carousel children")
    cleaned = []
    for position, child in enumerate(children, 1):
        if not isinstance(child, dict):
            raise OfflineRawMirrorError("Invalid carousel child")
        item = {"media_id": _post_id(child.get("id")), "item_order": position}
        for field in _CHILD_FIELDS:
            if field in child:
                item[field] = _validated_text(
                    child[field], maximum=2048 if field.endswith("url") else 65536
                )
        cleaned.append(item)
    all_ids = [result["id"]] + [c["media_id"] for c in cleaned]
    if len(all_ids) != len(set(all_ids)):
        raise OfflineRawMirrorError("Duplicate media identity within a post")
    result["children"] = cleaned
    return result


def _observation_time(value):
    if not isinstance(value, str):
        raise OfflineRawMirrorError("Explicit timezone-aware observation timestamp required")
    try:
        parsed = datetime.fromisoformat(value)
    except ValueError:
        raise OfflineRawMirrorError("Invalid observation timestamp") from None
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise OfflineRawMirrorError("Explicit timezone-aware observation timestamp required")
    return parsed.astimezone(timezone.utc).isoformat()


def _canonical(data):
    return json.dumps(data, ensure_ascii=False, sort_keys=True,
                      separators=(",", ":"), allow_nan=False)


class OfflineRawMirror:
    """A separate synthetic SQLite file, never an existing operational DB."""

    def __init__(self, path):
        if not isinstance(path, (str, Path)):
            raise OfflineRawMirrorError("Explicit isolated mirror path required")
        path = Path(path)
        if (path.name != "three_api_raw_mirror.sqlite"
                or path.is_symlink() or path.resolve().name != path.name):
            raise OfflineRawMirrorError("Only isolated synthetic mirror DB is permitted")
        path.parent.mkdir(parents=True, exist_ok=True)
        existed = path.exists()
        self.path = path
        self.db = sqlite3.connect(path, timeout=30, isolation_level=None)
        self.db.row_factory = sqlite3.Row
        try:
            if existed:
                try:
                    value = self.db.execute(
                        "SELECT value FROM raw_mirror_meta WHERE key='schema'"
                    ).fetchone()
                    tables = {row[0] for row in self.db.execute(
                        "SELECT name FROM sqlite_master WHERE type='table'"
                    )}
                except sqlite3.Error:
                    raise OfflineRawMirrorError("Existing database is not a synthetic mirror") from None
                if (value is None or value["value"] != _SCHEMA_MARK
                        or not {"raw_mirror_meta", "page_receipts", "raw_posts",
                                "raw_post_media", "raw_post_observations"}.issubset(tables)):
                    raise OfflineRawMirrorError("Existing database is not a synthetic mirror")
            else:
                self.db.executescript("""
                  CREATE TABLE raw_mirror_meta (key TEXT PRIMARY KEY, value TEXT NOT NULL);
                  CREATE TABLE page_receipts (
                    request_id TEXT PRIMARY KEY, account_key TEXT NOT NULL,
                    observed_at TEXT NOT NULL, payload_sha256 TEXT NOT NULL,
                    payload_json TEXT NOT NULL, state TEXT NOT NULL
                      CHECK(state IN ('staged','committed'))
                  );
                  CREATE TABLE raw_posts (
                    post_id TEXT PRIMARY KEY, account_key TEXT NOT NULL,
                    username TEXT, caption TEXT, timestamp TEXT, permalink TEXT,
                    media_type TEXT, media_product_type TEXT, media_url TEXT,
                    thumbnail_url TEXT, first_seen_request TEXT NOT NULL
                  );
                  CREATE TABLE raw_post_media (
                    media_id TEXT PRIMARY KEY, post_id TEXT NOT NULL
                      REFERENCES raw_posts(post_id),
                    account_key TEXT NOT NULL, media_role TEXT NOT NULL,
                    item_order INTEGER NOT NULL, media_type TEXT,
                    media_url TEXT, thumbnail_url TEXT,
                    first_seen_request TEXT NOT NULL
                  );
                  CREATE TABLE raw_post_observations (
                    post_id TEXT NOT NULL REFERENCES raw_posts(post_id),
                    request_id TEXT NOT NULL, observed_at TEXT NOT NULL,
                    like_count INTEGER, comments_count INTEGER,
                    like_present INTEGER NOT NULL, comments_present INTEGER NOT NULL,
                    PRIMARY KEY(post_id,request_id)
                  );
                """)
                self.db.execute(
                    "INSERT INTO raw_mirror_meta VALUES ('schema', ?)", (_SCHEMA_MARK,)
                )
            self.db.execute("PRAGMA foreign_keys=ON")
            self.db.execute("PRAGMA busy_timeout=30000")
        except BaseException:
            self.db.close()
            raise

    def close(self):
        self.db.close()

    def stage_page(self, request_id, account_key, observed_at, posts):
        """Persist an immutable, allowlisted synthetic page before promotion."""
        if not isinstance(request_id, str) or not _RECEIPT.fullmatch(request_id):
            raise OfflineRawMirrorError("Invalid synthetic receipt")
        if not isinstance(account_key, str) or not _ACCOUNT.fullmatch(account_key):
            raise OfflineRawMirrorError("Invalid account key")
        observed_at = _observation_time(observed_at)
        if not isinstance(posts, list) or len(posts) > 4:
            raise OfflineRawMirrorError("Synthetic page exceeds four posts")
        normalized = [_normalize_post(p) for p in posts]
        ids = [p["id"] for p in normalized]
        if len(ids) != len(set(ids)):
            raise OfflineRawMirrorError("Duplicate post in synthetic page")
        media_ids = [p["id"] for p in normalized]
        media_ids += [c["media_id"] for p in normalized for c in p["children"]]
        if len(media_ids) != len(set(media_ids)):
            raise OfflineRawMirrorError("Conflicting media identity within page")
        payload = _canonical(normalized)
        digest = hashlib.sha256(payload.encode("utf-8")).hexdigest()
        self.db.execute("BEGIN IMMEDIATE")
        try:
            row = self.db.execute(
                "SELECT account_key,observed_at,payload_sha256,payload_json,state "
                "FROM page_receipts WHERE request_id=?", (request_id,)
            ).fetchone()
            if row:
                if (row["account_key"] != account_key or row["observed_at"] != observed_at
                        or row["payload_sha256"] != digest or row["payload_json"] != payload):
                    raise OfflineRawMirrorError("Conflicting immutable page receipt")
                status = row["state"]
            else:
                self.db.execute(
                    "INSERT INTO page_receipts VALUES (?,?,?,?,?,'staged')",
                    (request_id, account_key, observed_at, digest, payload)
                )
                status = "staged"
            self.db.execute("COMMIT")
            return {"status": status, "post_count": len(normalized)}
        except BaseException:
            self.db.execute("ROLLBACK")
            raise

    def _insert_media(self, post_id, account_key, media_id, role, order, media, receipt):
        existing = self.db.execute(
            "SELECT post_id,account_key,media_role,item_order FROM raw_post_media "
            "WHERE media_id=?", (media_id,)
        ).fetchone()
        if existing:
            if tuple(existing) != (post_id, account_key, role, order):
                raise OfflineRawMirrorError("Media identity maps to a different post")
            return
        self.db.execute(
            "INSERT INTO raw_post_media VALUES (?,?,?,?,?,?,?,?,?)",
            (media_id, post_id, account_key, role, order,
             media.get("media_type"), media.get("media_url"),
             media.get("thumbnail_url"), receipt)
        )

    def promote_page(self, request_id):
        """Atomic promotion; any insertion failure rolls back every row."""
        if not isinstance(request_id, str) or not _RECEIPT.fullmatch(request_id):
            raise OfflineRawMirrorError("Invalid synthetic receipt")
        self.db.execute("BEGIN IMMEDIATE")
        try:
            row = self.db.execute(
                "SELECT * FROM page_receipts WHERE request_id=?", (request_id,)
            ).fetchone()
            if row is None:
                raise OfflineRawMirrorError("Missing staged page")
            if row["state"] == "committed":
                self.db.execute("COMMIT")
                return {"status": "already_committed"}
            if (row["state"] != "staged" or
                    hashlib.sha256(row["payload_json"].encode("utf-8")).hexdigest()
                    != row["payload_sha256"]):
                raise OfflineRawMirrorError("Page receipt integrity mismatch")
            try:
                posts = json.loads(row["payload_json"])
            except (TypeError, ValueError):
                raise OfflineRawMirrorError("Invalid staged payload") from None
            account, observed = row["account_key"], row["observed_at"]
            for post in posts:
                pid = post["id"]
                existing = self.db.execute(
                    "SELECT account_key FROM raw_posts WHERE post_id=?", (pid,)
                ).fetchone()
                if existing and existing["account_key"] != account:
                    raise OfflineRawMirrorError("Post belongs to a different account")
                if existing is None:
                    self.db.execute(
                        "INSERT INTO raw_posts VALUES (?,?,?,?,?,?,?,?,?,?,?)",
                        (pid, account, *(post.get(field) for field in _TEXT_FIELDS),
                         request_id)
                    )
                self._insert_media(pid, account, pid, "parent", 0, post, request_id)
                for child in post["children"]:
                    self._insert_media(
                        pid, account, child["media_id"], "child",
                        child["item_order"], child, request_id
                    )
                present_likes = int("like_count" in post)
                present_comments = int("comments_count" in post)
                self.db.execute(
                    "INSERT INTO raw_post_observations VALUES (?,?,?,?,?,?,?)",
                    (pid, request_id, observed, post.get("like_count"),
                     post.get("comments_count"), present_likes, present_comments)
                )
            self.db.execute(
                "UPDATE page_receipts SET state='committed' WHERE request_id=?",
                (request_id,)
            )
            self.db.execute("COMMIT")
            return {"status": "committed", "post_count": len(posts)}
        except BaseException as exc:
            self.db.execute("ROLLBACK")
            if isinstance(exc, OfflineRawMirrorError):
                raise
            raise OfflineRawMirrorError("Isolated mirror transaction failed") from None

    def reconcile_pending(self):
        """Promote stored pages without fetching, reanalysing or token access."""
        receipts = [r[0] for r in self.db.execute(
            "SELECT request_id FROM page_receipts WHERE state='staged' "
            "ORDER BY request_id"
        )]
        return [self.promote_page(receipt) for receipt in receipts]

    def summary(self):
        counts = {}
        for table in ("page_receipts", "raw_posts", "raw_post_media",
                      "raw_post_observations"):
            counts[table] = self.db.execute(
                "SELECT COUNT(*) FROM " + table
            ).fetchone()[0]
        counts["pending_pages"] = self.db.execute(
            "SELECT COUNT(*) FROM page_receipts WHERE state='staged'"
        ).fetchone()[0]
        return counts
