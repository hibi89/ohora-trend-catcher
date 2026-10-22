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

    def test_repeated_capture_cursor_rejected_before_raw_lookup(self):
        account = self.keys[0]
        with P50HistoryStore(self.path) as store:
            store.anchor_verified_latest_tail(
                account_key=account, next_cursor="tail",
                capture_id="latest-cap", raw_receipt_id="latest-raw",
                raw_receipt_verified=True, capture_verified=True)
            request = SimpleNamespace(
                account_key=account, phase="history", history_offline_only=True,
                cursor="tail", request_id="history-request", page=4)
            cursor_state = SimpleNamespace(
                request_id="history-request", current_cursor="tail",
                next_cursor="tail", page=4)
            record = SimpleNamespace(
                capture_id="cap-repeated", request=request,
                cursor_state=cursor_state, normalized_posts=[])
            journal = SimpleNamespace(get_capture=lambda _: record)
            def forbidden_raw(_):
                raise AssertionError("RAW must not be read for repeated cursor")
            raw = SimpleNamespace(get_raw_receipt=forbidden_raw)
            with self.assertRaisesRegex(ValueError, "repeats its input cursor"):
                commit_persisted_history_page(
                    store, journal, raw, account_key=account,
                    capture_id="cap-repeated", oldest_post_at=None,
                    terminal=True)
            self.assertEqual(store.read(account)["cursor"], "tail")
            self.assertEqual(store.read(account)["pages_verified"], 0)

    def test_invalid_next_cursor_rejected_before_raw_lookup(self):
        account = self.keys[0]
        with P50HistoryStore(self.path) as store:
            store.anchor_verified_latest_tail(
                account_key=account, next_cursor="tail",
                capture_id="latest-cap", raw_receipt_id="latest-raw",
                raw_receipt_verified=True, capture_verified=True)
            request = SimpleNamespace(
                account_key=account, phase="history", history_offline_only=True,
                cursor="tail", request_id="req", page=4)
            for bad_cursor in ("", 123, [], {}):
                with self.subTest(cursor=bad_cursor):
                    cursor_state = SimpleNamespace(
                        request_id="req", current_cursor="tail",
                        next_cursor=bad_cursor, page=4)
                    record = SimpleNamespace(
                        capture_id="cap-invalid", request=request,
                        cursor_state=cursor_state, normalized_posts=[])
                    journal = SimpleNamespace(get_capture=lambda _: record)
                    raw = SimpleNamespace(get_raw_receipt=lambda _:
                        self.fail("RAW accessed for invalid cursor"))
                    with self.assertRaisesRegex(ValueError, "continuation cursor"):
                        commit_persisted_history_page(
                            store, journal, raw, account_key=account,
                            capture_id="cap-invalid", oldest_post_at=None,
                            terminal=True)
            self.assertEqual(store.read(account)["pages_verified"], 0)

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
