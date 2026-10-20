"""Offline KST daily-front and durable catch-up continuity ledger.

This is an isolated offline policy ledger. DailyEvidenceProjector can
project completed, ordered three-API staging evidence into it, but there is
no live Meta connector, background scheduler, or RAW production DB writer.
It stores IDs, cursor edges and audit state in a separate SQLite file.
Live integration and real post persistence still require explicit approval.

Daily front: at most three pages x four posts = twelve per account.
If the prior latest post ID was not reached, preserve its anchor and next
cursor as a catch-up task. Verify the gap only upon seeing that exact anchor.
New daily heads never overwrite older unresolved catch-up sessions.
"""
from __future__ import annotations

from datetime import date, datetime, timedelta, timezone
import json
from pathlib import Path
import re
import sqlite3

KST = timezone(timedelta(hours=9))
FRONT_PAGES = 3
PAGE_SIZE = 4
_ACCOUNT = re.compile(r"[A-Za-z0-9_:-]{1,128}\Z")
_POST_ID = re.compile(r"[0-9]{1,64}\Z")
_CURSOR = re.compile(r"[A-Za-z0-9_=-]{1,512}\Z")


class GapEvidenceError(ValueError):
    """Never includes cursor or other potentially sensitive original input."""


def day_kst(value=None):
    """Resolve day from a timezone-aware instant, never the host local zone."""
    value = value or datetime.now(timezone.utc)
    if not isinstance(value, datetime) or value.tzinfo is None:
        raise GapEvidenceError("Timezone-aware instant required")
    return value.astimezone(KST).date().isoformat()


def _day(value):
    if not isinstance(value, str):
        raise GapEvidenceError("Invalid KST date")
    if len(value) != 10:
        raise GapEvidenceError("Invalid KST date")
    try:
        return date.fromisoformat(value).isoformat()
    except ValueError:
        raise GapEvidenceError("Invalid KST date") from None


def _cursor(value):
    if value is not None and (not isinstance(value, str) or not _CURSOR.fullmatch(value)):
        raise GapEvidenceError("Invalid paging cursor")
    return value


def _post(value):
    if not isinstance(value, str) or not _POST_ID.fullmatch(value):
        raise GapEvidenceError("Invalid media ID")
    return value


def _posts(values):
    if not isinstance(values, list) or len(values) > PAGE_SIZE:
        raise GapEvidenceError("Invalid four-item media page")
    posts = [_post(v) for v in values]
    if len(set(posts)) != len(posts):
        raise GapEvidenceError("Duplicate ID within page")
    return posts


class DailyGapLedger:
    """One isolated SQLite ledger; never opens a RAW collection database."""

    def __init__(self, path):
        self.path = Path(path)
        if self.path.name.lower() == "collection.sqlite":
            raise GapEvidenceError("Production database path is forbidden")
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.db = sqlite3.connect(self.path, isolation_level=None)
        self.db.row_factory = sqlite3.Row
        self.db.execute("PRAGMA busy_timeout=30000")
        self.db.executescript("""
            CREATE TABLE IF NOT EXISTS account_head (
              account_key TEXT PRIMARY KEY, head_id TEXT NOT NULL,
              observed_day TEXT NOT NULL);
            CREATE TABLE IF NOT EXISTS daily_window (
              account_key TEXT NOT NULL, day TEXT NOT NULL,
              anchor_id TEXT, head_id TEXT, front_pages INTEGER NOT NULL DEFAULT 0,
              front_next TEXT, catch_next TEXT,
              catch_pages INTEGER NOT NULL DEFAULT 0,
              status TEXT NOT NULL DEFAULT 'front_open'
                CHECK(status IN ('front_open','catchup_pending','verified',
                                 'needs_review','bootstrap_history')),
              PRIMARY KEY(account_key,day));
            CREATE INDEX IF NOT EXISTS idx_daily_window_account_status_day
              ON daily_window(account_key,status,day);
            CREATE TABLE IF NOT EXISTS page_proof (
              account_key TEXT NOT NULL, day TEXT NOT NULL,
              phase TEXT NOT NULL CHECK(phase IN ('front','catchup')),
              page_no INTEGER NOT NULL,
              requested_cursor TEXT, next_cursor TEXT,
              media_ids_json TEXT NOT NULL, anchor_reached INTEGER NOT NULL,
              PRIMARY KEY(account_key,day,phase,page_no));
        """)

    def close(self):
        self.db.close()

    def seed_previous_head(self, account_key, head_id, *, day="2026-10-18"):
        """Synthetic baseline helper; refuses overwriting an existing head."""
        self._account(account_key)
        _post(head_id)
        day = _day(day)
        with self.db:
            self.db.execute("INSERT INTO account_head VALUES (?,?,?)",
                            (account_key, head_id, day))

    def _account(self, account_key):
        if not isinstance(account_key, str) or not _ACCOUNT.fullmatch(account_key):
            raise GapEvidenceError("Invalid account key")

    def begin_day(self, account_key, day):
        self._account(account_key)
        day = _day(day)
        with self.db:
            previous = self.db.execute(
                "SELECT head_id,observed_day FROM account_head WHERE account_key=?",
                (account_key,)).fetchone()
            already=self.db.execute(
                "SELECT 1 FROM daily_window WHERE account_key=? AND day=?",
                (account_key,day)).fetchone() is not None
            if not already and previous and previous["observed_day"] > day:
                raise GapEvidenceError("Cannot create an unanchored earlier day")
            self.db.execute(
                "INSERT OR IGNORE INTO daily_window(account_key,day,anchor_id) VALUES (?,?,?)",
                (account_key, day, previous["head_id"] if previous else None))
        return self.status(account_key, day)

    def status(self, account_key, day):
        self._account(account_key)
        day = _day(day)
        row = self.db.execute(
            "SELECT * FROM daily_window WHERE account_key=? AND day=?",
            (account_key,day)).fetchone()
        if row is None:
            return None
        return {"account_key": account_key, "day": day, "status": row["status"],
                "front_pages": row["front_pages"], "catchup_pages": row["catch_pages"],
                "anchor_present": row["anchor_id"] is not None,
                "front_cursor_available": row["front_next"] is not None,
                "catchup_cursor_available": row["catch_next"] is not None}

    def record_page(self, account_key, day, *, phase, requested_cursor=None,
                    post_ids, next_cursor=None):
        """Commit one page and its evidence atomically; no page replay writes.

        front: first 3 pages maximum. catchup: page 4 onwards until old head.
        post_ids are observed IDs in API response order, not estimated counts.
        """
        self._account(account_key)
        day = _day(day)
        if phase not in ("front", "catchup"):
            raise GapEvidenceError("Invalid capture phase")
        requested_cursor = _cursor(requested_cursor)
        next_cursor = _cursor(next_cursor)
        posts = _posts(post_ids)
        if next_cursor is not None and not posts:
            raise GapEvidenceError("Empty page cannot advance the cursor")
        if next_cursor and next_cursor == requested_cursor:
            raise GapEvidenceError("Unchanged cursor")
        self.db.execute("BEGIN IMMEDIATE")
        try:
            row = self.db.execute(
                "SELECT * FROM daily_window WHERE account_key=? AND day=?",
                (account_key,day)).fetchone()
            if row is None:
                raise GapEvidenceError("Daily window must be started first")
            if phase == "front":
                if row["status"] != "front_open" or row["front_pages"] >= FRONT_PAGES:
                    raise GapEvidenceError("Front window is already closed")
                expected = row["front_next"] if row["front_pages"] else None
                number = row["front_pages"] + 1
            else:
                if row["status"] != "catchup_pending":
                    raise GapEvidenceError("No unfinished catch-up gap")
                expected = row["catch_next"]
                number = row["catch_pages"] + 1
            if requested_cursor != expected:
                raise GapEvidenceError("Cursor does not match stored predecessor")
            prior_rows = list(self.db.execute(
                "SELECT media_ids_json,requested_cursor,next_cursor FROM page_proof WHERE account_key=? AND day=?",
                (account_key,day)))
            prior_ids = {pid for item in prior_rows for pid in json.loads(item["media_ids_json"])}
            if prior_ids.intersection(posts):
                raise GapEvidenceError("Repeated media ID across pages; resnapshot required")
            prior_cursors = {c for item in prior_rows for c in
                             (item["requested_cursor"],item["next_cursor"]) if c}
            if next_cursor and next_cursor in prior_cursors:
                raise GapEvidenceError("Paging cursor cycle; resnapshot required")
            anchor_reached = row["anchor_id"] in posts if row["anchor_id"] else False
            if phase == "front":
                if row["anchor_id"] is None:
                    new_status = "bootstrap_history" if number == FRONT_PAGES or not next_cursor else "front_open"
                elif anchor_reached:
                    new_status = "verified"
                elif not next_cursor:
                    new_status = "needs_review"
                elif number == FRONT_PAGES:
                    new_status = "catchup_pending"
                else:
                    new_status = "front_open"
                self.db.execute(
                    "UPDATE daily_window SET head_id=COALESCE(head_id,?), front_pages=?, "
                    "front_next=?,catch_next=?,status=? WHERE account_key=? AND day=?",
                    (posts[0] if posts else None,number,next_cursor,
                     next_cursor if new_status == "catchup_pending" else None,
                     new_status,account_key,day))
                if number == 1 and posts:
                    latest = self.db.execute(
                        "SELECT observed_day FROM account_head WHERE account_key=?",
                        (account_key,)).fetchone()
                    if latest is None or latest["observed_day"] <= day:
                        self.db.execute(
                            "INSERT INTO account_head VALUES (?,?,?) "
                            "ON CONFLICT(account_key) DO UPDATE SET head_id=excluded.head_id,"
                            " observed_day=excluded.observed_day",
                            (account_key,posts[0],day))
            else:
                new_status = ("verified" if anchor_reached else
                              "catchup_pending" if next_cursor else "needs_review")
                self.db.execute(
                    "UPDATE daily_window SET catch_pages=?,catch_next=?,status=? "
                    "WHERE account_key=? AND day=?",
                    (number,next_cursor if new_status == "catchup_pending" else None,
                     new_status,account_key,day))
            self.db.execute(
                "INSERT INTO page_proof VALUES (?,?,?,?,?,?,?,?)",
                (account_key,day,phase,number,requested_cursor,next_cursor,
                 json.dumps(posts,separators=(",", ":")),int(anchor_reached)))
            self.db.execute("COMMIT")
        except BaseException:
            self.db.execute("ROLLBACK")
            raise
        return self.status(account_key,day)

    def _pending_gap_action(self, account, gap_day):
        """Never re-use a cursor after a manual replay-from-head has begun."""
        table=self.db.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' "
            "AND name='reanchor_attempt'"
        ).fetchone()
        state=None
        if table is not None:
            row=self.db.execute(
                "SELECT state FROM reanchor_attempt "
                "WHERE account_key=? AND gap_day=? "
                "ORDER BY replay_day DESC LIMIT 1",
                (account,gap_day)).fetchone()
            state=row["state"] if row is not None else None
        phase=("reanchor" if state=="active" else
               "needs_review" if state in ("needs_review","verified") else "catchup")
        return {"account_key":account,"phase":phase,"gap_day":gap_day}

    def next_actions(self, account_keys, day):
        """Global policy: all unfinished fronts, then oldest gap, then history.

        This returns decisions only; NEVER runs, schedules or calls Meta.
        An unresolved needs_review window prohibits history for that account.
        """
        day = _day(day)
        accounts = list(account_keys)
        if len(accounts) != len(set(accounts)):
            raise GapEvidenceError("Duplicate roster account")
        for account in accounts:
            self._account(account)
        fronts=[];catchups=[];history=[];reviews=[]
        for account in accounts:
            s = self.db.execute(
                "SELECT status,front_next,catch_next,day FROM daily_window "
                "WHERE account_key=? AND day=?",(account,day)).fetchone()
            if s is None or s["status"] == "front_open":
                fronts.append({"account_key":account,"phase":"front"})
                continue
            unfinished = list(self.db.execute(
                "SELECT status,day FROM daily_window WHERE account_key=? "
                "AND day<=? AND status IN ('catchup_pending','needs_review') "
                "ORDER BY day",(account,day)))
            old_front=self.db.execute(
                "SELECT day FROM daily_window WHERE account_key=? AND day<? "
                "AND status='front_open' ORDER BY day LIMIT 1",
                (account,day)).fetchone()
            if old_front is not None:
                reviews.append({"account_key":account,"phase":"needs_review",
                                "gap_day":old_front["day"]})
                continue
            if unfinished:
                blocked = next((x for x in unfinished if x["status"] == "needs_review"),None)
                if blocked:
                    reviews.append({"account_key":account,"phase":"needs_review",
                                    "gap_day":blocked["day"]})
                else:
                    catchups.append(self._pending_gap_action(account, unfinished[0]["day"]))
            elif s["status"] == "needs_review":
                reviews.append({"account_key":account,"phase":"needs_review","gap_day":day})
            elif s["status"] == "bootstrap_history":
                history.append({"account_key":account,"phase":"history_unverified"})
            else:
                history.append({"account_key":account,"phase":"history"})
        if fronts:
            return fronts
        if catchups:
            return reviews + catchups
        if reviews:
            return reviews
        return history
