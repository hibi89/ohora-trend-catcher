"""Offline ingress preflight must not mutate durable stores on invalid input."""
from pathlib import Path
from types import SimpleNamespace
import sys
import unittest

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

    def test_wrong_response_identity_never_touches_capture(self):
        request = SimpleNamespace(
            phase="history", history_offline_only=True, request_id="req-1")
        response = SimpleNamespace(request_id="req-other", http_status=200)
        with self.assertRaises(ValueError):
            ingest_offline_history_response(object(), object(), request, response)


if __name__ == "__main__":
    unittest.main()
