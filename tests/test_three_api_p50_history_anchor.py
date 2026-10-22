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

    def test_early_latest_pages_cannot_anchor(self):
        for page, phase in ((1, "front"), (2, "catchup")):
            record = SimpleNamespace(
                capture_id="capture-1",
                request=SimpleNamespace(
                    account_key=self.keys[0], phase=phase, page=page))
            journal = SimpleNamespace(get_capture=lambda _: record)
            def unexpected(_):
                raise AssertionError("RAW should not be read")
            raw = SimpleNamespace(get_raw_receipt=unexpected)
            with P50HistoryStore(self.path) as store:
                with self.assertRaises(ValueError):
                    anchor_latest_tail(
                        store, journal, raw, account_key=self.keys[0],
                        capture_id="capture-1")
                self.assertEqual(store.read(self.keys[0])["state"], "ready")

    def test_invalid_tail_timestamp_rejected_before_store_write(self):
        capture_id = "capture-tail-invalid"
        record = SimpleNamespace(
            capture_id=capture_id,
            request=SimpleNamespace(account_key=self.keys[0], phase="catchup",
                                    page=3, request_id="req-3", cursor="prior"),
            cursor_state=SimpleNamespace(request_id="req-3", current_cursor="prior",
                                         page=3, next_cursor="next"),
            normalized_posts=[{"timestamp": "2026-10-20T12:00:00+00:00"}],
        )
        journal = SimpleNamespace(get_capture=lambda _: record)
        receipt = SimpleNamespace(raw_receipt_id="receipt-3")
        raw = SimpleNamespace(get_raw_receipt=lambda _: receipt)
        with P50HistoryStore(self.path) as store:
            # The caller cannot manufacture an older cutoff date.
            with self.assertRaises(ValueError):
                anchor_latest_tail(store, journal, raw, account_key=self.keys[0],
                                   capture_id=capture_id, terminal=False,
                                   oldest_post_at=datetime(2025, 1, 1, tzinfo=timezone.utc))
            self.assertEqual(store.read(self.keys[0])["state"], "ready")

    def test_repeated_latest_tail_cursor_rejected_without_raw_lookup(self):
        record = SimpleNamespace(
            capture_id="repeat-tail",
            request=SimpleNamespace(account_key=self.keys[0], phase="catchup",
                                    page=3, request_id="req", cursor="same"),
            cursor_state=SimpleNamespace(request_id="req", current_cursor="same",
                                         page=3, next_cursor="same"),
        )
        journal = SimpleNamespace(get_capture=lambda _: record)
        raw = SimpleNamespace(get_raw_receipt=lambda _: SimpleNamespace(raw_receipt_id="raw"))
        with P50HistoryStore(self.path) as store:
            with self.assertRaises(ValueError):
                anchor_latest_tail(store, journal, raw, account_key=self.keys[0],
                                   capture_id="repeat-tail")
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
