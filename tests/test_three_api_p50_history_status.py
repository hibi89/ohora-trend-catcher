"""Offline status and resume tests."""
from datetime import date
from pathlib import Path
import sys
import tempfile
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "raw_v2"))
from three_api_p50_history_store import P50HistoryStore
from three_api_p50_history_status import history_status

class HistoryStatusTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.path = Path(self.tmp.name) / "p50" / "history.sqlite"
        self.keys = [f"P50_US_{i:03d}" for i in range(1, 51)]
        with P50HistoryStore(self.path) as store:
            store.initialize(self.keys, date(2025, 10, 22))

    def test_empty_history_status(self):
        with P50HistoryStore(self.path) as store:
            result = history_status(store, self.keys,
                                    latest_complete_keys=set(self.keys))
            self.assertEqual(result["states"], {"ready": 50})
            self.assertEqual(result["verified_pages"], 0)
            self.assertIsNone(result["next_job"])
            self.assertEqual(result["database_writes"], 0)

    def test_resume_after_restart(self):
        with P50HistoryStore(self.path) as store:
            store.anchor_verified_latest_tail(
                account_key=self.keys[0], next_cursor="tail123",
                capture_id="latest-capture", raw_receipt_id="latest-raw",
                raw_receipt_verified=True, capture_verified=True)
        with P50HistoryStore(self.path) as store:
            result = history_status(store, self.keys,
                                    latest_complete_keys={self.keys[0]})
            self.assertEqual(result["anchored"], 1)
            self.assertEqual(result["next_job"]["cursor"], "tail123")

if __name__ == "__main__":
    unittest.main()
