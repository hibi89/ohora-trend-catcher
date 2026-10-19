"""Offline coordinator regression tests; no Meta access."""
import importlib.util
from datetime import datetime, timedelta, timezone
from pathlib import Path
import tempfile
import unittest

SOURCE = Path(__file__).resolve().parents[1] / "raw_v2" / "three_api_coordinator.py"
spec = importlib.util.spec_from_file_location("three_api_coordinator", SOURCE)
mod = importlib.util.module_from_spec(spec)
spec.loader.exec_module(mod)


class CoordinatorTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.c = mod.Coordinator(Path(self.tmp.name) / "coordinator.sqlite")
        self.addCleanup(self.c.db.close)
        self.c.seed()

    def test_all_accounts_three_pages_and_idempotent_seed(self):
        self.c.seed()
        self.assertEqual(self.c.summary()["jobs"], {"pending": 882})
        self.assertEqual(self.c.db.execute(
            "SELECT COUNT(*) FROM jobs WHERE page=1").fetchone()[0], 294)
        for ordinal in range(1, 295):
            pages = self.c.db.execute(
                "SELECT page FROM jobs WHERE account_ordinal=? ORDER BY page",
                (ordinal,)).fetchall()
            self.assertEqual([r[0] for r in pages], [1, 2, 3])

    def test_daily_page_continuation_uses_allowed_catchup_phase(self):
        """Page 2/3 are daily continuation, not paused historical backfill."""
        rows = self.c.db.execute(
            "SELECT page, phase, COUNT(*) FROM jobs GROUP BY page, phase "
            "ORDER BY page"
        ).fetchall()
        self.assertEqual(
            [(row[0], row[1], row[2]) for row in rows],
            [(1, "front", 294), (2, "catchup", 294), (3, "catchup", 294)],
        )

    def test_lease_exclusive_and_stale_completion(self):
        t = datetime(2026, 10, 19, tzinfo=timezone.utc)
        first = self.c.lease("A", t)
        self.assertEqual((first["account_ordinal"],first["page"]), (1, 1))
        other = self.c.lease("A", t)
        self.assertNotEqual(first["id"], other["id"])
        later = self.c.lease("A", t + timedelta(minutes=16))
        self.assertEqual(later["id"], first["id"])
        self.assertFalse(self.c.complete(first["id"],first["lease_id"]))
        self.assertTrue(self.c.complete(later["id"],later["lease_id"]))
        self.assertFalse(self.c.complete(later["id"],later["lease_id"]))

    def test_page_dependency(self):
        t = datetime(2026, 10, 19, tzinfo=timezone.utc)
        self.assertIsNone(self.c.lease("C",t))
        first = self.c.lease("A",t)
        self.assertTrue(self.c.complete(first["id"],first["lease_id"]))
        second = self.c.lease("C",t)
        self.assertEqual((second["account_ordinal"],second["page"]), (1,2))

    def test_evidence_mode_blocks_page_two_without_cursor(self):
        """A completed page alone cannot authorize a later-page lease."""
        t = datetime(2026, 10, 19, tzinfo=timezone.utc)
        self.c.db.execute(
            "CREATE TABLE page_evidence (account_ordinal INTEGER, "
            "page INTEGER, terminal INTEGER, response_cursor TEXT)"
        )
        first = self.c.lease("A", t)
        self.assertEqual((first["account_ordinal"], first["page"]), (1, 1))
        with self.assertRaises(RuntimeError):
            self.c.complete(first["id"], first["lease_id"])
        self.c.db.execute(
            "UPDATE jobs SET state='done' WHERE id=?", (first["id"],)
        )
        self.assertIsNone(self.c.lease("C", t))
        self.c.db.execute(
            "INSERT INTO page_evidence VALUES (1, 1, 0, NULL)"
        )
        self.assertIsNone(self.c.lease("C", t))
        self.c.db.execute(
            "UPDATE page_evidence SET response_cursor='opaque-next'"
        )
        second = self.c.lease("C", t)
        self.assertEqual((second["account_ordinal"], second["page"]), (1, 2))

    def test_expired_lease_can_be_reissued_without_outcome_reconciliation(self):
        """Characterize unsafe legacy behavior; adapter must refuse resend."""
        t = datetime(2026, 10, 19, tzinfo=timezone.utc)
        first = self.c.lease("A", t)
        reissued = self.c.lease("A", t + timedelta(minutes=16))
        self.assertEqual(first["id"], reissued["id"])
        self.assertNotEqual(first["lease_id"], reissued["lease_id"])

    def test_evidence_mode_does_not_reissue_expired_lease(self):
        """An expired lease may have already sent an unknown-outcome request."""
        t = datetime(2026, 10, 19, tzinfo=timezone.utc)
        self.c.db.execute(
            "CREATE TABLE page_evidence (account_ordinal INTEGER, "
            "page INTEGER, terminal INTEGER, response_cursor TEXT)"
        )
        first = self.c.lease("A", t)
        next_job = self.c.lease("A", t + timedelta(minutes=16))
        self.assertIsNotNone(next_job)
        self.assertNotEqual(first["id"], next_job["id"])
        saved = self.c.db.execute(
            "SELECT state,lease_id FROM jobs WHERE id=?", (first["id"],)
        ).fetchone()
        self.assertEqual(saved["state"], "leased")
        self.assertEqual(saved["lease_id"], first["lease_id"])

    def test_usage_halts_only_affected_lane(self):
        t = datetime(2026, 10, 19, tzinfo=timezone.utc)
        self.assertFalse(self.c.observe_usage("A",84,t))
        self.assertTrue(self.c.observe_usage("A",85,t))
        self.assertIsNone(self.c.lease("A",t))
        self.assertIsNotNone(self.c.lease("B",t))
        self.assertFalse(self.c.reset_after_verified_cooldown("A",t))
        self.assertTrue(self.c.reset_after_verified_cooldown(
            "A",t + timedelta(minutes=65)))

    def test_missing_usage_fails_closed(self):
        self.assertTrue(self.c.observe_usage("B",None))
        self.assertEqual(self.c.db.execute(
            "SELECT reason FROM lanes WHERE lane='B'").fetchone()[0],
            "missing_usage")

    def test_refuses_live_database_name(self):
        with self.assertRaises(ValueError):
            mod.Coordinator(Path(self.tmp.name) / "collection.sqlite")


if __name__ == "__main__":
    unittest.main()
