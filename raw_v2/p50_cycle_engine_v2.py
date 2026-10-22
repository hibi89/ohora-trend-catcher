"""OHORA P50 v2: independent, offline-safe cycle planner.

No Meta requests or credentials in this module. The caller supplies a verified
fetcher and must durably persist RAW before acknowledge(). A fetcher result
must include posts, next_cursor, exhausted, and oldest_published_at.
"""
from __future__ import annotations

from datetime import datetime, timezone, timedelta
from pathlib import Path
import sqlite3

LANES = ("A", "B", "C")
ACCOUNTS = 50
MAX_CYCLES = 20
PAGE_LIMIT = 8
MAX_USAGE = 85


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
          lane TEXT PRIMARY KEY, usage INTEGER, halted INTEGER NOT NULL DEFAULT 0);
        """)
        for lane in LANES:
            self.db.execute("INSERT OR IGNORE INTO lanes(lane) VALUES (?)", (lane,))
        for ordinal in range(1, ACCOUNTS + 1):
            self.db.execute("INSERT OR IGNORE INTO accounts(ordinal) VALUES (?)", (ordinal,))
        self.cutoff = (start_at or datetime.now(timezone.utc)) - timedelta(days=365)

    def set_usage(self, lane, usage):
        if lane not in LANES or (usage is not None and
                                  (type(usage) is not int or not 0 <= usage <= 100)):
            raise ValueError("Invalid lane or usage")
        self.db.execute("UPDATE lanes SET usage=?,halted=? WHERE lane=?",
                        (usage, int(usage is None or usage >= MAX_USAGE), lane))

    def next_job(self):
        """A->B->C within each cycle; unfinished accounts only.

        An unavailable lane is bypassed, not falsely marked completed.
        A cycle is not advanced until every lane's accounts are settled.
        """
        self.db.execute("BEGIN IMMEDIATE")
        try:
            if self.db.execute("SELECT 1 FROM jobs WHERE state='reserved' LIMIT 1").fetchone():
                self.db.execute("COMMIT")
                return None
            for cycle in range(1, MAX_CYCLES + 1):
                for lane in LANES:
                    pending = self.db.execute("""
                      SELECT a.ordinal,a.cursor FROM accounts a
                      WHERE a.finished=0 AND NOT EXISTS(
                        SELECT 1 FROM jobs j
                        WHERE j.cycle=? AND j.lane=? AND j.ordinal=a.ordinal)
                      ORDER BY a.ordinal LIMIT 1""", (cycle, lane)).fetchone()
                    if pending is None:
                        continue
                    gate = self.db.execute("SELECT halted FROM lanes WHERE lane=?", (lane,)).fetchone()
                    if gate["halted"]:
                        self.db.execute("COMMIT")
                        return None
                    self.db.execute("""
                      INSERT INTO jobs(cycle,lane,ordinal,state,cursor_before)
                      VALUES (?,?,?,'reserved',?)""",
                      (cycle,lane,pending["ordinal"],pending["cursor"]))
                    self.db.execute("COMMIT")
                    return {"cycle":cycle,"lane":lane,"ordinal":pending["ordinal"],
                            "cursor":pending["cursor"],"limit":PAGE_LIMIT}
            self.db.execute("COMMIT")
            return None
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
