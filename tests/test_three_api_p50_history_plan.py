"""P50 history planning is read-only and gated on latest completion."""
from datetime import date
from pathlib import Path
import sys
import tempfile
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "raw_v2"))
from three_api_p50_history_plan import plan_next_history_page
from three_api_p50_history_store import P50HistoryStore


class P50HistoryPlanTests(unittest.TestCase):
    def test_only_latest_complete_accounts_eligible(self):
        keys = [f"P50_US_{i:03d}" for i in range(1, 51)]
        with tempfile.TemporaryDirectory() as tmp:
            with P50HistoryStore(Path(tmp) / "p50" / "history.sqlite") as store:
                store.initialize(keys, date(2025, 10, 22))
                result = plan_next_history_page(
                    store, keys, latest_complete_keys={keys[1], keys[3]},
                    max_pages=1)
                self.assertEqual(len(result["jobs"]), 0)  # No verified tail anchor yet
                self.assertEqual(result["network_requests"], 0)
                self.assertEqual(result["database_writes"], 0)

    def test_only_persisted_continuation_cursor_is_planned(self):
        keys = [f"P50_US_{i:03d}" for i in range(1, 51)]
        with tempfile.TemporaryDirectory() as tmp:
            with P50HistoryStore(Path(tmp) / "p50" / "history.sqlite") as store:
                store.initialize(keys, date(2025, 10, 22))
                store.db.execute(
                    "UPDATE history_accounts SET state='continuation_ready',cursor='saved-tail' "
                    "WHERE account_key=?", (keys[1],))
                result = plan_next_history_page(
                    store, keys, latest_complete_keys={keys[1]}, max_pages=1)
                self.assertEqual(result["jobs"][0]["cursor"], "saved-tail")
                self.assertEqual(result["network_requests"], 0)

    def test_unknown_completion_rejected(self):
        keys = [f"P50_US_{i:03d}" for i in range(1, 51)]
        with tempfile.TemporaryDirectory() as tmp:
            with P50HistoryStore(Path(tmp) / "p50" / "history.sqlite") as store:
                store.initialize(keys, date(2025, 10, 22))
                with self.assertRaises(ValueError):
                    plan_next_history_page(store, keys,
                                           latest_complete_keys={"P50_UNKNOWN"})


if __name__ == "__main__":
    unittest.main()
