"""Offline P50 history anchor tests using independent evidence ports."""
from datetime import date, datetime, timezone
from pathlib import Path
from types import SimpleNamespace
import sys
import tempfile
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "raw_v2"))
from three_api_p50_history_anchor import anchor_latest_tail
from three_api_p50_history_store import P50HistoryStore


class HistoryAnchorTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.path = Path(self.tmp.name) / "p50" / "history.sqlite"
        self.keys = [f"P50_US_{i:03d}" for i in range(1, 51)]
        with P50HistoryStore(self.path) as store:
            store.initialize(self.keys, date(2025, 10, 22))

    def test_missing_capture_does_not_advance(self):
        journal = SimpleNamespace(get_capture=lambda _: None)
        raw = SimpleNamespace(get_raw_receipt=lambda _: None)
        with P50HistoryStore(self.path) as store:
            with self.assertRaises(ValueError):
                anchor_latest_tail(store, journal, raw, account_key=self.keys[0],
                                   capture_id="missing")
            self.assertEqual(store.read(self.keys[0])["state"], "ready")

    def test_wrong_account_does_not_read_raw(self):
        record = SimpleNamespace(capture_id="capture-1",
            request=SimpleNamespace(account_key=self.keys[1], phase="front"))
        journal = SimpleNamespace(get_capture=lambda _: record)
        def unexpected(_):
            raise AssertionError("RAW must not be read for wrong account")
        raw = SimpleNamespace(get_raw_receipt=unexpected)
        with P50HistoryStore(self.path) as store:
            with self.assertRaises(ValueError):
                anchor_latest_tail(store, journal, raw, account_key=self.keys[0],
                                   capture_id="capture-1")
            self.assertEqual(store.read(self.keys[0])["state"], "ready")


if __name__ == "__main__":
    unittest.main()
