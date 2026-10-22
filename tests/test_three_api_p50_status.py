"""P50 read-only progress checks without live credentials."""
import sqlite3
from contextlib import closing
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "raw_v2"))
from three_api_p50_status import inspect_p50


class P50StatusTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name) / "p50"

    def test_missing_workspace_is_not_initialized_and_not_created(self):
        self.assertEqual(inspect_p50(self.root)["status"], "not_initialized")
        self.assertFalse(self.root.exists())

    def test_reject_wrong_workspace(self):
        with self.assertRaises(ValueError):
            inspect_p50(Path(self.temp.name) / "production")

    def test_reject_symlinked_workspace(self):
        target = Path(self.temp.name) / "actual"
        target.mkdir()
        try:
            self.root.symlink_to(target, target_is_directory=True)
        except (OSError, NotImplementedError):
            self.skipTest("Directory symlinks unavailable on this platform")
        with self.assertRaises(ValueError):
            inspect_p50(self.root)

    def test_counts_raw_and_daily_jobs_without_writes(self):
        self.root.mkdir()
        raw = self.root / "p50_raw.sqlite"
        with closing(sqlite3.connect(raw)) as db:
            db.executescript("""
                CREATE TABLE accounts(account_key TEXT);
                CREATE TABLE posts(post_id TEXT);
                CREATE TABLE post_observations(id INTEGER);
                CREATE TABLE three_api_contract_receipts(capture_id TEXT);
            """)
            db.executemany("INSERT INTO accounts VALUES (?)",
                           [(f"key-{i}",) for i in range(50)])
            db.execute("INSERT INTO posts VALUES ('p1')")
            db.execute("INSERT INTO post_observations VALUES (1)")
            db.execute("INSERT INTO three_api_contract_receipts VALUES ('c1')")
        day = self.root / "2026-10-22"
        day.mkdir()
        with closing(sqlite3.connect(day / "coordinator.sqlite")) as db:
            db.executescript("""
                CREATE TABLE jobs(state TEXT,lane TEXT);
                INSERT INTO jobs VALUES ('done','A');
                INSERT INTO jobs VALUES ('pending','B');
            """)
        before = raw.read_bytes()
        result = inspect_p50(self.root)
        self.assertIsNone(result["live_meta_called"])
        self.assertEqual(result["accounts"], 50)
        self.assertEqual(result["unique_posts"], 1)
        self.assertEqual(result["post_observations"], 1)
        self.assertEqual(result["contract_receipts"], 1)
        self.assertEqual(result["daily_jobs"][0]["committed_jobs_by_lane"], {"A": 1})
        self.assertEqual(raw.read_bytes(), before)


if __name__ == "__main__":
    unittest.main()
