"""P50 RAW bootstrap smoke test: offline and isolated."""
import csv
from contextlib import closing
from datetime import datetime, timedelta, timezone
import sys
import tempfile
import sqlite3
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "raw_v2"))
from three_api_p50_preflight import initialize_isolated_workspace


class P50WorkspaceTests(unittest.TestCase):
    def test_create_only_and_exact_50(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            source = root / "accounts.csv"
            with source.open("w", encoding="utf-8", newline="") as f:
                w = csv.DictWriter(f, fieldnames=["account_key", "username", "country"])
                w.writeheader()
                for country, n in (("US", 18), ("KR", 16), ("JP", 16)):
                    for i in range(1, n + 1):
                        w.writerow({"account_key": f"P50_{country}_{i:03d}",
                                    "username": f"nail_{country.lower()}_{i}", "country": country})
            workspace = root / "p50"
            result = initialize_isolated_workspace(source, workspace)
            self.assertEqual(result["planned_pages"], 150)
            with closing(sqlite3.connect(workspace / "p50_raw.sqlite")) as db:
                self.assertEqual(db.execute("SELECT COUNT(*) FROM accounts").fetchone()[0], 50)
            day = datetime.now(timezone(timedelta(hours=9))).date().isoformat()
            with closing(sqlite3.connect(workspace / day / "coordinator.sqlite")) as db:
                self.assertEqual(db.execute("SELECT COUNT(*) FROM jobs").fetchone()[0], 150)
            with self.assertRaises(ValueError):
                initialize_isolated_workspace(source, workspace)


if __name__ == "__main__":
    unittest.main()
