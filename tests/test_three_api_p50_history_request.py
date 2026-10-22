"""Offline tests for P50 history request planning."""
from datetime import date
from pathlib import Path
import sys
import tempfile
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "raw_v2"))
from three_api_p50_history_store import P50HistoryStore
from three_api_p50_history_request import prepare_history_request


class HistoryRequestTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.path = Path(self.temp.name) / "p50" / "history.sqlite"
        self.keys = [f"P50_US_{i:03d}" for i in range(1, 51)]
        self.names = {key: f"nailartist{i}" for i, key in enumerate(self.keys)}
        with P50HistoryStore(self.path) as store:
            store.initialize(self.keys, date(2025, 10, 22))

    def test_unanchored_history_never_dispatches(self):
        with P50HistoryStore(self.path) as store:
            self.assertIsNone(prepare_history_request(
                store, self.keys, self.names,
                latest_complete_keys=set(self.keys)))

    def test_anchored_cursor_is_used_with_limit_four(self):
        with P50HistoryStore(self.path) as store:
            store.anchor_verified_latest_tail(
                account_key=self.keys[0], next_cursor="abc123",
                capture_id="capture-1", raw_receipt_id="raw-1",
                raw_receipt_verified=True, capture_verified=True)
            result = prepare_history_request(
                store, self.keys, self.names,
                latest_complete_keys={self.keys[0]}, lane="B")
            self.assertEqual(result["cursor"], "abc123")
            self.assertEqual(result["spec"].limit, 4)
            self.assertEqual(result["spec"].after, "abc123")
            self.assertEqual(result["spec"].lane, "B")
            self.assertTrue(result["offline_only"])
            self.assertEqual(result["network_requests"], 0)

    def test_latest_incomplete_account_not_dispatched(self):
        with P50HistoryStore(self.path) as store:
            store.anchor_verified_latest_tail(
                account_key=self.keys[0], next_cursor="abc123",
                capture_id="capture-1", raw_receipt_id="raw-1",
                raw_receipt_verified=True, capture_verified=True)
            self.assertIsNone(prepare_history_request(
                store, self.keys, self.names,
                latest_complete_keys=set()))


if __name__ == "__main__":
    unittest.main()
