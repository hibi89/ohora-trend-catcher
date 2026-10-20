"""Synthetic history cutoff evidence tests. No Meta or operating DB."""
import unittest
from datetime import datetime, timedelta, timezone

from raw_v2.history_cutoff_evidence import assess_cutoff_evidence


class CutoffAssessorTests(unittest.TestCase):
    def setUp(self):
        self.anchor = datetime(2026, 10, 20, tzinfo=timezone.utc)

    def check(self, days, offset_microseconds, **changes):
        cutoff = self.anchor - timedelta(days=days)
        page = dict(cursor_in="root", cursor_out="next", receipt="raw:1",
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
            base = dict(cursor_in="root", cursor_out=None, receipt="raw:1",
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
            dict(cursor_in="root", cursor_out="cursor2", receipt="raw:1",
                 published_at=[self.anchor - timedelta(days=1)]),
            dict(cursor_in="wrong", cursor_out="cursor3", receipt="raw:2",
                 published_at=[cutoff - timedelta(seconds=1)]),
        ]
        self.assertEqual(assess_cutoff_evidence(
            anchor=self.anchor, cutoff=cutoff, pages=pages,
            rooted=True)["reason"], "cursor_gap")


if __name__ == "__main__":
    unittest.main()
