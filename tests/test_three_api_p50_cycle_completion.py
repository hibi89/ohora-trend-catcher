"""Offline P50 latest completion signalling for n8n; no HTTP or real DB."""
from contextlib import nullcontext, redirect_stdout
from io import StringIO
import json
from pathlib import Path
import sys
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "raw_v2"))
from three_api_p50_cycle import main


class P50LatestCompletionTests(unittest.TestCase):
    def invoke(self, status, *, complete, max_pages=150):
        output = StringIO()
        with (patch("three_api_p50_cycle.run_p50",
                    return_value={"status": status, "committed_pages": 0,
                                  "recovered_pages": 0, "account_failures": 0,
                                  "attempted_steps": 1}) as collector,
              patch("three_api_p50_cycle.serial_tick_lock",
                    return_value=nullcontext()),
              patch("three_api_p50_cycle._needs_latest",
                    return_value=not complete) as pending,
              redirect_stdout(output)):
            code = main(["latest", "--workspace", "unused-p50", "--source",
                         "unused-roster.csv", "--live", "--confirm-owner-approval",
                         "--max-pages", str(max_pages)])
        collector.assert_called_once()
        pending.assert_called_once()
        return code, json.loads(output.getvalue())

    def test_no_ready_work_is_not_daily_success(self):
        code, data = self.invoke("no_ready_work", complete=False)
        self.assertEqual(code, 2)
        self.assertFalse(data["collection_complete"])

    def test_one_committed_page_is_valid_bounded_progress(self):
        code, data = self.invoke("page_committed", complete=False, max_pages=1)
        self.assertEqual(code, 0)
        self.assertFalse(data["collection_complete"])

    def test_fully_finished_day_is_success(self):
        code, data = self.invoke("no_ready_work", complete=True)
        self.assertEqual(code, 0)
        self.assertTrue(data["collection_complete"])

    def test_busy_serial_dispatch_cannot_mark_incomplete_day_finished(self):
        code, data = self.invoke("serial_busy", complete=False)
        self.assertEqual(code, 2)
        self.assertFalse(data["collection_complete"])


if __name__ == "__main__":
    unittest.main()
