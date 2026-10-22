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
    def store(self):
        return SimpleNamespace(read=lambda key: {
            "state": "continuation_ready", "cursor": "tail"})

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
            result = ingest_offline_history_batch(self.store(), None, None, pages)
        self.assertEqual(calls, [("tail", "cursor-2"), ("cursor-2", None)])
        self.assertEqual(result["pages_committed"], 2)
        self.assertEqual(result["network_requests"], 0)

    def test_later_bad_page_preflight_prevents_earlier_writes(self):
        pages = [page(1, "tail", "next"), page(2, "next", None)]
        pages[1]["response"].request_id = "wrong"
        with patch("three_api_p50_history_batch.ingest_and_commit_offline_history_page") as ingest:
            with self.assertRaises(ValueError):
                ingest_offline_history_batch(self.store(), None, None, pages)
            ingest.assert_not_called()

    def test_budget_and_duplicate_request_preflight(self):
        pages = [page(1, "tail", "next"), page(1, "next", None, terminal=True)]
        with patch("three_api_p50_history_batch.ingest_and_commit_offline_history_page") as ingest:
            with self.assertRaisesRegex(ValueError, "Duplicate"):
                ingest_offline_history_batch(None, None, None, pages)
            with self.assertRaisesRegex(ValueError, "budget"):
                ingest_offline_history_batch(self.store(), None, None, pages, max_pages=1)
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

    def test_unanchored_second_account_blocks_first_account_write(self):
        pages = [page(1, "tail", "next"), page(2, "other-tail", None, terminal=True)]
        pages[1]["request"].account_key = "P50_US_002"
        def read(key):
            return {"state": "continuation_ready",
                    "cursor": "tail" if key == "P50_US_001" else "different"}
        with patch("three_api_p50_history_batch.ingest_and_commit_offline_history_page") as ingest:
            with self.assertRaisesRegex(ValueError, "unverified persisted cursor"):
                ingest_offline_history_batch(
                    SimpleNamespace(read=read), None, None, pages)
            ingest.assert_not_called()

    def test_fifty_accounts_serial_batch_and_restart_cursor(self):
        """50 account pages use isolated persisted cursors in deterministic order."""
        import tempfile
        from datetime import date
        from three_api_p50_history_store import P50HistoryStore
        with tempfile.TemporaryDirectory() as tmp:
            db_path = Path(tmp) / "p50" / "history.sqlite"
            keys = ([f"P50_US_{i:03d}" for i in range(1, 19)] +
                    [f"P50_KR_{i:03d}" for i in range(1, 17)] +
                    [f"P50_JP_{i:03d}" for i in range(1, 17)])
            pages = []
            for index, key in enumerate(keys, 1):
                p = page(index if index <= 28 else index - 28,
                         f"tail-{index}", f"next-{index}")
                p["request"].account_key = key
                p["request"].request_id = f"history-{index}"
                p["response"].request_id = f"history-{index}"
                pages.append(p)
            with P50HistoryStore(db_path) as store:
                store.initialize(keys, date(2025, 10, 22))
                for index, key in enumerate(keys, 1):
                    store.anchor_verified_latest_tail(
                        account_key=key, next_cursor=f"tail-{index}",
                        capture_id=f"latest-capture-{index}",
                        raw_receipt_id=f"latest-raw-{index}",
                        raw_receipt_verified=True, capture_verified=True)
                committed = []
                def persist(store_arg, capture, raw, request, response, **kwargs):
                    index = keys.index(request.account_key) + 1
                    self.assertEqual(store_arg.read(request.account_key)["cursor"],
                                     f"tail-{index}")
                    decision = store_arg.commit_verified_page(
                        account_key=request.account_key,
                        expected_cursor=request.cursor,
                        next_cursor=response.next_cursor,
                        oldest_post_at=kwargs["oldest_post_at"],
                        terminal=False,
                        capture_id=f"history-capture-{index}",
                        raw_receipt_id=f"history-raw-{index}",
                        raw_receipt_verified=True, capture_verified=True)
                    committed.append(request.account_key)
                    return {"history": decision}
                with patch(
                    "three_api_p50_history_batch.ingest_and_commit_offline_history_page",
                    side_effect=persist):
                    result = ingest_offline_history_batch(
                        store, object(), object(), pages, max_pages=50)
                self.assertEqual(result["pages_committed"], 50)
                self.assertEqual(committed, keys)
                self.assertEqual(result["network_requests"], 0)
            with P50HistoryStore(db_path) as reopened:
                for index, key in enumerate(keys, 1):
                    row = reopened.read(key)
                    self.assertEqual(row["cursor"], f"next-{index}")
                    self.assertEqual(row["pages_verified"], 1)

    def test_first_commit_failure_stops_remaining_pages(self):
        pages = [page(1, "tail", "next"), page(2, "next", None, terminal=True)]
        with patch("three_api_p50_history_batch.ingest_and_commit_offline_history_page",
                   side_effect=OSError("disk full")) as ingest:
            with self.assertRaises(OSError):
                ingest_offline_history_batch(self.store(), None, None, pages)
            self.assertEqual(ingest.call_count, 1)


if __name__ == "__main__":
    unittest.main()
