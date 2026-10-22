"""Offline history recovery never sends HTTP without persisted page evidence."""
from datetime import date
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "raw_v2"))
from three_api_p50_history_recovery import recover_history_pages
from three_api_p50_history_store import P50HistoryStore


class HistoryRecoveryTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.path = Path(self.tmp.name) / "p50" / "history.sqlite"
        self.keys = [f"P50_US_{i:03d}" for i in range(1, 51)]
        self.names = {k: "nailartist" for k in self.keys}
        with P50HistoryStore(self.path) as store:
            store.initialize(self.keys, date(2025, 10, 22))

    def test_no_anchor_means_no_work(self):
        with P50HistoryStore(self.path) as store:
            result = recover_history_pages(
                store, None, None, self.keys, self.names,
                latest_complete_keys=set(self.keys), captured_pages={})
            self.assertEqual(result["status"], "no_eligible_cursor")
            self.assertEqual(result["network_requests"], 0)

    def test_missing_page_stops_without_advancement(self):
        with P50HistoryStore(self.path) as store:
            store.anchor_verified_latest_tail(
                account_key=self.keys[0], next_cursor="tail",
                capture_id="latest-cap", raw_receipt_id="latest-raw",
                raw_receipt_verified=True, capture_verified=True)
            result = recover_history_pages(
                store, None, None, self.keys, self.names,
                latest_complete_keys={self.keys[0]}, captured_pages={})
            self.assertEqual(result["status"], "awaiting_persisted_page")
            self.assertEqual(result["deferred_accounts"], [self.keys[0]])
            self.assertEqual(store.read(self.keys[0])["pages_verified"], 0)

    def test_recovers_two_consecutive_persisted_pages(self):
        with P50HistoryStore(self.path) as store:
            account = self.keys[0]
            store.anchor_verified_latest_tail(
                account_key=account, next_cursor="tail",
                capture_id="latest-cap", raw_receipt_id="latest-raw",
                raw_receipt_verified=True, capture_verified=True)
            captured = {
                (account, "tail"): {
                    "capture_id": "cap-1", "oldest_post_at": None, "terminal": False},
                (account, "next"): {
                    "capture_id": "cap-2", "oldest_post_at": None, "terminal": True},
            }
            calls = []
            def verified_commit(store_arg, journal, raw_writer, *,
                                account_key, capture_id, oldest_post_at, terminal):
                calls.append(capture_id)
                if capture_id == "cap-1":
                    store_arg.db.execute(
                        "UPDATE history_accounts SET cursor='next',pages_verified=1 "
                        "WHERE account_key=?", (account_key,))
                    return {"state": "continuation_ready"}
                store_arg.db.execute(
                    "UPDATE history_accounts SET cursor=NULL,state='terminal_verified',"
                    "pages_verified=2 WHERE account_key=?", (account_key,))
                return {"state": "terminal_verified"}
            with patch("three_api_p50_history_recovery.commit_persisted_history_page",
                       side_effect=verified_commit):
                result = recover_history_pages(
                    store, None, None, self.keys, self.names,
                    latest_complete_keys={account}, captured_pages=captured,
                    max_steps=2)
            self.assertEqual(calls, ["cap-1", "cap-2"])
            self.assertEqual(len(result["completed"]), 2)
            self.assertEqual(result["status"], "completed")
            self.assertEqual(store.read(account)["state"], "terminal_verified")
            self.assertEqual(result["network_requests"], 0)

    def test_single_step_preserves_next_page_for_next_run(self):
        with P50HistoryStore(self.path) as store:
            account = self.keys[0]
            store.anchor_verified_latest_tail(
                account_key=account, next_cursor="tail",
                capture_id="latest-cap", raw_receipt_id="latest-raw",
                raw_receipt_verified=True, capture_verified=True)
            captured = {
                (account, "tail"): {
                    "capture_id": "cap-1", "oldest_post_at": None, "terminal": False},
                (account, "next"): {
                    "capture_id": "cap-2", "oldest_post_at": None, "terminal": True},
            }
            def verified_commit(store_arg, journal, raw_writer, *,
                                account_key, capture_id, oldest_post_at, terminal):
                store_arg.db.execute(
                    "UPDATE history_accounts SET cursor='next',pages_verified=1 "
                    "WHERE account_key=?", (account_key,))
                return {"state": "continuation_ready"}
            with patch("three_api_p50_history_recovery.commit_persisted_history_page",
                       side_effect=verified_commit) as commit:
                result = recover_history_pages(
                    store, None, None, self.keys, self.names,
                    latest_complete_keys={account}, captured_pages=captured,
                    max_steps=1)
            self.assertEqual(commit.call_count, 1)
            self.assertEqual(result["status"], "step_budget_exhausted")
            self.assertEqual(store.read(account)["cursor"], "next")

    def test_invalid_step_budget_rejected(self):
        with P50HistoryStore(self.path) as store:
            with self.assertRaises(ValueError):
                recover_history_pages(
                    store, None, None, self.keys, self.names,
                    latest_complete_keys=set(), captured_pages={},
                    max_steps=0)


if __name__ == "__main__":
    unittest.main()
