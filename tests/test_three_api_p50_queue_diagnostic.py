"""Synthetic P50 latest queue diagnostics: no Meta, secrets, or operating writes."""
from contextlib import closing
from datetime import datetime, timezone
from pathlib import Path
import sqlite3
import sys
import tempfile
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "raw_v2"))
from three_api_p50_queue_diagnostic import inspect_queue


class QueueDiagnosticTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name) / "p50"
        self.day = self.root / "2026-10-23"
        self.day.mkdir(parents=True)
        self.db = self.day / "coordinator.sqlite"
        with closing(sqlite3.connect(self.db)) as conn, conn:
            conn.executescript("""
                CREATE TABLE lanes(lane TEXT PRIMARY KEY,halted INTEGER,reason TEXT,ready_at TEXT);
                INSERT INTO lanes VALUES ('A',0,NULL,'2026-10-22T00:00:00+00:00');
                INSERT INTO lanes VALUES ('B',1,'usage_limit','2026-10-22T00:00:00+00:00');
                INSERT INTO lanes VALUES ('C',0,NULL,'2026-10-22T00:00:00+00:00');
                CREATE TABLE jobs(id INTEGER PRIMARY KEY,account_ordinal INTEGER,
                                  page INTEGER,lane TEXT,state TEXT,leased_until TEXT);
                INSERT INTO jobs VALUES(1,1,1,'B','pending',NULL);
                INSERT INTO jobs VALUES(2,1,2,'A','pending',NULL);
                INSERT INTO jobs VALUES(3,1,3,'C','pending',NULL);
                CREATE TABLE page_evidence(account_ordinal INTEGER,page INTEGER,
                                           terminal INTEGER,response_cursor TEXT);
                CREATE TABLE job_request_bindings(job_id INTEGER PRIMARY KEY,request_id TEXT);
            """)
        self.now = datetime(2026, 10, 23, 0, 0, tzinfo=timezone.utc)

    def test_held_b_blocks_dependent_a_and_c(self):
        result = inspect_queue(self.root, now=self.now)
        self.assertEqual(result["jobs_remaining"], 3)
        self.assertEqual(result["front_accounts_completed"], 0)
        self.assertEqual(result["page_coverage"]["1"], {"done": 0, "total": 1})
        self.assertFalse(result["daily_complete"])
        self.assertEqual(result["dispatchable_now"], 0)
        self.assertEqual(result["next_action"], "waiting_quota_lane")
        self.assertEqual(result["lanes"]["B"]["eligible_before_gate"], 1)
        self.assertEqual(result["lanes"]["A"]["waiting_predecessor"], 1)
        self.assertEqual(result["lanes"]["C"]["waiting_predecessor"], 1)
        self.assertEqual(result["meta_calls"], 0)
        self.assertEqual(result["db_writes"], 0)

    def test_verified_b_release_would_make_one_page_eligible(self):
        with closing(sqlite3.connect(self.db)) as conn, conn:
            conn.execute("UPDATE lanes SET halted=0,reason=NULL WHERE lane='B'")
        result = inspect_queue(self.root, now=self.now)
        self.assertEqual(result["dispatchable_now"], 1)
        self.assertEqual(result["next_action"], "ready_for_latest")

    def test_cursor_proof_needed_after_prior_done(self):
        with closing(sqlite3.connect(self.db)) as conn, conn:
            conn.execute("UPDATE jobs SET state='done' WHERE id=1")
        result = inspect_queue(self.root, now=self.now)
        self.assertEqual(result["front_accounts_completed"], 1)
        self.assertEqual(result["lanes"]["A"]["waiting_cursor"], 1)
        self.assertEqual(result["dispatchable_now"], 0)
        with closing(sqlite3.connect(self.db)) as conn, conn:
            conn.execute("INSERT INTO page_evidence VALUES(1,1,0,'cursor')")
        result = inspect_queue(self.root, now=self.now)
        self.assertEqual(result["lanes"]["A"]["dispatchable_now"], 1)

    def test_bound_jobs_not_marked_dispatchable(self):
        with closing(sqlite3.connect(self.db)) as conn, conn:
            conn.execute("INSERT INTO job_request_bindings VALUES(1,'immutable')")
        result = inspect_queue(self.root, now=self.now)
        self.assertEqual(result["lanes"]["B"]["already_bound"], 1)
        self.assertEqual(result["held_lanes_eligible_jobs"], 0)

    def test_missing_workspace_never_initialized(self):
        missing = Path(self.temp.name) / "another" / "p50"
        with self.assertRaises(ValueError):
            inspect_queue(missing, now=self.now)
        self.assertFalse(missing.exists())


if __name__ == "__main__":
    unittest.main()
