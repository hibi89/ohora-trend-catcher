"""Offline P50 coordinator tests; no credentials or Meta network."""
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "raw_v2"))
from three_api_p50_coordinator import P50Coordinator


class P50CoordinatorTests(unittest.TestCase):
    def test_seed_exactly_150_jobs_and_idempotent(self):
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / "p50" / "coordinator.sqlite"
            coordinator = P50Coordinator(path)
            try:
                coordinator.seed()
                coordinator.seed()
                jobs = coordinator.db.execute(
                    "SELECT account_ordinal,page,lane,phase FROM jobs ORDER BY account_ordinal,page"
                ).fetchall()
                self.assertEqual(len(jobs), 150)
                self.assertEqual(tuple(jobs[0]), (1, 1, "A", "front"))
                self.assertEqual(tuple(jobs[1]), (1, 2, "C", "catchup"))
                self.assertEqual(tuple(jobs[2]), (1, 3, "B", "catchup"))
                self.assertEqual(tuple(jobs[3]), (2, 1, "B", "front"))
                self.assertEqual(tuple(jobs[4]), (2, 2, "A", "catchup"))
                self.assertEqual(tuple(jobs[6]), (3, 1, "C", "front"))
                self.assertEqual(tuple(jobs[-1]), (50, 3, "C", "catchup"))
                self.assertEqual(
                    dict(coordinator.db.execute(
                        "SELECT lane,COUNT(*) FROM jobs WHERE page=1 GROUP BY lane"
                    ).fetchall()),
                    {"A": 17, "B": 17, "C": 16},
                )
                self.assertEqual(
                    dict(coordinator.db.execute(
                        "SELECT lane,COUNT(*) FROM jobs GROUP BY lane"
                    ).fetchall()),
                    {"A": 50, "B": 50, "C": 50},
                )
                self.assertEqual(coordinator.db.execute(
                    "SELECT COUNT(*) FROM jobs WHERE account_ordinal > 50"
                ).fetchone()[0], 0)
            finally:
                coordinator.db.close()

    def test_reject_shared_workspace(self):
        with tempfile.TemporaryDirectory() as temp:
            with self.assertRaises(ValueError):
                P50Coordinator(Path(temp) / "coordinator.sqlite")


if __name__ == "__main__":
    unittest.main()
