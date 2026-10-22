"""Offline integration tests for P50 completed-latest handoff."""
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch
import sqlite3
import sys
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "raw_v2"))
from three_api_p50_history_handoff import handoff_completed_latest_accounts


class P50HandoffTests(unittest.TestCase):
    def setUp(self):
        self.accounts = [
            {"key": f"P50_{'US' if i < 18 else 'KR' if i < 34 else 'JP'}_{i + 1:03d}",
             "username": f"pilot_{i}", "country": "US" if i < 18 else "KR" if i < 34 else "JP",
             "ordinal": i + 1}
            for i in range(50)
        ]
        self.db = sqlite3.connect(":memory:")
        self.addCleanup(self.db.close)
        self.db.row_factory = sqlite3.Row
        self.db.execute("CREATE TABLE jobs (account_ordinal INTEGER, page INTEGER, phase TEXT, state TEXT)")
        self.db.execute("CREATE TABLE history_receipts (account_key TEXT, capture_id TEXT, committed_at TEXT)")
        self.key = self.accounts[0]["key"]
        self.history = SimpleNamespace(db=self.db, read=lambda key: {
            "account_key": key, "state": "ready" if key == self.key else "terminal_verified"})
        self.coordinator = SimpleNamespace(db=self.db)
        self.record = SimpleNamespace(
            request=SimpleNamespace(account_ordinal=1, account_key=self.key),
            normalized_posts=[{"timestamp": "2026-10-21T00:00:00Z"}],
            cursor_state=SimpleNamespace(next_cursor="opaque-cursor"))
        self.journal = SimpleNamespace(get_capture=lambda _: self.record)

    def seed(self, states=("done", "done", "done")):
        self.db.executemany("INSERT INTO jobs VALUES (?,?,?,?)", [
            (1, 1, "front", states[0]),
            (1, 2, "catchup", states[1]),
            (1, 3, "catchup", states[2]),
        ])

    def test_incomplete_latest_defers_without_anchoring(self):
        self.seed(("done", "done", "pending"))
        with patch("three_api_p50_history_handoff.anchor_latest_tail") as anchor:
            result = handoff_completed_latest_accounts(
                self.coordinator, self.history, self.journal, object(),
                self.accounts, {self.key: "capture-3"})
        anchor.assert_not_called()
        self.assertEqual(result["deferred"], [self.key])
        self.assertEqual(result["network_requests"], 0)

    def test_completed_latest_handoffs_exact_capture(self):
        self.seed()
        with patch("three_api_p50_history_handoff.anchor_latest_tail") as anchor:
            result = handoff_completed_latest_accounts(
                self.coordinator, self.history, self.journal, object(),
                self.accounts, {self.key: "capture-3"})
        self.assertEqual(result["anchored"], [self.key])
        self.assertEqual(anchor.call_args.kwargs["capture_id"], "capture-3")
        self.assertEqual(anchor.call_args.kwargs["oldest_post_at"],
                         datetime(2026, 10, 21, tzinfo=timezone.utc))
        self.assertFalse(anchor.call_args.kwargs["terminal"])

    def test_resume_same_capture_is_idempotent(self):
        self.db.execute("INSERT INTO history_receipts VALUES (?,?,?)",
                        (self.key, "capture-3", "2026-10-21T00:00:00Z"))
        self.history.read = lambda key: {
            "account_key": key, "state": "continuation_ready"
            if key == self.key else "terminal_verified"}
        with patch("three_api_p50_history_handoff.anchor_latest_tail") as anchor:
            result = handoff_completed_latest_accounts(
                self.coordinator, self.history, self.journal, object(),
                self.accounts, {self.key: "capture-3"})
        anchor.assert_not_called()
        self.assertIn(self.key, result["already_anchored"])

    def test_resume_conflicting_capture_fails_closed(self):
        self.db.execute("INSERT INTO history_receipts VALUES (?,?,?)",
                        (self.key, "capture-3", "2026-10-21T00:00:00Z"))
        self.history.read = lambda key: {
            "account_key": key, "state": "continuation_ready"
            if key == self.key else "terminal_verified"}
        with patch("three_api_p50_history_handoff.anchor_latest_tail") as anchor:
            with self.assertRaisesRegex(ValueError, "Conflicting latest-tail"):
                handoff_completed_latest_accounts(
                    self.coordinator, self.history, self.journal, object(),
                    self.accounts, {self.key: "different-capture"})
        anchor.assert_not_called()

    def test_wrong_ordinal_fails_closed(self):
        self.seed()
        self.record.request.account_ordinal = 2
        with patch("three_api_p50_history_handoff.anchor_latest_tail") as anchor:
            with self.assertRaises(ValueError):
                handoff_completed_latest_accounts(
                    self.coordinator, self.history, self.journal, object(),
                    self.accounts, {self.key: "capture-3"})
        anchor.assert_not_called()


if __name__ == "__main__":
    unittest.main()
