"""Offline safety checks for P50 history checkpoint decisions."""
from datetime import date, datetime, timezone
from pathlib import Path
import sys
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "raw_v2"))
from three_api_p50_history_checkpoint import history_checkpoint


class P50HistoryCheckpointTests(unittest.TestCase):
    def setUp(self):
        self.kw = dict(account_key="P50_US_001", current_cursor="old",
                       next_cursor="new",
                       oldest_post_at=datetime(2026, 9, 1, tzinfo=timezone.utc),
                       cutoff_day=date(2025, 10, 22),
                       raw_receipt_verified=True, capture_verified=True,
                       terminal=False)

    def check(self, **changes):
        return history_checkpoint(**(self.kw | changes))

    def test_verified_cursor_advances(self):
        self.assertEqual(self.check()["state"], "continuation_ready")
        self.assertEqual(self.check()["next_cursor"], "new")

    def test_unknown_http_blocks_even_with_receipts(self):
        result = self.check(uncertain_http=True)
        self.assertFalse(result["eligible_to_dispatch"])
        self.assertEqual(result["next_cursor"], "old")

    def test_missing_raw_receipt_blocks(self):
        self.assertEqual(self.check(raw_receipt_verified=False)["state"],
                         "blocked_unverified_page")

    def test_missing_capture_blocks(self):
        self.assertEqual(self.check(capture_verified=False)["state"],
                         "blocked_unverified_page")

    def test_older_than_cutoff_completes(self):
        self.assertEqual(self.check(oldest_post_at=datetime(
            2025, 10, 21, tzinfo=timezone.utc))["state"], "window_covered")

    def test_kst_boundary_does_not_prematurely_mark_window_covered(self):
        # 2025-10-21 16:00 UTC is 2025-10-22 01:00 KST.
        # KST cutoff=Oct 22 means this post is still in the window.
        self.assertEqual(self.check(oldest_post_at=datetime(
            2025, 10, 21, 16, tzinfo=timezone.utc))["state"],
                         "continuation_ready")

    def test_kst_previous_day_marks_window_covered(self):
        # 2025-10-21 14:59 UTC is still 2025-10-21 in KST.
        self.assertEqual(self.check(oldest_post_at=datetime(
            2025, 10, 21, 14, 59, tzinfo=timezone.utc))["state"],
                         "window_covered")

    def test_terminal_page_completes(self):
        self.assertEqual(self.check(terminal=True, next_cursor=None)["state"],
                         "terminal_verified")

    def test_terminal_cursor_conflict_fails_closed(self):
        with self.assertRaises(ValueError):
            self.check(terminal=True)

    def test_terminal_cursor_conflict_even_when_window_covered(self):
        with self.assertRaisesRegex(ValueError, "Terminal page"):
            self.check(
                terminal=True, next_cursor="unexpected",
                oldest_post_at=datetime(2025, 1, 1, tzinfo=timezone.utc))

    def test_missing_receipt_blocks_before_terminal_conflict(self):
        self.assertEqual(self.check(terminal=True, raw_receipt_verified=False)["state"],
                         "blocked_unverified_page")

    def test_unknown_http_blocks_before_terminal_conflict(self):
        self.assertEqual(self.check(terminal=True, uncertain_http=True)["state"],
                         "blocked_unknown_http")

    def test_missing_cursor_blocks(self):
        self.assertEqual(self.check(next_cursor=None)["state"],
                         "blocked_missing_cursor")

    def test_repeated_cursor_blocks(self):
        self.assertEqual(self.check(next_cursor="old")["state"],
                         "blocked_repeated_cursor")

    def test_missing_timestamp_does_not_imply_coverage(self):
        self.assertEqual(self.check(oldest_post_at=None)["state"],
                         "continuation_ready")

    def test_invalid_timestamp_rejected(self):
        with self.assertRaises(ValueError):
            self.check(oldest_post_at=datetime(2026, 1, 1))


if __name__ == "__main__":
    unittest.main()
