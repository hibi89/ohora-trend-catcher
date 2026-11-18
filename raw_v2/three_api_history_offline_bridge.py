"""Synthetic-only P3 history cursor -> P4 RAW mirror bridge.

Three durable phases: record normalized fixture in P3 epoch store, commit
isolated RAW mirror, then advance P3 history checkpoint. Reconcile from the
first phase after an interruption, without issuing any HTTP or new fixtures.

This is NOT the production capture contract, which explicitly holds history.
No credentials, network, timers, unattended execution, production writer or
operating database handles are accepted.
"""
from __future__ import annotations

from datetime import datetime
from hashlib import sha256
import json
import sqlite3

from three_api_epochs import ThreeApiEpochStore, EpochError, _cursor, _utc
from three_api_raw_mirror import (
    OfflineRawMirror, OfflineRawMirrorError, _normalize_post,
    _observation_time, _canonical,
)


class OfflineHistoryBridgeError(ValueError):
    """Sanitized exception; does not expose post IDs or opaque cursors."""


class OfflineHistoryRawBridge:
    def __init__(self, epoch_store: ThreeApiEpochStore, mirror: OfflineRawMirror):
        if not isinstance(epoch_store, ThreeApiEpochStore) or not isinstance(
                mirror, OfflineRawMirror):
            raise OfflineHistoryBridgeError("Isolated offline epoch and RAW mirror required")
        main_file = epoch_store.db.execute("PRAGMA database_list").fetchone()[2]
        if main_file and main_file == str(mirror.path):
            raise OfflineHistoryBridgeError("History and RAW require isolated stores")
        self.epochs = epoch_store
        self.mirror = mirror
        self.db = epoch_store.db
        self.db.execute("""CREATE TABLE IF NOT EXISTS offline_history_capture (
            job_id TEXT PRIMARY KEY REFERENCES jobs_v2(job_id),
            request_id TEXT NOT NULL UNIQUE,
            account_key TEXT NOT NULL,
            cursor_in TEXT,
            cursor_out TEXT,
            observed_at TEXT NOT NULL,
            terminal_basis TEXT NOT NULL,
            posts_json TEXT NOT NULL,
            source_digest TEXT NOT NULL
        )""")

    @staticmethod
    def _receipt(job_id):
        return "history." + job_id

    @staticmethod
    def _source(job_id):
        return "synthetic-history:" + job_id

    @staticmethod
    def _mirror_posts(normalized):
        """Reconstruct the RAW mirror's input-only child ID contract.

        Staged immutable source stores canonical children with media_id and
        item_order. Mirror input expects id and derives item_order from the
        list position. A round trip must reconstruct exactly the same source.
        """
        converted = []
        for post in normalized:
            item = {key: value for key, value in post.items()
                    if key != "children"}
            item["children"] = [
                {"id": child["media_id"],
                 **{key: value for key, value in child.items()
                    if key not in ("media_id", "item_order")}}
                for child in post.get("children", [])
            ]
            converted.append(item)
        return converted

    @staticmethod
    def _digest(job_id, request_id, account_key, cursor_in, cursor_out,
                observed_at, terminal_basis, posts_json):
        record = {
            "job_id": job_id, "request_id": request_id,
            "account_key": account_key, "cursor_in": cursor_in,
            "cursor_out": cursor_out, "observed_at": observed_at,
            "terminal_basis": terminal_basis, "posts": json.loads(posts_json),
        }
        return sha256(_canonical(record).encode("utf-8")).hexdigest()

    def stage_capture(self, job_id, request_id, *, observed_at, posts,
                      next_cursor, terminal_basis):
        """Durably retain one *supplied synthetic fixture*, before RAW writes.

        Staging never sends a request or advances an epoch cursor.
        A conflicting replay is rejected even after a committed page.
        """
        if not isinstance(observed_at, datetime):
            raise OfflineHistoryBridgeError("Capture requires timezone-aware time")
        try:
            stamp = _utc(observed_at)
            after = _cursor(next_cursor)
            if not isinstance(posts, list) or len(posts) > 4:
                raise OfflineHistoryBridgeError("Fixed history limit=4")
            clean = [_normalize_post(item) for item in posts]
            ids = [post["id"] for post in clean]
            if len(ids) != len(set(ids)):
                raise OfflineHistoryBridgeError("Duplicate history media ID")
            if terminal_basis not in ("continued", "verified_terminal"):
                raise OfflineHistoryBridgeError("Invalid history terminal basis")
            if ((terminal_basis == "continued" and after is None) or
                    (terminal_basis == "verified_terminal" and
                     (after is not None or len(clean) >= 4))):
                raise OfflineHistoryBridgeError("Unproven history terminal boundary")
            job = self.epochs._job_row(job_id)
            if job["epoch_kind"] != "history" or job["phase"] != "history" or (
                    job["state"] not in ("bound", "done")):
                raise OfflineHistoryBridgeError("Bound history page required")
            binding = self.db.execute(
                "SELECT request_id,requested_cursor FROM request_bindings_v2 "
                "WHERE job_id=?", (job_id,)
            ).fetchone()
            if binding is None or binding[0] != request_id:
                raise OfflineHistoryBridgeError("Immutable history binding mismatch")
            posts_json = _canonical(clean)
            digest = self._digest(
                job_id, request_id, job["account_key"], binding[1], after,
                stamp, terminal_basis, posts_json
            )
            proposed = (
                job_id, request_id, job["account_key"], binding[1], after,
                stamp, terminal_basis, posts_json, digest
            )
            self.db.execute("BEGIN IMMEDIATE")
            try:
                previous = self.db.execute(
                    "SELECT job_id,request_id,account_key,cursor_in,cursor_out,"
                    "observed_at,terminal_basis,posts_json,source_digest "
                    "FROM offline_history_capture WHERE job_id=?", (job_id,)
                ).fetchone()
                if previous is None:
                    if job["state"] != "bound":
                        raise OfflineHistoryBridgeError("Committed page cannot be newly staged")
                    self.db.execute(
                        "INSERT INTO offline_history_capture VALUES (?,?,?,?,?,?,?,?,?)",
                        proposed
                    )
                    status = "staged"
                elif tuple(previous) != proposed:
                    raise OfflineHistoryBridgeError("Conflicting immutable history capture")
                else:
                    status = "already_staged"
                self.db.execute("COMMIT")
            except BaseException:
                self.db.execute("ROLLBACK")
                raise
            return {"status": status, "job_id": job_id,
                    "history_dispatch_enabled": False}
        except (EpochError, OfflineRawMirrorError, sqlite3.Error, TypeError,
                ValueError) as exc:
            if isinstance(exc, OfflineHistoryBridgeError):
                raise
            raise OfflineHistoryBridgeError("Invalid offline history capture") from None

    def reconcile_one(self, job_id):
        """Mirror-first replay, then cursor commit; never refetch or invent time."""
        row = self.db.execute(
            "SELECT job_id,request_id,account_key,cursor_in,cursor_out,"
            "observed_at,terminal_basis,posts_json,source_digest "
            "FROM offline_history_capture WHERE job_id=?", (job_id,)
        ).fetchone()
        if row is None:
            raise OfflineHistoryBridgeError("No durable history capture to replay")
        (saved_job, request_id, account_key, cursor_in, cursor_out,
         observed_at, terminal, posts_json, source_digest) = tuple(row)
        try:
            job = self.epochs._job_row(job_id)
            binding = self.db.execute(
                "SELECT request_id,requested_cursor FROM request_bindings_v2 "
                "WHERE job_id=?", (job_id,)
            ).fetchone()
            if (saved_job != job_id or job["phase"] != "history" or
                    job["epoch_kind"] != "history" or
                    job["account_key"] != account_key or
                    job["state"] not in ("bound", "done") or
                    binding is None or tuple(binding) != (request_id, cursor_in)):
                raise OfflineHistoryBridgeError("History source/binding mismatch")
            if source_digest != self._digest(
                    job_id, request_id, account_key, cursor_in, cursor_out,
                    observed_at, terminal, posts_json):
                raise OfflineHistoryBridgeError("Immutable history source digest mismatch")
            posts = json.loads(posts_json)
            if not isinstance(posts, list) or len(posts) > 4:
                raise OfflineHistoryBridgeError("Invalid durable history page")
            # Canonical round trip rejects ambiguous, altered, or extra data.
            mirror_input = self._mirror_posts(posts)
            if _canonical([_normalize_post(p) for p in mirror_input]) != posts_json:
                raise OfflineHistoryBridgeError("Durable history payload mismatch")
            receipt_id = self._receipt(job_id)
            self.mirror.stage_page(receipt_id, account_key, observed_at, mirror_input)
            self.mirror.promote_page(receipt_id)
            receipt = self.mirror.db.execute(
                "SELECT account_key,observed_at,payload_json,payload_sha256,state "
                "FROM page_receipts WHERE request_id=?", (receipt_id,)
            ).fetchone()
            utc_observed = _observation_time(observed_at)
            if (receipt is None or receipt["account_key"] != account_key or
                    receipt["observed_at"] != utc_observed or
                    receipt["payload_json"] != posts_json or
                    receipt["payload_sha256"] != sha256(posts_json.encode("utf-8")).hexdigest() or
                    receipt["state"] != "committed"):
                raise OfflineHistoryBridgeError("RAW receipt not durably verified")
            observed = self.mirror.db.execute(
                "SELECT post_id,observed_at,like_count,comments_count,"
                "like_present,comments_present FROM raw_post_observations "
                "WHERE request_id=?", (receipt_id,)
            ).fetchall()
            expected = {p["id"]: p for p in posts}
            if len(observed) != len(expected) or {
                    r["post_id"] for r in observed} != set(expected):
                raise OfflineHistoryBridgeError("RAW history observations incomplete")
            for item in observed:
                p = expected[item["post_id"]]
                if (item["observed_at"] != utc_observed or
                        item["like_count"] != p.get("like_count") or
                        item["comments_count"] != p.get("comments_count") or
                        item["like_present"] != int("like_count" in p) or
                        item["comments_present"] != int("comments_count" in p)):
                    raise OfflineHistoryBridgeError("RAW history observations changed")
                owner = self.mirror.db.execute(
                    "SELECT account_key FROM raw_posts WHERE post_id=?",
                    (item["post_id"],)
                ).fetchone()
                if owner is None or owner["account_key"] != account_key:
                    raise OfflineHistoryBridgeError("RAW history post identity mismatch")
                media = self.mirror.db.execute(
                    "SELECT media_id,account_key,media_role,item_order "
                    "FROM raw_post_media WHERE post_id=?", (item["post_id"],)
                ).fetchall()
                identity = {(item["post_id"],account_key,"parent",0)}
                identity.update((c["media_id"],account_key,"child",c["item_order"])
                                for c in p["children"])
                if len(media) != len(identity) or {
                        tuple(v) for v in media} != identity:
                    raise OfflineHistoryBridgeError("RAW history media identity mismatch")
            return self.epochs.commit_history_page(
                job_id, request_id, observed_at=datetime.fromisoformat(
                    observed_at.replace("Z", "+00:00")),
                ordered_ids=[p["id"] for p in posts],
                next_cursor=cursor_out, terminal_basis=terminal,
                source_evidence_id=self._source(job_id),
                raw_receipt_id=receipt_id, source_digest=source_digest
            )
        except (EpochError, OfflineRawMirrorError, sqlite3.Error, TypeError,
                ValueError, KeyError):
            raise OfflineHistoryBridgeError("Offline history reconciliation blocked") from None

    def reconcile_pending(self):
        """Explicitly replay all durable history fixtures in stable order."""
        rows = self.db.execute(
            "SELECT job_id FROM offline_history_capture ORDER BY rowid"
        ).fetchall()
        return [self.reconcile_one(row[0]) for row in rows]
