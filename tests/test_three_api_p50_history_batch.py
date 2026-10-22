"""Offline serial history batch: full preflight and bounded durable commits."""
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch
import sys
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "raw_v2"))
from three_api_p50_history_batch import ingest_offline_history_batch


def page(number, cursor, next_cursor, terminal=False):
    request = SimpleNamespace(
        request_id=f"req-{number}", phase="history", history_offline_only=True,
        account_key="P50_US_001", cursor=cursor,
        request_spec=SimpleNamespace(limit=4))
    response = SimpleNamespace(
        request_id=request.request_id, http_status=200, next_cursor=next_cursor)
    return {"request": request, "response": response,
            "oldest_post_at": datetime(2026, 1, number, tzinfo=timezone.utc),
            "terminal": terminal}


class OfflineHistoryBatchTests(unittest.TestCase):
    def test_two_serial_pages_use_ingress_in_order(self):
        pages = [page(1, "tail", "cursor-2"),
                 page(2, "cursor-2", None, terminal=True)]
        calls = []
        def ingest(store, capture, raw, request, response, **kwargs):
            calls.append((request.cursor, response.next_cursor))
            return {"history": {"state": "terminal_verified"
                    if kwargs["terminal"] else "continuation_ready"}}
        with patch("three_api_p50_history_batch.ingest_and_commit_offline_history_page",
                   side_effect=ingest):
            result = ingest_offline_history_batch(None, None, None, pages)
        self.assertEqual(calls, [("tail", "cursor-2"), ("cursor-2", None)])
        self.assertEqual(result["pages_committed"], 2)
        self.assertEqual(result["network_requests"], 0)

    def test_later_bad_page_preflight_prevents_earlier_writes(self):
        pages = [page(1, "tail", "next"), page(2, "next", None)]
        pages[1]["response"].request_id = "wrong"
        with patch("three_api_p50_history_batch.ingest_and_commit_offline_history_page") as ingest:
            with self.assertRaises(ValueError):
                ingest_offline_history_batch(None, None, None, pages)
            ingest.assert_not_called()

    def test_budget_and_duplicate_request_preflight(self):
        pages = [page(1, "tail", "next"), page(1, "next", None, terminal=True)]
        with patch("three_api_p50_history_batch.ingest_and_commit_offline_history_page") as ingest:
            with self.assertRaisesRegex(ValueError, "Duplicate"):
                ingest_offline_history_batch(None, None, None, pages)
            with self.assertRaisesRegex(ValueError, "budget"):
                ingest_offline_history_batch(None, None, None, pages, max_pages=1)
            ingest.assert_not_called()

    def test_chain_mismatch_preflight_blocks_all_writes(self):
        pages = [page(1, "tail", "next"), page(2, "wrong", None, terminal=True)]
        with patch("three_api_p50_history_batch.ingest_and_commit_offline_history_page") as ingest:
            with self.assertRaisesRegex(ValueError, "chain cursor mismatch"):
                ingest_offline_history_batch(None, None, None, pages)
            ingest.assert_not_called()

    def test_terminal_page_cannot_be_followed_by_same_account(self):
        pages = [page(1, "tail", None, terminal=True),
                 page(2, "next", None, terminal=True)]
        with patch("three_api_p50_history_batch.ingest_and_commit_offline_history_page") as ingest:
            with self.assertRaises(ValueError):
                ingest_offline_history_batch(None, None, None, pages)
            ingest.assert_not_called()

    def test_nonterminal_missing_cursor_preflight_blocks_all_writes(self):
        pages = [page(1, "tail", "next"), page(2, "next", None)]
        with patch("three_api_p50_history_batch.ingest_and_commit_offline_history_page") as ingest:
            with self.assertRaisesRegex(ValueError, "requires continuation cursor"):
                ingest_offline_history_batch(None, None, None, pages)
            ingest.assert_not_called()

    def test_first_commit_failure_stops_remaining_pages(self):
        pages = [page(1, "tail", "next"), page(2, "next", None, terminal=True)]
        with patch("three_api_p50_history_batch.ingest_and_commit_offline_history_page",
                   side_effect=OSError("disk full")) as ingest:
            with self.assertRaises(OSError):
                ingest_offline_history_batch(None, None, None, pages)
            self.assertEqual(ingest.call_count, 1)


if __name__ == "__main__":
    unittest.main()
