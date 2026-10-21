"""Offline regression tests for official 294-account roster preflight."""
import unittest

from three_api_roster_preflight import validate_official_roster, RosterPreflightError


def roster():
    return [{"ordinal": n, "key": f"KR_{n:03}", "username": f"nail_{n}"}
            for n in range(1, 295)]


class RosterPreflightTests(unittest.TestCase):
    def test_valid(self):
        self.assertTrue(validate_official_roster(roster()))

    def test_incomplete(self):
        with self.assertRaises(RosterPreflightError):
            validate_official_roster(roster()[:-1])

    def test_duplicate_key(self):
        rows = roster()
        rows[1]["key"] = rows[0]["key"]
        with self.assertRaises(RosterPreflightError):
            validate_official_roster(rows)

    def test_case_insensitive_duplicate_username(self):
        rows = roster()
        rows[1]["username"] = "NAIL_1"
        with self.assertRaises(RosterPreflightError):
            validate_official_roster(rows)

    def test_reordered(self):
        rows = roster()
        rows[1]["ordinal"] = 3
        with self.assertRaises(RosterPreflightError):
            validate_official_roster(rows)

    def test_boolean_ordinal_rejected(self):
        rows = roster()
        rows[0]["ordinal"] = True
        with self.assertRaises(RosterPreflightError):
            validate_official_roster(rows)

    def test_blank_key_rejected(self):
        rows = roster()
        rows[0]["key"] = "   "
        with self.assertRaises(RosterPreflightError):
            validate_official_roster(rows)

    def test_missing_identity(self):
        rows = roster()
        rows[0]["username"] = ""
        with self.assertRaises(RosterPreflightError):
            validate_official_roster(rows)


if __name__ == "__main__":
    unittest.main()
