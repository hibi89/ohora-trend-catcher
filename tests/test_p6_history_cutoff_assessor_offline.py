"""Synthetic history cutoff evidence tests. No Meta or operating DB."""
import unittest
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from datetime import datetime, timedelta, timezone

from raw_v2.history_cutoff_evidence import assess_cutoff_evidence


class CutoffAssessorTests(unittest.TestCase):
    def setUp(self):
        self.anchor = datetime(2026, 10, 20, tzinfo=timezone.utc)

    def check(self, days, offset_microseconds, **changes):
        cutoff = self.anchor - timedelta(days=days)
        page = dict(cursor_in="root", cursor_out="next", receipt="raw:1", receipt_verified=True, timestamps_verified=True,
                    published_at=[cutoff + timedelta(microseconds=offset_microseconds)])
        page.update(changes)
        return assess_cutoff_evidence(anchor=self.anchor, cutoff=cutoff,
                                      pages=[page], rooted=True)

    def test_cutoff_before_equal_after(self):
        for days in (365, 730):
            with self.subTest(days=days):
                self.assertTrue(self.check(days, -1)["verified"])
                self.assertTrue(self.check(days, 0)["verified"])
                self.assertFalse(self.check(days, 1)["verified"])

    def test_missing_root_receipt_timestamp_or_terminal_proof(self):
        for days in (365, 730):
            cutoff = self.anchor - timedelta(days=days)
            base = dict(cursor_in="root", cursor_out=None, receipt="raw:1", receipt_verified=True, timestamps_verified=True,
                        published_at=[cutoff + timedelta(seconds=1)])
            for key, value in (("receipt", None), ("published_at", [])):
                self.assertFalse(self.check(days, 1, **{key: value})["verified"])
            self.assertFalse(assess_cutoff_evidence(
                anchor=self.anchor, cutoff=cutoff, pages=[base], rooted=False)["verified"])
            self.assertFalse(assess_cutoff_evidence(
                anchor=self.anchor, cutoff=cutoff, pages=[base], rooted=True)["verified"])
            base["terminal_verified"] = True
            self.assertTrue(assess_cutoff_evidence(
                anchor=self.anchor, cutoff=cutoff, pages=[base], rooted=True)["verified"])

    def test_chain_gap_fails_closed(self):
        cutoff = self.anchor - timedelta(days=365)
        pages = [
            dict(cursor_in="root", cursor_out="cursor2", receipt="raw:1", receipt_verified=True, timestamps_verified=True,
                 published_at=[self.anchor - timedelta(days=1)]),
            dict(cursor_in="wrong", cursor_out="cursor3", receipt="raw:2", receipt_verified=True, timestamps_verified=True,
                 published_at=[cutoff - timedelta(seconds=1)]),
        ]
        self.assertEqual(assess_cutoff_evidence(
            anchor=self.anchor, cutoff=cutoff, pages=pages,
            rooted=True)["reason"], "cursor_gap")


    def test_unverified_receipt_and_timestamp_are_rejected(self):
        for days in (365, 730):
            self.assertEqual(self.check(
                days, -1, receipt_verified=False)["reason"], "receipt_unverified")
            self.assertEqual(self.check(
                days, -1, timestamps_verified=False)["reason"], "timestamps_unverified")

    def test_cursor_cycle_is_rejected(self):
        self.assertEqual(self.check(
            365, -1, cursor_in="root", cursor_out="root")["reason"], "cursor_cycle")


    def test_repeated_cursor_across_multiple_pages_is_rejected(self):
        cutoff = self.anchor - timedelta(days=365)
        pages = [
            dict(cursor_in="root", cursor_out="a", receipt="raw:1",
                 receipt_verified=True, timestamps_verified=True,
                 published_at=[self.anchor - timedelta(days=1)]),
            dict(cursor_in="a", cursor_out="b", receipt="raw:2",
                 receipt_verified=True, timestamps_verified=True,
                 published_at=[self.anchor - timedelta(days=2)]),
            dict(cursor_in="b", cursor_out="a", receipt="raw:3",
                 receipt_verified=True, timestamps_verified=True,
                 published_at=[cutoff - timedelta(seconds=1)]),
        ]
        result = assess_cutoff_evidence(
            anchor=self.anchor, cutoff=cutoff, pages=pages, rooted=True)
        self.assertEqual(result["reason"], "cursor_cycle")


    def test_duplicate_receipt_cannot_certify_cutoff(self):
        cutoff = self.anchor - timedelta(days=365)
        pages = [
            dict(cursor_in="root", cursor_out="a", receipt="same",
                 receipt_verified=True, timestamps_verified=True,
                 published_at=[self.anchor - timedelta(days=1)]),
            dict(cursor_in="a", cursor_out="b", receipt="same",
                 receipt_verified=True, timestamps_verified=True,
                 published_at=[cutoff - timedelta(seconds=1)]),
        ]
        self.assertEqual(assess_cutoff_evidence(
            anchor=self.anchor, cutoff=cutoff, pages=pages,
            rooted=True)["reason"], "duplicate_receipt")

    def test_invalid_future_publication_fails_closed(self):
        self.assertEqual(self.check(
            365, -1, published_at=[self.anchor + timedelta(seconds=1)]
        )["reason"], "future_publication")


if __name__ == "__main__":
    unittest.main()
