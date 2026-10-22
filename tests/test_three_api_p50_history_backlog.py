"""Tests for offline backlog scan without network or writes."""
from datetime import date
from pathlib import Path
import sys
import tempfile
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "raw_v2"))
from three_api_p50_history_store import P50HistoryStore
from three_api_p50_history_backlog import scan_history_backlog

class BacklogTests(unittest.TestCase):
    def test_ready_account_not_hidden_by_missing_account(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "p50" / "history.sqlite"
            keys = [f"P50_US_{i:03d}" for i in range(50)]
            with P50HistoryStore(path) as store:
                store.initialize(keys, date(2025, 10, 22))
                for i in (0, 1):
                    store.anchor_verified_latest_tail(
                        account_key=keys[i], next_cursor=f"cursor{i}",
                        capture_id=f"capture{i}", raw_receipt_id=f"raw{i}",
                        raw_receipt_verified=True, capture_verified=True)
                result = scan_history_backlog(
                    store, keys, latest_complete_keys=set(keys),
                    captured_pages={(keys[1], "cursor1"): {"capture_id": "saved"}})
                self.assertEqual(len(result["ready"]), 1)
                self.assertEqual(result["ready"][0]["account_key"], keys[1])
                self.assertEqual(len(result["awaiting_persisted_page"]), 1)
                self.assertEqual(result["database_writes"], 0)

if __name__ == "__main__":
    unittest.main()
