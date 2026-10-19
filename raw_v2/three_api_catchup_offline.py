"""Synthetic-only page-4+ catch-up executor for the isolated daily ledger.

No tokens, Meta transport, automatic scheduler, or RAW collection.sqlite access.
An inbox proof is staged before the ledger cursor is advanced. On interruption
reconcile_pending() replays the durable inbox, without fetching another page.
New fixture captures preserve allowlisted payload, digest and observed_at
atomically beside ID proof. Legacy ID-only pages remain unprojectable.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
import hashlib
import json
from pathlib import Path
import sqlite3
import uuid

from three_api_coordinator import Coordinator, LANES, LEASE_MINUTES, stamp, utcnow
from three_api_daily_gap import DailyGapLedger, GapEvidenceError, _day, _posts, _cursor
from three_api_meta_normalizer import normalize_meta_page


class OfflineCatchupError(ValueError):
    """Sanitized errors; never include tokens, IDs, captions or cursors."""



def _sealed_payload(account, day, number, cursor, next_cursor, ids, posts, observed_at):
    return json.dumps(
        {"account_key": account, "gap_day": day, "page_no": number,
         "requested_cursor": cursor, "next_cursor": next_cursor,
         "ids": ids, "posts": posts, "observed_at":observed_at},
        ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False,
    )


def verified_catchup_payload(row):
    """Validate versioned durable post payload against the inbox ID/cursor edge."""
    if (row["payload_version"] != 1 or not row["payload_json"]
            or not row["payload_sha256"] or not row["observed_at"]):
        raise OfflineCatchupError("Unprojectable legacy or incomplete catch-up payload")
    if hashlib.sha256(row["payload_json"].encode("utf-8")).hexdigest() != row["payload_sha256"]:
        raise OfflineCatchupError("Catch-up payload seal mismatch")
    try:
        envelope = json.loads(row["payload_json"])
        posts = envelope["posts"]
        ids = json.loads(row["ids_json"])
        if (not isinstance(posts, list) or len(posts) > 4
                or not all(isinstance(p, dict) and isinstance(p.get("id"), str) for p in posts)
                or ids != [p["id"] for p in posts]
                or envelope != {
                    "account_key": row["account_key"], "gap_day": row["gap_day"],
                    "page_no": row["page_no"],
                    "requested_cursor": row["requested_cursor"],
                    "next_cursor": row["next_cursor"], "ids": ids, "posts": posts,
                    "observed_at":row["observed_at"]
                }
                or _sealed_payload(row["account_key"], row["gap_day"], row["page_no"],
                    row["requested_cursor"], row["next_cursor"], ids, posts,
                    row["observed_at"])
                   != row["payload_json"]):
            raise OfflineCatchupError("Staged payload differs from ID/cursor evidence")
    except (ValueError, TypeError, KeyError):
        raise OfflineCatchupError("Invalid staged catch-up payload") from None
    return posts


class OfflineCatchupRunner:
    """A deliberately offline and fixture-only bridge for catch-up pages."""

    def __init__(self, ledger: DailyGapLedger, coordinator: Coordinator,
                 account_keys, fixture_pages: dict):
        if not isinstance(ledger, DailyGapLedger) or not isinstance(coordinator, Coordinator):
            raise OfflineCatchupError("Isolated ledger and coordinator required")
        if (Path(coordinator.path).resolve() == ledger.path.resolve()
                or Path(coordinator.path).name.lower() == "collection.sqlite"):
            raise OfflineCatchupError("Source and ledger must be isolated from production")
        if type(fixture_pages) is not dict:
            raise OfflineCatchupError("Only an in-memory fixture dict is permitted")
        accounts = list(account_keys)
        if not accounts or len(accounts) != len(set(accounts)):
            raise OfflineCatchupError("Duplicate or empty account roster")
        for account in accounts:
            ledger._account(account)
        self.ledger = ledger
        self.coordinator = coordinator
        self.accounts = tuple(accounts)
        self._fixtures = fixture_pages
        self.db = ledger.db
        self.db.executescript("""
          CREATE TABLE IF NOT EXISTS catchup_inbox (
            account_key TEXT NOT NULL, gap_day TEXT NOT NULL,
            page_no INTEGER NOT NULL, lane TEXT NOT NULL,
            requested_cursor TEXT, next_cursor TEXT,
            ids_json TEXT NOT NULL, applied INTEGER NOT NULL DEFAULT 0,
            payload_version INTEGER NOT NULL DEFAULT 0,
            payload_json TEXT, payload_sha256 TEXT, observed_at TEXT,
            PRIMARY KEY(account_key,gap_day,page_no)
          );
          CREATE TABLE IF NOT EXISTS catchup_lease (
            account_key TEXT NOT NULL, gap_day TEXT NOT NULL,
            lease_id TEXT NOT NULL, leased_until TEXT NOT NULL,
            PRIMARY KEY(account_key,gap_day)
          );
        """)
        # Add nullable payload columns only; legacy ID-only pages are never invented.
        cols = {row[1] for row in self.db.execute("PRAGMA table_info(catchup_inbox)")}
        for name, ddl in (
            ("payload_version", "INTEGER NOT NULL DEFAULT 0"),
            ("payload_json", "TEXT"),
            ("payload_sha256", "TEXT"),
            ("observed_at", "TEXT"),
        ):
            if name not in cols:
                self.db.execute("ALTER TABLE catchup_inbox ADD COLUMN " + name + " " + ddl)

    def _lane_ready(self, lane, now):
        row = self.coordinator.db.execute(
            "SELECT ready_at,halted FROM lanes WHERE lane=?", (lane,)
        ).fetchone()
        return row is not None and not row["halted"] and row["ready_at"] <= stamp(now)

    def _pending(self):
        return self.db.execute(
            "SELECT * FROM catchup_inbox WHERE applied=0 "
            "ORDER BY gap_day,account_key,page_no LIMIT 1"
        ).fetchone()

    def _apply(self, row):
        """Replay existing inbox idempotently, including after proof-before-ack crash."""
        account, day, number = row["account_key"], row["gap_day"], row["page_no"]
        ids = json.loads(row["ids_json"])
        if row["payload_version"] == 1:
            verified_catchup_payload(row)
        elif row["payload_version"] != 0:
            raise OfflineCatchupError("Unknown catch-up payload version")
        proof = self.db.execute(
            "SELECT requested_cursor,next_cursor,media_ids_json FROM page_proof "
            "WHERE account_key=? AND day=? AND phase='catchup' AND page_no=?",
            (account, day, number),
        ).fetchone()
        if proof is None:
            checkpoint = self.db.execute(
                "SELECT catch_pages,status,catch_next FROM daily_window "
                "WHERE account_key=? AND day=?", (account, day),
            ).fetchone()
            if (checkpoint is None or checkpoint["status"] != "catchup_pending"
                    or checkpoint["catch_pages"] != number - 1
                    or checkpoint["catch_next"] != row["requested_cursor"]):
                raise OfflineCatchupError("Staged page conflicts with gap checkpoint")
            try:
                self.ledger.record_page(
                    account, day, phase="catchup",
                    requested_cursor=row["requested_cursor"],
                    post_ids=ids, next_cursor=row["next_cursor"],
                )
            except GapEvidenceError:
                raise OfflineCatchupError("Staged page proof invalid") from None
        elif (proof["requested_cursor"] != row["requested_cursor"]
              or proof["next_cursor"] != row["next_cursor"]
              or json.loads(proof["media_ids_json"]) != ids):
            raise OfflineCatchupError("Committed proof differs from staged evidence")
        with self.db:
            self.db.execute(
                "UPDATE catchup_inbox SET applied=1 "
                "WHERE account_key=? AND gap_day=? AND page_no=?",
                (account, day, number),
            )
            self.db.execute(
                "DELETE FROM catchup_lease WHERE account_key=? AND gap_day=?",
                (account, day),
            )
        return {"status": "applied", "account_key": account,
                "gap_day": day, "page_no": number,
                "gap_status": self.ledger.status(account, day)["status"]}

    def reconcile_pending(self):
        """Apply one staged page without using any fixture or transport."""
        row = self._pending()
        return None if row is None else self._apply(row)

    def _lease(self, account, day, now):
        lease_id = uuid.uuid4().hex
        expiry = stamp(now + timedelta(minutes=LEASE_MINUTES))
        self.db.execute("BEGIN IMMEDIATE")
        try:
            current = self.db.execute(
                "SELECT leased_until FROM catchup_lease "
                "WHERE account_key=? AND gap_day=?", (account, day),
            ).fetchone()
            if current is not None and current["leased_until"] > stamp(now):
                self.db.execute("COMMIT")
                return None
            self.db.execute(
                "INSERT INTO catchup_lease VALUES (?,?,?,?) "
                "ON CONFLICT(account_key,gap_day) DO UPDATE SET "
                "lease_id=excluded.lease_id,leased_until=excluded.leased_until",
                (account, day, lease_id, expiry),
            )
            self.db.execute("COMMIT")
            return lease_id
        except BaseException:
            self.db.execute("ROLLBACK")
            raise

    def run_one(self, lane, today, *, now=None, stage_only=False):
        """Process at most one offline page; stage_only simulates interruption."""
        if lane not in LANES:
            raise OfflineCatchupError("Unknown lane")
        today = _day(today)
        now = utcnow() if now is None else now
        if not isinstance(now, datetime) or now.tzinfo is None:
            raise OfflineCatchupError("Timezone-aware clock required")
        now = now.astimezone(timezone.utc)
        recovered = self.reconcile_pending()
        if recovered is not None:
            return {**recovered, "status": "reconciled"}
        if not self._lane_ready(lane, now):
            return {"status": "lane_blocked", "lane": lane}
        actions = self.ledger.next_actions(self.accounts, today)
        if any(item["phase"] == "front" for item in actions):
            return {"status": "front_priority"}
        catchups = [item for item in actions if item["phase"] == "catchup"]
        if not catchups:
            return {"status": "no_catchup",
                    "review_required": any(item["phase"] in ("needs_review", "reanchor")
                                           for item in actions)}
        target = catchups[0]
        account, gap_day = target["account_key"], target["gap_day"]
        checkpoint = self.db.execute(
            "SELECT catch_next,catch_pages,status FROM daily_window "
            "WHERE account_key=? AND day=?", (account, gap_day)
        ).fetchone()
        if (checkpoint is None or checkpoint["status"] != "catchup_pending"
                or not checkpoint["catch_next"]):
            raise OfflineCatchupError("Missing catch-up cursor checkpoint")
        if checkpoint["catch_pages"] >= 250:
            return {"status": "manual_review_page_limit"}
        cursor, number = checkpoint["catch_next"], checkpoint["catch_pages"] + 1
        lease_id = self._lease(account, gap_day, now)
        if lease_id is None:
            return {"status": "lease_busy"}
        key = (account, gap_day, cursor)
        if key not in self._fixtures:
            with self.db:
                self.db.execute(
                    "DELETE FROM catchup_lease WHERE account_key=? AND gap_day=? AND lease_id=?",
                    (account, gap_day, lease_id))
            return {"status": "missing_offline_fixture"}
        try:
            normalized = normalize_meta_page(self._fixtures[key],
                                             requested_cursor=cursor)
            ids = [post["id"] for post in normalized["data"]]
            next_cursor = normalized.get("paging", {}).get("cursors", {}).get("after")
            _posts(ids)
            _cursor(next_cursor)
            self.db.execute("BEGIN IMMEDIATE")
            try:
                active = self.db.execute(
                    "SELECT lease_id FROM catchup_lease "
                    "WHERE account_key=? AND gap_day=?",
                    (account, gap_day)).fetchone()
                fresh = self.db.execute(
                    "SELECT catch_pages,catch_next,status FROM daily_window "
                    "WHERE account_key=? AND day=?", (account, gap_day)).fetchone()
                if (active is None or active["lease_id"] != lease_id or fresh is None
                        or fresh["status"] != "catchup_pending"
                        or fresh["catch_pages"] != number - 1
                        or fresh["catch_next"] != cursor):
                    raise OfflineCatchupError("Lease or cursor checkpoint changed")
                sealed = _sealed_payload(account, gap_day, number, cursor,
                                         next_cursor, ids, normalized["data"], now.isoformat())
                digest = hashlib.sha256(sealed.encode("utf-8")).hexdigest()
                self.db.execute(
                    """INSERT INTO catchup_inbox
                       (account_key,gap_day,page_no,lane,requested_cursor,next_cursor,
                        ids_json,applied,payload_version,payload_json,payload_sha256,observed_at)
                       VALUES (?,?,?,?,?,?,?,0,1,?,?,?)""",
                    (account,gap_day,number,lane,cursor,next_cursor,
                     json.dumps(ids,separators=(",",":")),sealed,digest,now.isoformat()))
                self.db.execute("COMMIT")
            except BaseException:
                self.db.execute("ROLLBACK")
                raise
        except (ValueError, TypeError, sqlite3.Error):
            with self.db:
                self.db.execute(
                    "DELETE FROM catchup_lease WHERE account_key=? AND gap_day=? AND lease_id=?",
                    (account, gap_day, lease_id))
            return {"status": "invalid_offline_fixture", "lane": lane}
        if stage_only:
            return {"status": "staged_unapplied", "page_no": number}
        return self.reconcile_pending()
