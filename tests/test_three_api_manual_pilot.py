"""Offline smoke tests for manual single-page P6 runner.

All Graph responses come from an in-memory sender. Test authorization does
NOT grant permission for real Meta HTTP. No production database is opened.
"""
import csv
from pathlib import Path
import sqlite3
import sys
import tempfile
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "raw_v2"))
sys.path.insert(0, str(ROOT / "tests"))

from three_api_credentials import CredentialReference
from three_api_graph_http import GraphHTTPClient
from three_api_live_lane_router import ThreeLaneMetaTransport
from three_api_coordinator import Coordinator
from three_api_manual_pilot import (
    PilotConfigurationError, run_manual_pilot, read_pilot_status,
    read_pilot_failures, main, _inherit_latest_lane_gates,
)
from three_api_transport import MetaTransport
from test_three_api_serial_bootstrap import MockResponse


class ManualPilotTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.csv = self.root / "account_list.csv"
        with self.csv.open("w", newline="", encoding="utf-8") as fp:
            writer = csv.writer(fp)
            writer.writerow(("account_key", "username", "country"))
            for country, count in (("KR", 100), ("US", 101), ("JP", 93)):
                for i in range(1, count + 1):
                    ordinal = ({"KR": 0, "US": 100, "JP": 201}[country] + i)
                    writer.writerow((
                        f"{country}_{i:03d}", f"nail_{ordinal}", country,
                    ))
        self.workspace = self.root / "private-pilot"

    def test_default_only_checks_official_roster_without_creating_db(self):
        with patch.dict("os.environ", {}, clear=True):
            result = run_manual_pilot(self.csv, self.workspace)
        self.assertEqual(result["official_accounts"], 294)
        self.assertEqual(result["post_limit"], 4)
        self.assertFalse(result["live_meta_called"])
        self.assertFalse(self.workspace.exists())

    def test_separate_live_approval_required_before_any_write(self):
        with self.assertRaisesRegex(PilotConfigurationError, "approval"):
            run_manual_pilot(self.csv, self.workspace, live=True)
        self.assertFalse(self.workspace.exists())

    def test_absent_credentials_rejected_without_creating_databases(self):
        with self.assertRaisesRegex(PilotConfigurationError, "credentials"):
            run_manual_pilot(
                self.csv, self.workspace, live=True,
                confirm_owner_approval=True, environment={},
            )
        self.assertFalse(self.workspace.exists())

    def test_one_synthetic_graph_response_persists_in_real_raw_v2_schema(self):
        calls = []
        lanes = {}
        environment = {"P6_META_GRAPH_VERSION": "v26.0"}
        for lane, caller in (("A", "1001"), ("B", "1002"), ("C", "1003")):
            environment[f"P6_META_TOKEN_{lane}"] = f"synthetic-token-{lane}"
            environment[f"P6_META_IG_ID_{lane}"] = caller
            ref = CredentialReference(lane, f"P6_META_TOKEN_{lane}", f"P6_META_IG_ID_{lane}")
            def fake_sender(req, timeout, *, lane=lane):
                calls.append(lane)
                return MockResponse()
            client = GraphHTTPClient(sender=fake_sender, approved=True)
            lanes[lane] = MetaTransport(
                client=client, credentials=ref, caller_ig_id=caller,
                token=environment[f"P6_META_TOKEN_{lane}"],
            )
        offline_transport = ThreeLaneMetaTransport(lanes)
        with patch("three_api_manual_pilot._transports", return_value=offline_transport):
            result = run_manual_pilot(
                self.csv, self.workspace, live=True,
                confirm_owner_approval=True, environment=environment,
            )
        self.assertEqual(result["status"], "page_committed")
        self.assertEqual(result["committed_pages"], 1)
        self.assertEqual(result["attempted_steps"], 1)
        self.assertEqual(result["new_http_attempt_limit"], 1)
        self.assertIsInstance(result["verified_success_at"], str)
        self.assertEqual(calls, ["A"])
        raw_db = sqlite3.connect(self.workspace / "p6_pilot_raw.sqlite")
        self.addCleanup(raw_db.close)
        self.assertEqual(raw_db.execute("SELECT COUNT(*) FROM accounts").fetchone()[0], 294)
        self.assertEqual(raw_db.execute("SELECT COUNT(*) FROM post_observations").fetchone()[0], 1)
        self.assertEqual(raw_db.execute("SELECT COUNT(*) FROM three_api_contract_receipts").fetchone()[0], 1)
        self.assertNotIn("synthetic-token", str(result))

    def test_invalid_page_bounds_rejected_before_any_io(self):
        for bound in (0, 295, True, "3"):
            with self.subTest(bound=bound):
                with self.assertRaises(PilotConfigurationError):
                    run_manual_pilot(self.csv, self.workspace, max_pages=bound)
        self.assertFalse(self.workspace.exists())

    def test_two_serial_pages_save_two_accounts_with_one_caller(self):
        calls = []
        environment = {"P6_META_GRAPH_VERSION": "v26.0"}
        lanes = {}
        for lane, caller in (("A", "1001"), ("B", "1002"), ("C", "1003")):
            environment[f"P6_META_TOKEN_{lane}"] = f"synthetic-token-{lane}"
            environment[f"P6_META_IG_ID_{lane}"] = caller
            ref = CredentialReference(lane, f"P6_META_TOKEN_{lane}", f"P6_META_IG_ID_{lane}")
            def sender(req, timeout, *, lane=lane):
                calls.append(lane)
                response = MockResponse()
                target = f"nail_{calls.count(lane) if lane == 'A' else 150 + calls.count(lane)}"
                response.payload["business_discovery"]["username"] = target
                response.payload["business_discovery"]["media"]["data"][0]["id"] = str(1234567890 + len(calls))
                response.payload["business_discovery"]["media"]["data"][0]["username"] = target
                return response
            lanes[lane] = MetaTransport(
                client=GraphHTTPClient(sender=sender, approved=True),
                credentials=ref, caller_ig_id=caller,
                token=environment[f"P6_META_TOKEN_{lane}"],
            )
        with patch("three_api_manual_pilot._transports",
                   return_value=ThreeLaneMetaTransport(lanes)):
            result = run_manual_pilot(
                self.csv, self.workspace, live=True,
                confirm_owner_approval=True, environment=environment,
                max_pages=2,
            )
        self.assertEqual(result["committed_pages"], 2)
        self.assertEqual(result["attempted_steps"], 2)
        self.assertEqual(result["new_http_attempt_limit"], 2)
        self.assertEqual(calls, ["A", "B"])
        raw_db = sqlite3.connect(self.workspace / "p6_pilot_raw.sqlite")
        self.addCleanup(raw_db.close)
        self.assertEqual(raw_db.execute(
            "SELECT COUNT(*) FROM post_observations").fetchone()[0], 2)
        self.assertEqual(raw_db.execute(
            "SELECT COUNT(*) FROM three_api_contract_receipts").fetchone()[0], 2)

    def test_second_invocation_resumes_existing_raw_without_duplicate(self):
        calls = []
        environment = {"P6_META_GRAPH_VERSION": "v26.0"}
        lanes = {}
        for lane, caller in (("A", "1001"), ("B", "1002"), ("C", "1003")):
            environment[f"P6_META_TOKEN_{lane}"] = f"synthetic-token-{lane}"
            environment[f"P6_META_IG_ID_{lane}"] = caller
            ref = CredentialReference(lane, f"P6_META_TOKEN_{lane}", f"P6_META_IG_ID_{lane}")
            def sender(req, timeout, *, lane=lane):
                calls.append(lane)
                response = MockResponse()
                target = f"nail_{calls.count(lane) if lane == 'A' else 150 + calls.count(lane)}"
                response.payload["business_discovery"]["username"] = target
                response.payload["business_discovery"]["media"]["data"][0]["id"] = str(1234567890 + len(calls))
                response.payload["business_discovery"]["media"]["data"][0]["username"] = target
                return response
            lanes[lane] = MetaTransport(
                client=GraphHTTPClient(sender=sender, approved=True),
                credentials=ref, caller_ig_id=caller,
                token=environment[f"P6_META_TOKEN_{lane}"],
            )
        with patch("three_api_manual_pilot._transports",
                   return_value=ThreeLaneMetaTransport(lanes)):
            first = run_manual_pilot(
                self.csv, self.workspace, live=True,
                confirm_owner_approval=True, environment=environment,
            )
            second = run_manual_pilot(
                self.csv, self.workspace, live=True,
                confirm_owner_approval=True, environment=environment,
            )
        self.assertEqual(first["committed_pages"], 1)
        self.assertEqual(second["committed_pages"], 1)
        self.assertEqual(calls, ["A", "B"])
        db = sqlite3.connect(self.workspace / "p6_pilot_raw.sqlite")
        self.addCleanup(db.close)
        self.assertEqual(db.execute("SELECT COUNT(*) FROM post_observations").fetchone()[0], 2)
        self.assertEqual(db.execute("SELECT COUNT(*) FROM three_api_contract_receipts").fetchone()[0], 2)

    def test_failure_diagnostic_missing_db_is_read_only(self):
        with self.assertRaises(PilotConfigurationError):
            read_pilot_failures(self.workspace, day="2026-10-21")
        self.assertFalse(self.workspace.exists())

    def test_confirmed_cursor_error_is_persisted_as_resume_required_without_retry(self):
        from email.message import Message
        from io import BytesIO
        from urllib.error import HTTPError
        from datetime import datetime, timezone, timedelta
        from three_api_contract_executor import ContractExecutionError

        environment = {"P6_META_GRAPH_VERSION": "v26.0"}
        lanes = {}
        for lane, caller in (("A", "1001"), ("B", "1002"), ("C", "1003")):
            environment[f"P6_META_TOKEN_{lane}"] = f"synthetic-token-{lane}"
            environment[f"P6_META_IG_ID_{lane}"] = caller
            ref = CredentialReference(lane, f"P6_META_TOKEN_{lane}", f"P6_META_IG_ID_{lane}")
            def sender(request, timeout):
                body = b'{"error":{"code":100,"error_subcode":33,"message":"Invalid after cursor"}}'
                raise HTTPError(request.full_url, 400, "Bad Request", Message(), BytesIO(body))
            lanes[lane] = MetaTransport(
                client=GraphHTTPClient(sender=sender, approved=True),
                credentials=ref, caller_ig_id=caller,
                token=environment[f"P6_META_TOKEN_{lane}"],
            )

        with patch("three_api_manual_pilot._transports",
                   return_value=ThreeLaneMetaTransport(lanes)):
            with self.assertRaises(ContractExecutionError):
                run_manual_pilot(
                    self.csv, self.workspace, live=True,
                    confirm_owner_approval=True, environment=environment,
                    max_pages=1,
                )

        day = datetime.now(timezone(timedelta(hours=9))).date().isoformat()
        diagnostic = read_pilot_failures(self.workspace, day=day)
        self.assertFalse(diagnostic["live_meta_called"])
        self.assertEqual(diagnostic["db_writes"], 0)
        self.assertEqual(len(diagnostic["blocked_jobs"]), 1)
        failure = diagnostic["blocked_jobs"][0]
        self.assertEqual(failure["failure_category"], "cursor_invalid")
        self.assertEqual(failure["recovery_status"], "resume_required")
        self.assertFalse(failure["automatic_retry_allowed"])

    def test_status_missing_stores_never_creates_anything(self):
        with self.assertRaisesRegex(PilotConfigurationError, "directory"):
            read_pilot_status(self.workspace, day="2026-10-21")
        self.assertFalse(self.workspace.exists())

    def test_status_is_read_only_and_reports_lanes_without_meta(self):
        import io
        from contextlib import redirect_stdout
        calls = []
        environment = {"P6_META_GRAPH_VERSION": "v26.0"}
        lanes = {}
        for lane, caller in (("A", "1001"), ("B", "1002"), ("C", "1003")):
            environment[f"P6_META_TOKEN_{lane}"] = f"synthetic-token-{lane}"
            environment[f"P6_META_IG_ID_{lane}"] = caller
            ref = CredentialReference(lane, f"P6_META_TOKEN_{lane}", f"P6_META_IG_ID_{lane}")
            def sender(req, timeout, *, lane=lane):
                calls.append(lane)
                response = MockResponse()
                target = f"nail_{calls.count(lane) if lane == 'A' else 150 + calls.count(lane)}"
                response.payload["business_discovery"]["username"] = target
                response.payload["business_discovery"]["media"]["data"][0]["id"] = str(1234567890 + len(calls))
                response.payload["business_discovery"]["media"]["data"][0]["username"] = target
                return response
            lanes[lane] = MetaTransport(
                client=GraphHTTPClient(sender=sender, approved=True),
                credentials=ref, caller_ig_id=caller,
                token=environment[f"P6_META_TOKEN_{lane}"],
            )
        with patch("three_api_manual_pilot._transports",
                   return_value=ThreeLaneMetaTransport(lanes)):
            run_manual_pilot(self.csv, self.workspace, live=True,
                             confirm_owner_approval=True,
                             environment=environment, max_pages=2)
        self.assertEqual(calls, ["A", "B"])
        from datetime import datetime, timezone, timedelta
        day = datetime.now(timezone(timedelta(hours=9))).date().isoformat()
        control = self.workspace / day / "coordinator.sqlite"
        raw = self.workspace / "p6_pilot_raw.sqlite"
        before = (control.stat().st_mtime_ns, raw.stat().st_mtime_ns)
        with patch("three_api_manual_pilot._transports",
                   side_effect=AssertionError("Status must never contact Meta")):
            status = read_pilot_status(self.workspace, day=day)
            output = io.StringIO()
            with redirect_stdout(output):
                self.assertEqual(main([
                    "--workspace", str(self.workspace), "--status",
                    "--day", day]), 0)
        self.assertEqual(status["mode"], "READ_ONLY_PILOT_STATUS")
        self.assertEqual(status["post_observations"], 2)
        self.assertEqual(status["durable_receipts"], 2)
        self.assertEqual(status["lanes"]["A"]["done"], 1)
        self.assertEqual(status["lanes"]["B"]["done"], 1)
        self.assertEqual(status["lanes"]["C"]["done"], 0)
        self.assertFalse(status["live_meta_called"])
        self.assertEqual((control.stat().st_mtime_ns, raw.stat().st_mtime_ns), before)
        self.assertIn('"READ_ONLY_PILOT_STATUS"', output.getvalue())
        self.assertEqual(calls, ["A", "B"])

    def test_status_rejects_live_flags_and_invalid_date(self):
        with self.assertRaisesRegex(PilotConfigurationError, "directory"):
            read_pilot_status(self.workspace, day="2026-10-21")
        self.workspace.mkdir()
        with self.assertRaisesRegex(PilotConfigurationError, "date"):
            read_pilot_status(self.workspace, day="bad-date")
        with self.assertRaises(SystemExit) as context:
            main(["--workspace", str(self.workspace), "--status", "--live"])
        self.assertEqual(context.exception.code, 2)

    def test_new_daily_coordinator_inherits_prior_holds_and_future_cooldown(self):
        from datetime import datetime, timezone

        prior_day = "2026-10-21"
        today = "2026-10-22"
        now = datetime(2026, 10, 22, 15, 25, tzinfo=timezone.utc)
        prior_dir = self.workspace / prior_day
        prior_dir.mkdir(parents=True)
        prior = Coordinator(prior_dir / "coordinator.sqlite")
        prior.seed()
        prior.db.execute(
            "UPDATE lanes SET halted=1,reason='live_pending_usage',ready_at=? "
            "WHERE lane IN ('B','C')",
            (datetime(2026, 10, 21, 14, 0, tzinfo=timezone.utc).isoformat(),),
        )
        prior.db.execute(
            "UPDATE lanes SET ready_at=? WHERE lane='A'",
            (datetime(2026, 10, 22, 15, 30, tzinfo=timezone.utc).isoformat(),),
        )
        prior_mtime = prior.path.stat().st_mtime_ns
        prior.db.close()

        today_dir = self.workspace / today
        today_dir.mkdir(parents=True)
        current = Coordinator(today_dir / "coordinator.sqlite")
        self.addCleanup(current.db.close)
        inherited = _inherit_latest_lane_gates(
            current, self.workspace, today, now=now,
        )
        current.seed()

        rows = {
            row["lane"]: row for row in current.db.execute(
                "SELECT lane,halted,reason,ready_at FROM lanes")
        }
        self.assertEqual(inherited, ["A", "B", "C"])
        for lane in ("B", "C"):
            self.assertEqual(rows[lane]["halted"], 1)
            self.assertEqual(rows[lane]["reason"], "live_pending_usage")
        self.assertEqual(rows["A"]["halted"], 0)
        self.assertGreater(
            datetime.fromisoformat(rows["A"]["ready_at"]), now,
        )
        self.assertEqual(prior.path.stat().st_mtime_ns, prior_mtime)

    def test_invalid_roster_fails_before_env_read(self):
        self.csv.write_text("account_key,username,country\nKR_001,nail_1,KR\n", encoding="utf-8")
        with self.assertRaises(ValueError):
            run_manual_pilot(self.csv, self.workspace)
        self.assertFalse(self.workspace.exists())


if __name__ == "__main__":
    unittest.main()
