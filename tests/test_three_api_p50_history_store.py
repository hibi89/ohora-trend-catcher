"""Offline P50 history persistence and safe restart tests."""
from datetime import date, datetime, timezone
from pathlib import Path
import sys
import tempfile
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "raw_v2"))
from three_api_p50_history_store import P50HistoryStore


class P50HistoryStoreTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.path = Path(self.temp.name) / "p50" / "history.sqlite"
        self.keys = [f"P50_US_{i:03d}" for i in range(1, 51)]
        self.cutoff = date(2025, 10, 22)

    def initialize(self):
        with P50HistoryStore(self.path) as store:
            store.initialize(self.keys, self.cutoff)

    def commit(self, store, **changes):
        values = dict(account_key=self.keys[0], expected_cursor=None,
                      next_cursor="next-1",
                      oldest_post_at=datetime(2026, 1, 1, tzinfo=timezone.utc),
                      terminal=False, capture_id="capture-1",
                      raw_receipt_id="raw-1",
                      raw_receipt_verified=True, capture_verified=True)
        values.update(changes)
        return store.commit_verified_page(**values)

    def test_restart_preserves_independent_account_cursor(self):
        self.initialize()
        with P50HistoryStore(self.path) as store:
            self.assertEqual(self.commit(store)["state"], "continuation_ready")
        with P50HistoryStore(self.path) as store:
            self.assertEqual(store.read(self.keys[0])["cursor"], "next-1")
            self.assertEqual(store.read(self.keys[0])["pages_verified"], 1)
            self.assertIsNone(store.read(self.keys[1])["cursor"])

    def test_repeated_capture_rejected_without_advancement(self):
        self.initialize()
        with P50HistoryStore(self.path) as store:
            self.commit(store)
            with self.assertRaises(ValueError):
                self.commit(store, expected_cursor="next-1", next_cursor="next-2")
            self.assertEqual(store.read(self.keys[0])["pages_verified"], 1)

    def test_unverified_raw_never_advances(self):
        self.initialize()
        with P50HistoryStore(self.path) as store:
            result = self.commit(store, raw_receipt_verified=False)
            self.assertEqual(result["state"], "blocked_unverified_page")
            self.assertEqual(store.read(self.keys[0])["pages_verified"], 0)

    def test_unknown_http_never_advances(self):
        self.initialize()
        with P50HistoryStore(self.path) as store:
            self.commit(store, uncertain_http=True)
            self.assertEqual(store.read(self.keys[0])["pages_verified"], 0)

    def test_roster_change_rejected_without_mutation(self):
        self.initialize()
        with P50HistoryStore(self.path) as store:
            with self.assertRaises(ValueError):
                store.initialize(self.keys[:-1] + ["P50_JP_999"], self.cutoff)
            self.assertEqual(store.db.execute(
                "SELECT COUNT(*) FROM history_accounts").fetchone()[0], 50)

    def test_cutoff_change_rejected(self):
        self.initialize()
        with P50HistoryStore(self.path) as store:
            with self.assertRaises(ValueError):
                store.initialize(self.keys, date(2025, 10, 21))

    def test_stale_cursor_rejected(self):
        self.initialize()
        with P50HistoryStore(self.path) as store:
            with self.assertRaises(ValueError):
                self.commit(store, expected_cursor="stale")

    def test_terminal_history_cannot_restart(self):
        self.initialize()
        with P50HistoryStore(self.path) as store:
            self.commit(store, next_cursor=None, terminal=True)
            with self.assertRaises(ValueError):
                self.commit(store, capture_id="capture-2",
                            raw_receipt_id="raw-2")


if __name__ == "__main__":
    unittest.main()
