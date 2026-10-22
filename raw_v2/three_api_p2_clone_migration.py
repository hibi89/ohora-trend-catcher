"""Explicit offline P2 schema migration for a caller-owned RAW v2 clone.

No file is copied/opened and no operating SQLite filename is accepted.
This only adds versioned RAW tables after taking a fingerprint of all
pre-existing legacy RAW data; it never rewrites posts, post_media or histories.
P5 later owns backup, data-file selection, restore drills and live approval.
"""
from __future__ import annotations

from datetime import datetime, timezone
from hashlib import sha256
from pathlib import Path
import json
import sqlite3

from three_api_observation_versions import ensure_observation_schema
from three_api_raw_writer import _reject_operational_database, _validate_schema

_MIGRATION_ID = "p6-p2-append-only-v1"
_LEGACY = ("accounts", "posts", "post_media", "post_observations",
           "account_observations", "carousel_status", "request_log",
           "field_observations", "runs", "slots", "slot_accounts")


def _original_fingerprint(db):
    dig = sha256()
    counts = {}
    for table in _LEGACY:
        rows = db.execute(f'SELECT * FROM "{table}" ORDER BY rowid')
        count = 0
        for row in rows:
            safe = [(value.hex() if isinstance(value, bytes) else value) for value in row]
            dig.update(json.dumps(safe, ensure_ascii=False, separators=(",", ":"),
                                  allow_nan=False, default=str).encode("utf-8"))
            dig.update(b"\n")
            count += 1
        counts[table] = count
    return counts, dig.hexdigest()


def migrate_explicit_clone(db: sqlite3.Connection, *, clone_confirmed=False):
    """Return verified additive migration record; NEVER migrate a live DB.

    Explicit confirmation and a path tagged 'clone' (or :memory:) are required.
    The caller must have taken a backup and controls the connection lifecycle.
    """
    if type(clone_confirmed) is not bool or not clone_confirmed:
        raise ValueError("Explicit cloned-database confirmation required")
    if not isinstance(db, sqlite3.Connection) or db.in_transaction:
        raise ValueError("Idle caller-owned SQLite connection required")
    _reject_operational_database(db)
    main = next((path for _, name, path in db.execute("PRAGMA database_list")
                 if name == "main"), "")
    if main and "clone" not in Path(main).stem.casefold():
        raise ValueError("Only an explicitly named clone DB is eligible")
    _validate_schema(db)
    if db.execute("PRAGMA integrity_check").fetchone()[0] != "ok":
        raise ValueError("Source clone integrity failed")
    if db.execute("PRAGMA foreign_key_check").fetchall():
        raise ValueError("Source clone contains broken foreign keys")
    counts, before_digest = _original_fingerprint(db)
    db.execute("BEGIN IMMEDIATE")
    try:
        ensure_observation_schema(db)
        db.execute("""CREATE TABLE IF NOT EXISTS p2_schema_migrations(
            migration_id TEXT PRIMARY KEY,
            source_fingerprint_sha256 TEXT NOT NULL,
            source_counts_json TEXT NOT NULL,
            applied_at TEXT NOT NULL)""")
        exists = db.execute(
            "SELECT 1 FROM p2_schema_migrations WHERE migration_id=?",
            (_MIGRATION_ID,)).fetchone() is not None
        if not exists:
            stamp = datetime.now(timezone.utc).isoformat(timespec="microseconds")
            db.execute("INSERT INTO p2_schema_migrations VALUES(?,?,?,?)",
                       (_MIGRATION_ID, before_digest,
                        json.dumps(counts, sort_keys=True), stamp))
        after_counts, after_digest = _original_fingerprint(db)
        if after_counts != counts or after_digest != before_digest:
            raise ValueError("Legacy source data changed during schema migration")
        if db.execute("PRAGMA integrity_check").fetchone()[0] != "ok":
            raise ValueError("Clone integrity failed after migration")
        if db.execute("PRAGMA foreign_key_check").fetchall():
            raise ValueError("Clone foreign keys failed after migration")
        db.execute("COMMIT")
    except BaseException:
        db.execute("ROLLBACK")
        raise
    return {"migration_id": _MIGRATION_ID,
            "status": "already_present" if exists else "applied",
            "preserved_legacy_rows": counts,
            "verified_legacy_sha256": before_digest,
            "source": "explicit_offline_clone_only"}
