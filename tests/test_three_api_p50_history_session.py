"""Integration checks for offline P50 history session."""
from datetime import date
from pathlib import Path
import sys
import tempfile
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "raw_v2"))
from three_api_p50_history_store import P50HistoryStore
from three_api_p50_history_session import run_offline_history_session


class HistorySessionTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.path = Path(self.tmp.name) / "p50" / "history.sqlite"
        self.keys = [f"P50_US_{i:03d}" for i in range(1, 51)]
        self.names = {key: "nailartist" for key in self.keys}
        with P50HistoryStore(self.path) as store:
            store.initialize(self.keys, date(2025, 10, 22))

    def test_empty_session_does_not_advance(self):
        with P50HistoryStore(self.path) as store:
            result = run_offline_history_session(
                store, None, None, self.keys, self.names,
                latest_complete_keys=set(self.keys), captured_pages={})
            self.assertEqual(result["history_pages_added"], 0)
            self.assertEqual(result["network_requests"], 0)
            self.assertEqual(result["recovery"]["status"], "no_eligible_cursor")

    def test_restart_preserves_pending_cursor(self):
        with P50HistoryStore(self.path) as store:
            store.anchor_verified_latest_tail(
                account_key=self.keys[0], next_cursor="cursor123",
                capture_id="latest-capture", raw_receipt_id="latest-raw",
                raw_receipt_verified=True, capture_verified=True)
        with P50HistoryStore(self.path) as store:
            result = run_offline_history_session(
                store, None, None, self.keys, self.names,
                latest_complete_keys={self.keys[0]}, captured_pages={})
            self.assertEqual(result["before"]["next_job"]["cursor"], "cursor123")
            self.assertEqual(result["after"]["next_job"]["cursor"], "cursor123")
            self.assertEqual(result["recovery"]["status"], "awaiting_persisted_page")
            self.assertEqual(result["recovery"]["deferred_accounts"], [self.keys[0]])
            self.assertEqual(result["history_pages_added"], 0)

    def test_one_shot_iterables_survive_before_recovery_after(self):
        with P50HistoryStore(self.path) as store:
            result = run_offline_history_session(
                store, None, None, iter(self.keys), self.names,
                latest_complete_keys=iter(self.keys), captured_pages={})
            self.assertEqual(result["history_pages_added"], 0)
            self.assertEqual(result["recovery"]["status"], "no_eligible_cursor")
            self.assertEqual(result["network_requests"], 0)

if __name__ == "__main__":
    unittest.main()
