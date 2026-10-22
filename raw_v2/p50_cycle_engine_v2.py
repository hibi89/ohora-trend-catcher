"""OHORA P50 v2: independent, offline-safe cycle planner.

No Meta requests or credentials in this module. The caller supplies a verified
fetcher and must durably persist RAW before acknowledge(). A fetcher result
must include posts, next_cursor, exhausted, and oldest_published_at.
"""
from __future__ import annotations

from datetime import datetime, timezone, timedelta
from pathlib import Path
import sqlite3
import math

LANES = ("A", "B", "C")
ACCOUNTS = 50
MAX_CYCLES = 20
PAGE_LIMIT = 8
MAX_USAGE = 85
COOLDOWN_MINUTES = 63


def iso(dt):
    if not isinstance(dt, datetime) or dt.tzinfo is None:
        raise ValueError("timezone-aware datetime required")
    return dt.astimezone(timezone.utc).isoformat()


class CyclePlanner:
    def __init__(self, path, *, start_at=None):
        path = Path(path)
        if path.name == "coordinator.sqlite" or path.name == "p50_raw.sqlite":
            raise ValueError("Use a dedicated v2 planner database")
        path.parent.mkdir(parents=True, exist_ok=True)
        self.db = sqlite3.connect(path, isolation_level=None)
        self.db.row_factory = sqlite3.Row
        self.db.executescript("""
        CREATE TABLE IF NOT EXISTS accounts(
          ordinal INTEGER PRIMARY KEY CHECK(ordinal BETWEEN 1 AND 50),
          cursor TEXT, finished INTEGER NOT NULL DEFAULT 0,
          finished_reason TEXT);
        CREATE TABLE IF NOT EXISTS jobs(
          cycle INTEGER NOT NULL CHECK(cycle BETWEEN 1 AND 20),
          lane TEXT NOT NULL CHECK(lane IN ('A','B','C')),
          ordinal INTEGER NOT NULL CHECK(ordinal BETWEEN 1 AND 50),
          state TEXT NOT NULL DEFAULT 'pending'
            CHECK(state IN ('pending','reserved','done','skipped')),
          cursor_before TEXT, cursor_after TEXT,
          receipt_id TEXT, post_count INTEGER,
          PRIMARY KEY(cycle,lane,ordinal));
        CREATE TABLE IF NOT EXISTS lanes(
          lane TEXT PRIMARY KEY, usage REAL, halted INTEGER NOT NULL DEFAULT 0,
          ready_at TEXT);
        """)
        columns = {row[1] for row in self.db.execute('PRAGMA table_info(lanes)')}
        if 'ready_at' not in columns:
            self.db.execute('ALTER TABLE lanes ADD COLUMN ready_at TEXT')
        for lane in LANES:
            self.db.execute("INSERT OR IGNORE INTO lanes(lane) VALUES (?)", (lane,))
        for ordinal in range(1, ACCOUNTS + 1):
            self.db.execute("INSERT OR IGNORE INTO accounts(ordinal) VALUES (?)", (ordinal,))
        self.cutoff = (start_at or datetime.now(timezone.utc)) - timedelta(days=365)

    def set_usage(self, lane, usage, *, now=None):
        if lane not in LANES or (usage is not None and
                                  (type(usage) not in (int, float) or not math.isfinite(usage) or not 0 <= usage <= 100)):
            raise ValueError("Invalid lane or usage")
        halted = usage is None or usage >= MAX_USAGE
        until = iso((now or datetime.now(timezone.utc)) + timedelta(minutes=COOLDOWN_MINUTES)) if halted else None
        self.db.execute("UPDATE lanes SET usage=?,halted=?,ready_at=? WHERE lane=?",
                        (usage, int(halted), until, lane))

    def resume_cooled_lanes(self, *, now=None):
        # Cooldown expiry is not evidence of quota recovery.
        # Only a verified probe may call set_usage() to release a lane.
        return 0

    def cooldown_until(self):
        rows = self.db.execute('SELECT halted,ready_at FROM lanes').fetchall()
        if len(rows)!=3 or not all(row['halted'] for row in rows):
            return None
        dates = [datetime.fromisoformat(row['ready_at']) for row in rows if row['ready_at']]
        return max(dates) if len(dates)==3 else None

    def reconcile_saved_receipts(self, raw):
        # Only reconcile pages already durably saved; never replay unknown HTTP.
        rows = self.db.execute("SELECT cycle,lane,ordinal,cursor_before FROM jobs WHERE state='reserved'").fetchall()
        recovered = 0
        for row in rows:
            receipt = raw.execute('SELECT response_json FROM pages WHERE cycle=? AND lane=? AND ordinal=?',
                (row['cycle'],row['lane'],row['ordinal'])).fetchone()
            if receipt is None:
                continue
            body = __import__('json').loads(receipt[0]); media = body.get('media',{})
            posts = media.get('data',[]); paging = media.get('paging') or {}
            cursor = (paging.get('cursors') or {}).get('after')
            dates = [datetime.fromisoformat(p['timestamp'].replace('Z','+00:00')) for p in posts if p.get('timestamp')]
            self.acknowledge(dict(row),receipt_id=f"p50v2-{row['cycle']}-{row['lane']}-{row['ordinal']}",
                posts=posts,next_cursor=cursor,exhausted=cursor is None,
                oldest_published_at=min(dates) if dates else None)
            recovered += 1
        return recovered

    def next_job(self):
        """One page per account per sweep; retain current lane until quota halt.

        Existing job receipts are immutable. Choose the unfinished account
        with the fewest completed pages, breaking ties by account ordinal.
        Continue the last successful lane until it is unavailable; then
        switch to the next available lane in A/B/C order.
        """
        self.db.execute("BEGIN IMMEDIATE")
        try:
            if self.db.execute("SELECT 1 FROM jobs WHERE state='reserved' LIMIT 1").fetchone():
                self.db.execute("COMMIT")
                return None
            lanes = {r["lane"]:r["halted"] for r in self.db.execute(
                "SELECT lane,halted FROM lanes")}
            last = self.db.execute(
                "SELECT lane FROM jobs WHERE state='done' ORDER BY rowid DESC LIMIT 1"
            ).fetchone()
            current = last["lane"] if last else "A"
            start = LANES.index(current)
            lane = next((LANES[(start+i)%len(LANES)] for i in range(len(LANES))
                         if not lanes[LANES[(start+i)%len(LANES)]]), None)
            if lane is None:
                self.db.execute("COMMIT")
                return None
            account = self.db.execute("""
                SELECT a.ordinal,a.cursor,COALESCE(SUM(j.post_count),0) AS posts,COUNT(j.ordinal) AS pages
                FROM accounts a
                LEFT JOIN jobs j ON j.ordinal=a.ordinal AND j.state='done'
                WHERE a.finished=0
                GROUP BY a.ordinal,a.cursor
                HAVING COUNT(j.ordinal) < ?
                ORDER BY posts ASC,a.ordinal ASC LIMIT 1
            """,(MAX_CYCLES,)).fetchone()
            if account is None:
                self.db.execute("COMMIT")
                return None
            cycle=self.db.execute("""
                SELECT COALESCE(MAX(cycle),0)+1 FROM jobs
                WHERE lane=? AND ordinal=?
            """,(lane,account["ordinal"])).fetchone()[0]
            if cycle > MAX_CYCLES:
                self.db.execute("COMMIT")
                return None
            self.db.execute("""
                INSERT INTO jobs(cycle,lane,ordinal,state,cursor_before)
                VALUES (?,?,?,'reserved',?)
            """,(cycle,lane,account["ordinal"],account["cursor"]))
            self.db.execute("COMMIT")
            return {"cycle":cycle,"lane":lane,"ordinal":account["ordinal"],
                    "cursor":account["cursor"],"limit":PAGE_LIMIT - (account["posts"] % PAGE_LIMIT)}
        except BaseException:
            self.db.execute("ROLLBACK")
            raise

    def acknowledge(self, job, *, receipt_id, posts, next_cursor,
                    exhausted=False, oldest_published_at=None):
        """Advance only after caller confirms a durable, unique RAW receipt."""
        if not isinstance(receipt_id, str) or not receipt_id.strip():
            raise ValueError("Durable RAW receipt required")
        if not isinstance(posts, (list, tuple)) or len(posts) > PAGE_LIMIT:
            raise ValueError("Invalid page size")
        if not isinstance(exhausted, bool):
            raise ValueError("Invalid exhaustion flag")
        if oldest_published_at is not None and (
            not isinstance(oldest_published_at, datetime) or
            oldest_published_at.tzinfo is None):
            raise ValueError("Timestamp required")
        if not exhausted and next_cursor is None:
            raise ValueError("Missing next cursor; cannot continue safely")
        if next_cursor is not None and (not isinstance(next_cursor, str) or not next_cursor):
            raise ValueError("Invalid next cursor")
        finished = exhausted or (oldest_published_at is not None and
                                  oldest_published_at < self.cutoff)
        reason = "exhausted" if exhausted else "one_year_cutoff" if finished else None
        self.db.execute("BEGIN IMMEDIATE")
        try:
            row = self.db.execute("""
                SELECT state,cursor_before FROM jobs
                WHERE cycle=? AND lane=? AND ordinal=?""",
                (job["cycle"],job["lane"],job["ordinal"])).fetchone()
            account = self.db.execute("SELECT cursor FROM accounts WHERE ordinal=?",
                                      (job["ordinal"],)).fetchone()
            if row is None or row["state"] != "reserved" or account["cursor"] != row["cursor_before"]:
                raise ValueError("Job not reserved or cursor changed")
            self.db.execute("""
                UPDATE jobs SET state='done',cursor_after=?,receipt_id=?,post_count=?
                WHERE cycle=? AND lane=? AND ordinal=?""",
                (next_cursor,receipt_id,len(posts),job["cycle"],job["lane"],job["ordinal"]))
            self.db.execute("""
                UPDATE accounts SET cursor=?,finished=?,finished_reason=?
                WHERE ordinal=?""",
                (next_cursor,int(finished),reason,job["ordinal"]))
            self.db.execute("COMMIT")
        except BaseException:
            self.db.execute("ROLLBACK")
            raise

    def summary(self):
        return {
            "completed_requests":self.db.execute(
                "SELECT COUNT(*) FROM jobs WHERE state='done'").fetchone()[0],
            "finished_accounts":self.db.execute(
                "SELECT COUNT(*) FROM accounts WHERE finished=1").fetchone()[0],
            "reserved_requests":self.db.execute(
                "SELECT COUNT(*) FROM jobs WHERE state='reserved'").fetchone()[0],
            "lanes": [dict(r) for r in self.db.execute("SELECT * FROM lanes ORDER BY lane")]
        }
