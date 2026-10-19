"""Offline SQLite capture journal. No HTTP, credentials or RAW writes.

Stores canonical, allowlisted C contract records; deliberately does not
rehydrate draft contract objects until C freezes its serialization API.
"""
from __future__ import annotations

from datetime import datetime, timezone
import json
import sqlite3

def _contract():
    from three_api_runtime_contract import canonical_bytes, digest_sha256, assert_safe_tree
    return canonical_bytes, digest_sha256, assert_safe_tree

class CaptureJournal:
    """Crash-safe append-only evidence with idempotent capture IDs."""

    def __init__(self, path: str):
        if not isinstance(path, str) or not path or path == ":memory:":
            raise ValueError("Durable on-disk SQLite path required")
        self._db = sqlite3.connect(path, isolation_level=None, timeout=10)
        self._db.execute("PRAGMA busy_timeout=10000")
        self._db.execute("PRAGMA synchronous=FULL")
        self._db.execute("PRAGMA journal_mode=WAL")
        self._db.executescript("""
            CREATE TABLE IF NOT EXISTS intents(
                request_id TEXT PRIMARY KEY, run_id TEXT NOT NULL,
                payload BLOB NOT NULL, digest TEXT NOT NULL,
                outcome TEXT NOT NULL DEFAULT 'pending'
                CHECK(outcome IN ('pending','captured','unknown','failed_no_retry','manual_retry_approved')));
            CREATE INDEX IF NOT EXISTS intents_run ON intents(run_id,outcome);
            CREATE TABLE IF NOT EXISTS captures(
                capture_id TEXT PRIMARY KEY, request_id TEXT NOT NULL UNIQUE,
                run_id TEXT NOT NULL, payload BLOB NOT NULL,
                digest TEXT NOT NULL, processed_receipt TEXT,
                FOREIGN KEY(request_id) REFERENCES intents(request_id));
            CREATE INDEX IF NOT EXISTS captures_run ON captures(run_id,processed_receipt);
        """)
        self._db.execute("PRAGMA foreign_keys=ON")

    def close(self):
        self._db.close()

    def _write(self, sql, args, *, existing_sql, existing_key, digest):
        with self._db:
            existing = self._db.execute(existing_sql, (existing_key,)).fetchone()
            if existing:
                if existing[0] != digest:
                    raise ValueError("Conflicting immutable evidence")
                return False
            self._db.execute(sql, args)
            return True

    def save_intent(self, request):
        canonical, digest_fn, safe = _contract()
        safe(request)
        data = canonical(request)
        digest = digest_fn(request)
        return self._write(
            "INSERT INTO intents(request_id,run_id,payload,digest) VALUES(?,?,?,?)",
            (request.request_id, request.run_id, data, digest),
            existing_sql="SELECT digest FROM intents WHERE request_id=?",
            existing_key=request.request_id, digest=digest)

    def save_capture(self, record):
        canonical, digest_fn, safe = _contract()
        safe(record)
        data = canonical(record)
        digest = digest_fn(record)
        with self._db:
            intent = self._db.execute(
                "SELECT digest,outcome FROM intents WHERE request_id=?",
                (record.request.request_id,)).fetchone()
            if intent is None:
                raise ValueError("Capture requires durable intent")
            if intent[0] != digest_fn(record.request):
                raise ValueError("Capture request differs from intent")
            existing = self._db.execute(
                "SELECT digest FROM captures WHERE capture_id=? OR request_id=?",
                (record.capture_id,record.request.request_id)).fetchone()
            if existing and existing[0] != digest:
                raise ValueError("Conflicting capture evidence")
            if not existing:
                self._db.execute(
                    "INSERT INTO captures(capture_id,request_id,run_id,payload,digest) VALUES(?,?,?,?,?)",
                    (record.capture_id,record.request.request_id,record.request.run_id,data,digest))
            self._db.execute("UPDATE intents SET outcome='captured' WHERE request_id=?",
                             (record.request.request_id,))
        return digest

    def list_pending_capture_bytes(self, run_id):
        return [row[0] for row in self._db.execute(
            "SELECT payload FROM captures WHERE run_id=? AND processed_receipt IS NULL ORDER BY rowid",
            (run_id,))]

    def confirm_processed(self, capture_id, raw_receipt_id):
        if not isinstance(raw_receipt_id,str) or not raw_receipt_id:
            raise ValueError("RAW receipt required")
        with self._db:
            row = self._db.execute(
                "SELECT processed_receipt FROM captures WHERE capture_id=?",
                (capture_id,)).fetchone()
            if row is None or (row[0] is not None and row[0] != raw_receipt_id):
                raise ValueError("Unknown capture or conflicting RAW receipt")
            self._db.execute(
                "UPDATE captures SET processed_receipt=? WHERE capture_id=?",
                (raw_receipt_id,capture_id))

    def mark_outcome_unknown(self, request_id):
        with self._db:
            row = self._db.execute(
                "SELECT outcome FROM intents WHERE request_id=?", (request_id,)).fetchone()
            if row is None or row[0] not in ("pending","unknown"):
                raise ValueError("Unknown or already resolved intent")
            self._db.execute("UPDATE intents SET outcome='unknown' WHERE request_id=?",
                             (request_id,))

    def list_unresolved_intent_bytes(self, run_id):
        return [row[0] for row in self._db.execute(
            "SELECT payload FROM intents WHERE run_id=? AND outcome IN ('pending','unknown') ORDER BY rowid",
            (run_id,))]
