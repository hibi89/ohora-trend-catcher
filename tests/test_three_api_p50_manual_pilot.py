"""P50 bounded entrypoint regression: offline must never request credentials/HTTP."""
import csv
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "raw_v2"))
from three_api_p50_manual_pilot import run_p50


class P50ManualPilotTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        root = Path(self.temp.name)
        self.source = root / "p50.csv"
        self.workspace = root / "p50"
        with self.source.open("w", encoding="utf-8", newline="") as f:
            w = csv.DictWriter(f, fieldnames=["account_key", "username", "country"])
            w.writeheader()
            for country, n in (("US", 18), ("KR", 16), ("JP", 16)):
                for i in range(1, n + 1):
                    w.writerow({"account_key": f"P50_{country}_{i:03d}",
                                "username": f"nail_{country.lower()}_{i}",
                                "country": country})

    def test_offline_no_workspace_created(self):
        result = run_p50(self.source, self.workspace)
        self.assertEqual(result["accounts"], 50)
        self.assertFalse(result["live_meta_called"])
        self.assertFalse(self.workspace.exists())

    def test_live_requires_separate_approval(self):
        with self.assertRaises(ValueError):
            run_p50(self.source, self.workspace, live=True, approved=False)
        self.assertFalse(self.workspace.exists())

    def test_page_limit_rejects_unbounded(self):
        for invalid in (0, 151, True):
            with self.subTest(invalid=invalid):
                with self.assertRaises(ValueError):
                    run_p50(self.source, self.workspace, max_pages=invalid)


if __name__ == "__main__":
    unittest.main()
