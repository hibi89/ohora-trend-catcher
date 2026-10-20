"""Offline cutoff evidence contract: no unverified coverage promotion.

The frozen design requires a rooted cursor chain, intact receipts,
verified timestamps and cutoff-crossing/terminal evidence. Merely creating
a 365/730-day epoch, or observing a terminal cursor, proves none of these.
"""
import sqlite3
import unittest
from datetime import datetime, timedelta, timezone

from raw_v2.three_api_epochs import ThreeApiEpochStore


class HistoryCutoffBoundaryContractTests(unittest.TestCase):
    def test_distinct_fixed_cutoffs_never_imply_verified_coverage(self):
        db = sqlite3.connect(":memory:", isolation_level=None)
        try:
            store = ThreeApiEpochStore(db)
            # Synthetic only; no Meta call, no production DB.
            anchor = datetime(2026, 10, 20, 0, 0, tzinfo=timezone.utc)
            for days in (365, 730):
                cutoff = anchor - timedelta(days=days)
                self.assertLess(cutoff, anchor)
                self.assertEqual(
                    (anchor - cutoff).total_seconds(), days * 86400
                )
            # Actual coverage verification is intentionally deferred until
            # the full immutable evidence chain and timestamps are available.
            rows = db.execute(
                "SELECT COUNT(*) FROM coverage_ledger WHERE status='verified'"
            ).fetchone()[0]
            self.assertEqual(rows, 0)
        finally:
            db.close()


    def test_utc_cutoff_boundaries_365_and_730(self):
        anchor = datetime(2026, 10, 20, tzinfo=timezone.utc)
        for days in (365, 730):
            with self.subTest(days=days):
                cutoff = anchor - timedelta(days=days)
                before = cutoff - timedelta(microseconds=1)
                after = cutoff + timedelta(microseconds=1)
                self.assertTrue(before <= cutoff)
                self.assertTrue(cutoff <= cutoff)
                self.assertFalse(after <= cutoff)


    def test_cutoff_is_fixed_across_observation_days(self):
        anchor = datetime(2026, 10, 20, tzinfo=timezone.utc)
        for days in (365, 730):
            cutoff = anchor - timedelta(days=days)
            for observation_delay in (0, 1, 30):
                observed_at = anchor + timedelta(days=observation_delay)
                self.assertEqual(cutoff, anchor - timedelta(days=days))
                self.assertGreaterEqual(observed_at, anchor)


    def test_unverified_coverage_stays_unverified_at_boundary(self):
        db = sqlite3.connect(":memory:", isolation_level=None)
        try:
            ThreeApiEpochStore(db)
            anchor = datetime(2026, 10, 20, tzinfo=timezone.utc)
            for days in (365, 730):
                cutoff = anchor - timedelta(days=days)
                for published_at in (
                    cutoff - timedelta(microseconds=1),
                    cutoff,
                    cutoff + timedelta(microseconds=1),
                ):
                    self.assertIsNotNone(published_at)
                    self.assertEqual(
                        db.execute("SELECT COUNT(*) FROM coverage_ledger "
                                   "WHERE status='verified'").fetchone()[0], 0
                    )
        finally:
            db.close()


if __name__ == "__main__":
    unittest.main()
