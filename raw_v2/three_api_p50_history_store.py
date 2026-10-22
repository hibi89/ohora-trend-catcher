"""Isolated P50 historical backfill state, persisted across calendar days.

This module never sends HTTP. An account can advance only after the caller
supplies verified RAW/Capture receipts for its immutable page. All mutations
are transactional in a dedicated p50/history.sqlite database.
"""
from __future__ import annotations

from datetime import date, datetime
from pathlib import Path
import sqlite3
import re

from three_api_p50_history_checkpoint import history_checkpoint

_KEY = re.compile(r"P50_(?:US|KR|JP)_[0-9]{3}\Z")


class P50HistoryStore:
    def __init__(self, path):
        self.path = Path(path)
        if "p50" not in {part.lower() for part in self.path.parts} or self.path.name != "history.sqlite":
            raise ValueError("Dedicated P50 history.sqlite required")
        if self.path.is_symlink() or self.path.parent.is_symlink():
            raise ValueError("Symlink history database forbidden")
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.db = sqlite3.connect(self.path, isolation_level=None, timeout=30)
        self.db.row_factory = sqlite3.Row
        self.db.execute("PRAGMA busy_timeout=30000")
        self.db.executescript("""
        CREATE TABLE IF NOT EXISTS history_accounts (
            account_key TEXT PRIMARY KEY,
            cutoff_day TEXT NOT NULL,
            cursor TEXT,
            state TEXT NOT NULL CHECK(state IN (
                'ready','continuation_ready','blocked_unknown_http',
                'blocked_unverified_page','blocked_missing_cursor',
                'blocked_repeated_cursor','window_covered','terminal_verified')),
            pages_verified INTEGER NOT NULL DEFAULT 0,
            last_capture_id TEXT,
            last_raw_receipt_id TEXT
        );
        CREATE TABLE IF NOT EXISTS history_receipts (
            capture_id TEXT PRIMARY KEY,
            account_key TEXT NOT NULL REFERENCES history_accounts(account_key),
            raw_receipt_id TEXT NOT NULL UNIQUE,
            state TEXT NOT NULL,
            next_cursor TEXT,
            committed_at TEXT NOT NULL
        );
        """)

    def close(self):
        self.db.close()

    def __enter__(self):
        return self

    def __exit__(self, *_):
        self.close()

    def initialize(self, account_keys, cutoff_day):
        if not isinstance(cutoff_day, date) or isinstance(cutoff_day, datetime):
            raise ValueError("History cutoff must be a date")
        keys = tuple(account_keys)
        if (len(keys) != 50 or len(set(keys)) != 50
                or any(not isinstance(k, str) or not _KEY.fullmatch(k) for k in keys)):
            raise ValueError("Exactly 50 distinct P50 account keys required")
        self.db.execute("BEGIN IMMEDIATE")
        try:
            existing = self.db.execute("SELECT account_key,cutoff_day FROM history_accounts").fetchall()
            if existing and (len(existing) != 50 or
                             {row["account_key"] for row in existing} != set(keys) or
                             {row["cutoff_day"] for row in existing} != {cutoff_day.isoformat()}):
                raise ValueError("History roster or cutoff mismatch; existing state preserved")
            for key in keys:
                self.db.execute(
                    "INSERT OR IGNORE INTO history_accounts(account_key,cutoff_day,state) VALUES(?,?,'ready')",
                    (key, cutoff_day.isoformat()))
            self.db.execute("COMMIT")
        except BaseException:
            self.db.execute("ROLLBACK")
            raise

    def read(self, account_key):
        row = self.db.execute(
            "SELECT * FROM history_accounts WHERE account_key=?", (account_key,)).fetchone()
        return dict(row) if row else None

    def commit_verified_page(self, *, account_key, expected_cursor, next_cursor,
                             oldest_post_at, terminal, capture_id, raw_receipt_id,
                             raw_receipt_verified, capture_verified,
                             uncertain_http=False):
        if not isinstance(capture_id, str) or not capture_id:
            raise ValueError("Capture identifier required")
        if not isinstance(raw_receipt_id, str) or not raw_receipt_id:
            raise ValueError("RAW receipt identifier required")
        if raw_receipt_verified is not True or capture_verified is not True or uncertain_http is True:
            raise ValueError("Verified RAW and Capture receipts required; uncertain HTTP blocked")
        self.db.execute("BEGIN IMMEDIATE")
        try:
            row = self.read(account_key)
            if row is None:
                raise ValueError("Unknown P50 history account")
            if row["cursor"] != expected_cursor:
                raise ValueError("History cursor changed; stale page rejected")
            if row["state"] in ("window_covered", "terminal_verified"):
                raise ValueError("Completed history cannot be advanced")
            existing = self.db.execute(
                "SELECT * FROM history_receipts WHERE capture_id=?", (capture_id,)).fetchone()
            if existing is not None:
                raise ValueError("Duplicate history capture; no double advancement")
            decision = history_checkpoint(
                account_key=account_key, current_cursor=expected_cursor,
                next_cursor=next_cursor, oldest_post_at=oldest_post_at,
                cutoff_day=date.fromisoformat(row["cutoff_day"]),
                raw_receipt_verified=raw_receipt_verified,
                capture_verified=capture_verified, terminal=terminal,
                uncertain_http=uncertain_http)
            self.db.execute(
                "INSERT INTO history_receipts VALUES(?,?,?,?,?,?)",
                (capture_id, account_key, raw_receipt_id, decision["state"],
                 decision["next_cursor"], datetime.now().astimezone().isoformat()))
            self.db.execute(
                """UPDATE history_accounts SET cursor=?,state=?,
                   pages_verified=pages_verified+1,last_capture_id=?,
                   last_raw_receipt_id=? WHERE account_key=?""",
                (decision["next_cursor"], decision["state"],
                 capture_id, raw_receipt_id, account_key))
            self.db.execute("COMMIT")
            return decision
        except BaseException:
            self.db.execute("ROLLBACK")
            raise
