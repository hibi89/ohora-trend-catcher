"""Integration checks for offline P50 history session."""
from datetime import date
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch
from datetime import datetime, timezone

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "raw_v2"))
from three_api_p50_history_store import P50HistoryStore
from three_api_p50_history_session import run_offline_history_session


class HistorySessionTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.path = Path(self.tmp.name) / "p50" / "history.sqlite"
        self.keys = [f"P50_US_{i:03d}" for i in range(1, 51)]
        self.names = {key: "nailartist" for key in self.keys}
        with P50HistoryStore(self.path) as store:
            store.initialize(self.keys, date(2025, 10, 22))

    def test_invalid_budget_rejected_before_store_access(self):
        class ForbiddenStore:
            def __getattr__(self, name):
                raise AssertionError("Store accessed before budget validation")
        for budget in (0, 51, True, 1.5, "10"):
            with self.subTest(budget=budget), self.assertRaisesRegex(
                    ValueError, "step budget"):
                run_offline_history_session(
                    ForbiddenStore(), None, None, self.keys, self.names,
                    latest_complete_keys=set(self.keys), captured_pages={},
                    max_steps=budget)

    def test_empty_session_does_not_advance(self):
        with P50HistoryStore(self.path) as store:
            result = run_offline_history_session(
                store, None, None, self.keys, self.names,
                latest_complete_keys=set(self.keys), captured_pages={})
            self.assertEqual(result["history_pages_added"], 0)
            self.assertEqual(result["network_requests"], 0)
            self.assertEqual(result["recovery"]["status"], "no_eligible_cursor")

    def test_restart_preserves_pending_cursor(self):
        with P50HistoryStore(self.path) as store:
            store.anchor_verified_latest_tail(
                account_key=self.keys[0], next_cursor="cursor123",
                capture_id="latest-capture", raw_receipt_id="latest-raw",
                raw_receipt_verified=True, capture_verified=True)
        with P50HistoryStore(self.path) as store:
            result = run_offline_history_session(
                store, None, None, self.keys, self.names,
                latest_complete_keys={self.keys[0]}, captured_pages={})
            self.assertEqual(result["before"]["next_job"]["cursor"], "cursor123")
            self.assertEqual(result["after"]["next_job"]["cursor"], "cursor123")
            self.assertEqual(result["recovery"]["status"], "awaiting_persisted_page")
            self.assertEqual(result["recovery"]["deferred_accounts"], [self.keys[0]])
            self.assertEqual(result["history_pages_added"], 0)

    def test_one_shot_iterables_survive_before_recovery_after(self):
        with P50HistoryStore(self.path) as store:
            result = run_offline_history_session(
                store, None, None, iter(self.keys), self.names,
                latest_complete_keys=iter(self.keys), captured_pages={})
            self.assertEqual(result["history_pages_added"], 0)
            self.assertEqual(result["recovery"]["status"], "no_eligible_cursor")
            self.assertEqual(result["network_requests"], 0)

    def test_two_page_recovery_reports_durable_progress_after_restart(self):
        account = self.keys[0]
        with P50HistoryStore(self.path) as store:
            store.anchor_verified_latest_tail(
                account_key=account, next_cursor="tail",
                capture_id="latest-capture", raw_receipt_id="latest-raw",
                raw_receipt_verified=True, capture_verified=True)
            captured = {
                (account, "tail"): {
                    "capture_id": "history-cap-1",
                    "oldest_post_at": datetime(2026, 1, 1, tzinfo=timezone.utc),
                    "terminal": False},
                (account, "page-2"): {
                    "capture_id": "history-cap-2",
                    "oldest_post_at": datetime(2025, 1, 1, tzinfo=timezone.utc),
                    "terminal": True},
            }
            def verified_commit(store_arg, journal, raw_writer, *,
                                account_key, capture_id, oldest_post_at, terminal):
                return store_arg.commit_verified_page(
                    account_key=account_key,
                    expected_cursor=store_arg.read(account_key)["cursor"],
                    next_cursor=None if terminal else "page-2",
                    oldest_post_at=oldest_post_at, terminal=terminal,
                    capture_id=capture_id, raw_receipt_id="raw-" + capture_id,
                    raw_receipt_verified=True, capture_verified=True)
            with patch("three_api_p50_history_recovery.commit_persisted_history_page",
                       side_effect=verified_commit):
                result = run_offline_history_session(
                    store, None, None, self.keys, self.names,
                    latest_complete_keys={account}, captured_pages=captured,
                    max_steps=2)
            self.assertEqual(result["history_pages_added"], 2)
            self.assertEqual(result["recovery"]["status"], "completed")
            self.assertEqual(result["after"]["verified_pages"], 2)
        with P50HistoryStore(self.path) as reopened:
            self.assertEqual(reopened.read(account)["state"], "window_covered")
            self.assertEqual(reopened.read(account)["pages_verified"], 2)
            self.assertIsNone(reopened.read(account)["cursor"])

    def test_session_rejects_mismatched_durable_page_count(self):
        with P50HistoryStore(self.path) as store:
            with patch("three_api_p50_history_session.recover_history_pages",
                       return_value={"completed": [{"account_key": self.keys[0]}]}):
                with self.assertRaisesRegex(RuntimeError, "receipt count"):
                    run_offline_history_session(
                        store, None, None, self.keys, self.names,
                        latest_complete_keys=set(), captured_pages={})

if __name__ == "__main__":
    unittest.main()
