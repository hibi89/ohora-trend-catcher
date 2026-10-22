import json
from datetime import datetime, timedelta, timezone
from pathlib import Path
import sqlite3
import tempfile
import unittest

from raw_v2.p6_resume_b_guarded import run


NOW = datetime(2026, 10, 22, 12, 0, tzinfo=timezone.utc)


def make_workspace(root, *, fresh_usage=None, received_at=None):
    day = "2026-10-22"
    folder = root / day
    folder.mkdir(parents=True)
    ready_at = NOW - timedelta(minutes=1)
    db = sqlite3.connect(folder / "coordinator.sqlite")
    try:
        db.executescript("""
            CREATE TABLE lanes(lane TEXT PRIMARY KEY, halted INTEGER, reason TEXT, ready_at TEXT);
            CREATE TABLE jobs(id INTEGER PRIMARY KEY, account_ordinal INTEGER, page INTEGER,
                lane TEXT, phase TEXT, state TEXT, lease_id TEXT, leased_until TEXT);
            CREATE TABLE job_request_bindings(job_id INTEGER PRIMARY KEY, request_id TEXT UNIQUE,
                request_digest TEXT, reserved_at TEXT);
        """)
        db.execute("INSERT INTO lanes VALUES('B',1,'live_pending_usage',?)",
                   (ready_at.isoformat(),))
        db.execute("INSERT INTO jobs VALUES(451,151,1,'B','front','leased','l','later')")
        db.execute("INSERT INTO jobs VALUES(454,152,1,'B','front','pending',NULL,NULL)")
        db.execute("INSERT INTO job_request_bindings VALUES(451,'req-failed','digest','then')")
        if fresh_usage is not None:
            db.execute("INSERT INTO jobs VALUES(450,150,1,'B','front','done',NULL,NULL)")
            db.execute("INSERT INTO job_request_bindings VALUES(450,'req-safe','digest','then')")
        db.commit()
    finally:
        db.close()
    db = sqlite3.connect(folder / "capture.sqlite")
    try:
        db.executescript("""
            CREATE TABLE intents(request_id TEXT PRIMARY KEY, outcome TEXT NOT NULL);
            CREATE TABLE captures(request_id TEXT PRIMARY KEY, processed_receipt TEXT);
            CREATE TABLE response_evidence(request_id TEXT PRIMARY KEY, received_at TEXT,
                http_status INTEGER, safe_usage_json TEXT, classification TEXT);
        """)
        db.execute("INSERT INTO intents VALUES('req-failed','failed_no_retry')")
        if fresh_usage is not None:
            db.execute("INSERT INTO intents VALUES('req-safe','captured')")
            db.execute("INSERT INTO captures VALUES('req-safe','receipt-safe')")
            db.execute("INSERT INTO response_evidence VALUES(?,?,?,?,?)", (
                "req-safe", (received_at or NOW).isoformat().replace("+00:00", "Z"),
                200, json.dumps({"max_usage_percent": fresh_usage}), "safe"))
        db.commit()
    finally:
        db.close()
    return folder


class GuardedBResumeTests(unittest.TestCase):
    def test_elapsed_cooldown_without_fresh_usage_refuses_even_with_approval(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            make_workspace(root)
            with self.assertRaisesRegex(ValueError, "conditions not met"):
                run(root, "2026-10-22", True, now=NOW)
            db = sqlite3.connect(root / "2026-10-22" / "coordinator.sqlite")
            try:
                self.assertEqual(db.execute(
                    "SELECT halted,reason FROM lanes WHERE lane='B'").fetchone(),
                    (1, "live_pending_usage"))
            finally:
                db.close()

    def test_fresh_completed_safe_response_allows_explicit_resume_without_replay(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            make_workspace(root, fresh_usage=34)
            result = run(root, "2026-10-22", True, now=NOW)
            self.assertEqual(result["fresh_safe_usage_percent"], 34)
            self.assertTrue(result["lane_resumed"])
            self.assertFalse(result["failed_requests_replayed"])
            db = sqlite3.connect(root / "2026-10-22" / "coordinator.sqlite")
            try:
                self.assertEqual(db.execute(
                    "SELECT halted,reason FROM lanes WHERE lane='B'").fetchone(),
                    (0, None))
                self.assertEqual(db.execute(
                    "SELECT state FROM jobs WHERE id=451").fetchone()[0], "leased")
            finally:
                db.close()

    def test_stale_or_over_limit_usage_never_resumes(self):
        for usage, observed in ((85, NOW), (34, NOW - timedelta(minutes=2))):
            with self.subTest(usage=usage, observed=observed):
                with tempfile.TemporaryDirectory() as temp:
                    root = Path(temp)
                    make_workspace(root, fresh_usage=usage, received_at=observed)
                    with self.assertRaisesRegex(ValueError, "conditions not met"):
                        run(root, "2026-10-22", True, now=NOW)


if __name__ == "__main__":
    unittest.main()
