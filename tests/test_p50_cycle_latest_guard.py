"""Offline regression: incomplete latest cannot dispatch P50 history."""
import io
import json
import sys
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "raw_v2"))
import three_api_p50_cycle as cycle


class P50CycleSafetyTests(unittest.TestCase):
    def test_history_not_dispatched_if_latest_still_incomplete(self):
        latest = {"status": "quota_hold", "committed_pages": 0}
        with patch.object(cycle, "_needs_latest", side_effect=[True, True]), \
             patch.object(cycle, "run_p50", return_value=latest), \
             patch.object(cycle, "run_history") as history, \
             patch.object(cycle, "serial_tick_lock") as lock, \
             patch.object(cycle, "load_three_api_environment", return_value={}), \
             patch("sys.stdout", new_callable=io.StringIO) as out:
            lock.return_value.__enter__.return_value = None
            result = cycle.main(["history", "--live", "--confirm-owner-approval",
                                 "--credentials-dir", "/synthetic",
                                 "--workspace", "/synthetic/p50"])
        self.assertEqual(result, 2)
        history.assert_not_called()
        self.assertEqual(json.loads(out.getvalue())["status"], "latest_incomplete")


    def test_completed_latest_dispatches_history_without_recollecting(self):
        with patch.object(cycle, "_needs_latest", return_value=False), \
             patch.object(cycle, "run_p50") as latest, \
             patch.object(cycle, "run_history", return_value={"status": "quota_hold"}) as history, \
             patch.object(cycle, "load_three_api_environment", return_value={}), \
             patch("sys.stdout", new_callable=io.StringIO) as out:
            result = cycle.main(["history", "--live", "--confirm-owner-approval",
                                 "--credentials-dir", "/synthetic",
                                 "--workspace", "/synthetic/p50"])
        self.assertEqual(result, 0)
        latest.assert_not_called()
        history.assert_called_once()
        self.assertEqual(json.loads(out.getvalue())["status"], "quota_hold")


    def test_latest_gate_rejects_wrong_workspace_before_db_open(self):
        with self.assertRaisesRegex(ValueError, "P50 workspace"):
            cycle._needs_latest("/synthetic/not-p50")

    def test_latest_gate_rejects_symlink_root_before_db_open(self):
        with patch.object(cycle.Path, "is_symlink", return_value=True):
            with self.assertRaisesRegex(ValueError, "P50 workspace"):
                cycle._needs_latest("/synthetic/p50")


if __name__ == "__main__":
    unittest.main()
