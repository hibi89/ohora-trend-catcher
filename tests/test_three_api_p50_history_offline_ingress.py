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

    def test_wrong_response_identity_never_touches_capture(self):
        request = SimpleNamespace(
            phase="history", history_offline_only=True, request_id="req-1")
        response = SimpleNamespace(request_id="req-other", http_status=200)
        with self.assertRaises(ValueError):
            ingest_offline_history_response(object(), object(), request, response)


if __name__ == "__main__":
    unittest.main()
