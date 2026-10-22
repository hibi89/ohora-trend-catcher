"""Offline regression for the isolated 50-account roster."""
import csv
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "raw_v2"))
from three_api_p50_roster import PilotRosterError, load_p50_roster, validate_p50_roster


class P50RosterTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.path = Path(self.temp.name) / "p50.csv"
        self.rows = [
            {"account_key": f"P50_{country}_{i:03d}",
             "username": f"test_{country.lower()}_{i}",
             "country": country}
            for country, n in (("US", 18), ("KR", 16), ("JP", 16))
            for i in range(1, n + 1)
        ]

    def write_rows(self):
        with self.path.open("w", newline="", encoding="utf-8") as f:
            writer = csv.DictWriter(f, fieldnames=["account_key", "username", "country"])
            writer.writeheader()
            writer.writerows(self.rows)

    def test_valid_roster(self):
        self.write_rows()
        accounts, digest = load_p50_roster(self.path)
        self.assertEqual(len(accounts), 50)
        self.assertEqual(len(digest), 64)
        self.assertTrue(validate_p50_roster(accounts))

    def test_reject_duplicate_username(self):
        self.rows[1]["username"] = self.rows[0]["username"].upper()
        self.write_rows()
        with self.assertRaises(PilotRosterError):
            load_p50_roster(self.path)

    def test_reject_wrong_country_counts(self):
        self.rows[-1]["country"] = "US"
        self.rows[-1]["account_key"] = "P50_US_099"
        self.write_rows()
        with self.assertRaises(PilotRosterError):
            load_p50_roster(self.path)

    def test_reject_missing_row(self):
        self.rows.pop()
        self.write_rows()
        with self.assertRaises(PilotRosterError):
            load_p50_roster(self.path)

    def test_reject_bad_order(self):
        self.write_rows()
        accounts, _ = load_p50_roster(self.path)
        accounts[0]["ordinal"] = 2
        with self.assertRaises(PilotRosterError):
            validate_p50_roster(accounts)


if __name__ == "__main__":
    unittest.main()
