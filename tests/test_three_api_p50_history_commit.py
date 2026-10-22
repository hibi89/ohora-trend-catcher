"""P50 persisted history commit rejects absent and wrong-phase evidence."""
from datetime import date
from pathlib import Path
from types import SimpleNamespace
import sys
import tempfile
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "raw_v2"))
from three_api_p50_history_commit import commit_persisted_history_page
from three_api_p50_history_store import P50HistoryStore


class HistoryCommitTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.path = Path(self.tmp.name) / "p50" / "history.sqlite"
        self.keys = [f"P50_US_{i:03d}" for i in range(1, 51)]
        with P50HistoryStore(self.path) as store:
            store.initialize(self.keys, date(2025, 10, 22))

    def test_missing_capture_preserves_cursor(self):
        journal = SimpleNamespace(get_capture=lambda _: None)
        raw = SimpleNamespace(get_raw_receipt=lambda _: None)
        with P50HistoryStore(self.path) as store:
            with self.assertRaises(ValueError):
                commit_persisted_history_page(
                    store, journal, raw, account_key=self.keys[0],
                    capture_id="absent", oldest_post_at=None)
            self.assertEqual(store.read(self.keys[0])["pages_verified"], 0)

    def test_wrong_phase_rejected_before_raw_lookup(self):
        record = SimpleNamespace(capture_id="cap",
            request=SimpleNamespace(account_key=self.keys[0], phase="front"))
        journal = SimpleNamespace(get_capture=lambda _: record)
        def unexpected(_):
            raise AssertionError("RAW lookup must not occur")
        raw = SimpleNamespace(get_raw_receipt=unexpected)
        with P50HistoryStore(self.path) as store:
            with self.assertRaises(ValueError):
                commit_persisted_history_page(
                    store, journal, raw, account_key=self.keys[0],
                    capture_id="cap", oldest_post_at=None)
            self.assertEqual(store.read(self.keys[0])["pages_verified"], 0)


if __name__ == "__main__":
    unittest.main()
