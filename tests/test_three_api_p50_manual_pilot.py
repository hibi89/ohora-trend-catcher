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

    def test_injected_graph_response_commits_p50_raw_and_capture(self):
        """Exercise real P50 serial ports with synthetic HTTP, never a socket."""
        import sqlite3
        from datetime import datetime, timedelta, timezone
        from unittest.mock import patch
        from three_api_credentials import CredentialReference
        from three_api_graph_http import GraphHTTPClient
        from three_api_live_lane_router import ThreeLaneMetaTransport
        from three_api_transport import MetaTransport
        from test_three_api_serial_bootstrap import MockResponse

        calls = []
        lanes = {}
        env = {"P6_META_GRAPH_VERSION": "v26.0"}
        for lane, caller in (("A", "1001"), ("B", "1002"), ("C", "1003")):
            env[f"P6_META_TOKEN_{lane}"] = f"synthetic-token-{lane}"
            env[f"P6_META_IG_ID_{lane}"] = caller
            ref = CredentialReference(lane, f"P6_META_TOKEN_{lane}",
                                      f"P6_META_IG_ID_{lane}")

            def fake_sender(request, timeout, *, lane=lane):
                calls.append(lane)
                response = MockResponse()
                response.payload["business_discovery"]["username"] = "nail_us_1"
                response.payload["business_discovery"]["media"]["data"][0]["username"] = "nail_us_1"
                return response

            client = GraphHTTPClient(sender=fake_sender, approved=True)
            lanes[lane] = MetaTransport(client=client, credentials=ref,
                                        caller_ig_id=caller,
                                        token=env[f"P6_META_TOKEN_{lane}"])

        transport = ThreeLaneMetaTransport(lanes)
        with patch("three_api_p50_manual_pilot._transports", return_value=transport):
            result = run_p50(self.source, self.workspace, live=True,
                             approved=True, environment=env, max_pages=1)

        self.assertEqual(result["status"], "page_committed")
        self.assertEqual(result["committed_pages"], 1)
        self.assertEqual(result["attempted_steps"], 1)
        self.assertEqual(calls, ["A"])
        day_kst = datetime.now(timezone(timedelta(hours=9))).date().isoformat()
        with sqlite3.connect(self.workspace / "p50_raw.sqlite") as raw:
            self.assertEqual(raw.execute("SELECT COUNT(*) FROM accounts").fetchone()[0], 50)
            self.assertEqual(raw.execute("SELECT COUNT(*) FROM post_observations").fetchone()[0], 1)
            self.assertEqual(raw.execute(
                "SELECT COUNT(*) FROM three_api_contract_receipts").fetchone()[0], 1)
        with sqlite3.connect(self.workspace / day_kst / "coordinator.sqlite") as control:
            self.assertEqual(control.execute("SELECT COUNT(*) FROM jobs").fetchone()[0], 150)
            self.assertEqual(control.execute(
                "SELECT state FROM jobs WHERE account_ordinal=1 AND page=1"
            ).fetchone()[0], "done")
        with sqlite3.connect(self.workspace / day_kst / "capture.sqlite") as capture:
            self.assertEqual(capture.execute("SELECT COUNT(*) FROM intents").fetchone()[0], 1)
        self.assertNotIn("synthetic-token", str(result))
        # Cross-component contract: the actual P50 RAW schema must be readable
        # by the downstream n8n export, not merely a synthetic SQLite fixture.
        from three_api_sheets_export import export_observations
        exported = export_observations(self.workspace / "p50_raw.sqlite")
        self.assertEqual(len(exported["rows"]), 1)
        row = exported["rows"][0]
        self.assertEqual(row["country"], "US")
        self.assertEqual(row["account_key"], "P50_US_001")
        self.assertTrue(row["post_id"])
        self.assertEqual(row["collection_batch"], "three-api-p50-" +
                         day_kst.replace("-", ""))
        self.assertTrue(row["observation_key"].endswith(":" + row["post_id"]))
        self.assertEqual(exported["next_after_id"], row["observation_id"])
        self.assertEqual(export_observations(
            self.workspace / "p50_raw.sqlite",
            after_id=exported["next_after_id"])["rows"], [])


    def test_three_independent_lanes_commit_three_distinct_accounts(self):
        """One bounded serial cycle uses A, B and C, each with verified RAW."""
        import sqlite3
        from datetime import datetime, timedelta, timezone
        from unittest.mock import patch
        from three_api_credentials import CredentialReference
        from three_api_graph_http import GraphHTTPClient
        from three_api_live_lane_router import ThreeLaneMetaTransport
        from three_api_transport import MetaTransport
        from test_three_api_serial_bootstrap import MockResponse

        calls = []
        lanes = {}
        env = {"P6_META_GRAPH_VERSION": "v26.0"}
        account_by_lane = {"A": (1, "nail_us_1"),
                           "B": (2, "nail_us_2"),
                           "C": (3, "nail_us_3")}
        for lane, caller in (("A", "1001"), ("B", "1002"), ("C", "1003")):
            env[f"P6_META_TOKEN_{lane}"] = "test-token-" + lane
            env[f"P6_META_IG_ID_{lane}"] = caller
            ref = CredentialReference(lane, f"P6_META_TOKEN_{lane}",
                                      f"P6_META_IG_ID_{lane}")

            def sender(request, timeout, *, lane=lane):
                calls.append(lane)
                index, username = account_by_lane[lane]
                response = MockResponse()
                response.payload["business_discovery"]["username"] = username
                response.payload["business_discovery"]["media"]["data"][0]["username"] = username
                response.payload["business_discovery"]["media"]["data"][0]["id"] = str(1234567890 + index)
                return response

            lanes[lane] = MetaTransport(
                client=GraphHTTPClient(sender=sender, approved=True),
                credentials=ref, caller_ig_id=caller,
                token=env[f"P6_META_TOKEN_{lane}"])

        with patch("three_api_p50_manual_pilot._transports",
                   return_value=ThreeLaneMetaTransport(lanes)):
            result = run_p50(self.source, self.workspace, live=True,
                             approved=True, environment=env, max_pages=3)
        self.assertEqual(result["status"], "page_committed")
        self.assertEqual(result["committed_pages"], 3)
        self.assertEqual(result["attempted_steps"], 3)
        self.assertEqual(calls, ["A", "B", "C"])
        kst_day = datetime.now(timezone(timedelta(hours=9))).date().isoformat()
        with sqlite3.connect(self.workspace / "p50_raw.sqlite") as raw:
            self.assertEqual(raw.execute(
                "SELECT COUNT(*) FROM three_api_contract_receipts").fetchone()[0], 3)
            self.assertEqual(raw.execute(
                "SELECT COUNT(*) FROM post_observations").fetchone()[0], 3)
        with sqlite3.connect(self.workspace / kst_day / "coordinator.sqlite") as db:
            self.assertEqual(dict(db.execute(
                "SELECT lane,COUNT(*) FROM jobs WHERE state='done' GROUP BY lane"
            ).fetchall()), {"A": 1, "B": 1, "C": 1})

    def test_page_limit_rejects_unbounded(self):
        for invalid in (0, 151, True):
            with self.subTest(invalid=invalid):
                with self.assertRaises(ValueError):
                    run_p50(self.source, self.workspace, max_pages=invalid)


if __name__ == "__main__":
    unittest.main()
