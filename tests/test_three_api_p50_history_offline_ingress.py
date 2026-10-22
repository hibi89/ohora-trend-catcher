"""Offline ingress preflight must not mutate durable stores on invalid input."""
from pathlib import Path
from types import SimpleNamespace
import sys
import unittest
from unittest.mock import patch
from datetime import date, datetime, timezone
import tempfile

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "raw_v2"))
from three_api_p50_history_offline_ingress import ingest_offline_history_response


class OfflineHistoryIngressTests(unittest.TestCase):
    def test_non_success_response_never_touches_capture(self):
        class ForbiddenCapture:
            def __getattr__(self, name):
                raise AssertionError("Capture accessed before preflight")
        request = SimpleNamespace(
            phase="history", history_offline_only=True, request_id="req-1")
        for status in (199, 400, 429, 500):
            response = SimpleNamespace(request_id="req-1", http_status=status)
            with self.subTest(status=status), self.assertRaises(ValueError):
                ingest_offline_history_response(
                    ForbiddenCapture(), object(), request, response)

    def test_unanchored_history_never_touches_capture(self):
        from three_api_p50_history_offline_ingress import (
            ingest_and_commit_offline_history_page)
        store = SimpleNamespace(read=lambda _: {
            "state": "ready", "cursor": None})
        request = SimpleNamespace(
            phase="history", account_key="P50_US_001", cursor=None)
        with self.assertRaises(ValueError):
            ingest_and_commit_offline_history_page(
                store, object(), object(), request, object(),
                oldest_post_at=None)

    def test_mismatched_history_cursor_never_touches_capture(self):
        from three_api_p50_history_offline_ingress import (
            ingest_and_commit_offline_history_page)
        store = SimpleNamespace(read=lambda _: {
            "state": "continuation_ready", "cursor": "expected"})
        request = SimpleNamespace(
            phase="history", account_key="P50_US_001", cursor="stale")
        with self.assertRaises(ValueError):
            ingest_and_commit_offline_history_page(
                store, object(), object(), request, object(),
                oldest_post_at=None)

    def test_invalid_completion_metadata_never_touches_capture(self):
        from datetime import datetime
        from three_api_p50_history_offline_ingress import (
            ingest_and_commit_offline_history_page)
        store = SimpleNamespace(read=lambda _: {
            "state": "continuation_ready", "cursor": "expected"})
        request = SimpleNamespace(
            phase="history", account_key="P50_US_001", cursor="expected")
        for metadata in (
            {"oldest_post_at": None, "terminal": "yes"},
            {"oldest_post_at": datetime(2026, 10, 22), "terminal": False},
        ):
            with self.subTest(metadata=metadata), self.assertRaises(ValueError):
                ingest_and_commit_offline_history_page(
                    store, object(), object(), request, object(), **metadata)

    def test_ingest_then_commit_order_and_receipt_failure(self):
        from three_api_p50_history_offline_ingress import (
            ingest_and_commit_offline_history_page)
        request = SimpleNamespace(
            phase="history", history_offline_only=True,
            account_key="P50_US_001", cursor="cursor-1", request_id="req-1")
        store = SimpleNamespace(read=lambda _: {
            "state": "continuation_ready", "cursor": "cursor-1"})
        capture = SimpleNamespace(get_capture_for_request=lambda _: SimpleNamespace(
            capture_id="cap-1"))
        events = []
        def ingest(*args):
            events.append("raw")
            return "receipt-1"
        def commit(*args, **kwargs):
            events.append("cursor")
            self.assertEqual(kwargs["capture_id"], "cap-1")
            return {"state": "continuation_ready"}
        with patch("three_api_p50_history_offline_ingress.ingest_offline_history_response",
                   side_effect=ingest), patch(
                   "three_api_p50_history_commit.commit_persisted_history_page",
                   side_effect=commit):
            result = ingest_and_commit_offline_history_page(
                store, capture, object(), request, object(),
                oldest_post_at=None)
        self.assertEqual(events, ["raw", "cursor"])
        self.assertEqual(result["history"]["state"], "continuation_ready")

        events.clear()
        def fail_raw(*args):
            events.append("raw")
            raise OSError("RAW unavailable")
        with patch("three_api_p50_history_offline_ingress.ingest_offline_history_response",
                   side_effect=fail_raw), patch(
                   "three_api_p50_history_commit.commit_persisted_history_page",
                   side_effect=commit):
            with self.assertRaises(OSError):
                ingest_and_commit_offline_history_page(
                    store, capture, object(), request, object(),
                    oldest_post_at=None)
        self.assertEqual(events, ["raw"])

    def test_sqlite_cursor_persists_only_after_verified_commit(self):
        from three_api_p50_history_store import P50HistoryStore
        from three_api_p50_history_offline_ingress import (
            ingest_and_commit_offline_history_page)
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "p50" / "history.sqlite"
            keys = [f"P50_US_{i:03d}" for i in range(1, 51)]
            with P50HistoryStore(path) as store:
                store.initialize(keys, date(2025, 10, 22))
                store.anchor_verified_latest_tail(
                    account_key=keys[0], next_cursor="tail",
                    capture_id="latest-cap", raw_receipt_id="latest-raw",
                    raw_receipt_verified=True, capture_verified=True)
                request = SimpleNamespace(
                    phase="history", history_offline_only=True,
                    account_key=keys[0], cursor="tail", request_id="req")
                capture = SimpleNamespace(
                    get_capture_for_request=lambda _: SimpleNamespace(
                        capture_id="history-cap"))
                def commit_real(store_arg, *_args, **kwargs):
                    self.assertEqual(kwargs["capture_id"], "history-cap")
                    return store_arg.commit_verified_page(
                        account_key=keys[0], expected_cursor="tail",
                        next_cursor="next-page",
                        oldest_post_at=datetime(2026, 1, 1, tzinfo=timezone.utc),
                        terminal=False, capture_id="history-cap",
                        raw_receipt_id="history-raw",
                        raw_receipt_verified=True, capture_verified=True)
                with patch(
                    "three_api_p50_history_offline_ingress.ingest_offline_history_response",
                    return_value="raw-ok"), patch(
                    "three_api_p50_history_commit.commit_persisted_history_page",
                    side_effect=commit_real):
                    result = ingest_and_commit_offline_history_page(
                        store, capture, object(), request, object(),
                        oldest_post_at=datetime(2026, 1, 1, tzinfo=timezone.utc))
                self.assertEqual(result["history"]["state"], "continuation_ready")
            with P50HistoryStore(path) as reopened:
                self.assertEqual(reopened.read(keys[0])["cursor"], "next-page")
                self.assertEqual(reopened.read(keys[0])["pages_verified"], 1)
                self.assertEqual(reopened.read(keys[1])["pages_verified"], 0)

    def test_wrong_response_identity_never_touches_capture(self):
        request = SimpleNamespace(
            phase="history", history_offline_only=True, request_id="req-1")
        response = SimpleNamespace(request_id="req-other", http_status=200)
        with self.assertRaises(ValueError):
            ingest_offline_history_response(object(), object(), request, response)


if __name__ == "__main__":
    unittest.main()
