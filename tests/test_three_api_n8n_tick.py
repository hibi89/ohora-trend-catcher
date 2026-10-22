"""Offline tests for the n8n scheduler adapter. No Meta or pilot databases."""
from datetime import date, datetime, timezone
import json
from pathlib import Path
import re
import tempfile
import unittest
from unittest.mock import patch

from raw_v2 import three_api_n8n_tick as adapter


class N8nTickTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.now = datetime(2026, 10, 22, 1, 0, tzinfo=timezone.utc)
        self.kw = dict(source=self.root / "roster.csv", workspace=self.root / "pilot",
                       credentials_dir=self.root / "credentials",
                       state_file=self.root / "private" / "state.json",
                       now=self.now, max_pages=1)

    def tearDown(self):
        self.temp.cleanup()

    def fake_existing_pilot_store(self):
        (self.kw["workspace"] / "2026-10-22").mkdir(parents=True)
        (self.kw["workspace"] / "2026-10-22" / "coordinator.sqlite").touch()
        (self.kw["workspace"] / "p6_pilot_raw.sqlite").touch()

    def fake_bootstrap_runner(self, *_args, **_kwargs):
        self.fake_existing_pilot_store()
        return {"status": "page_committed", "committed_pages": 1,
                "account_failures": 0, "attempted_steps": 1,
                "verified_success_at": None}

    @patch.object(adapter, "_read_gate", return_value=("absent", None, True, None))
    def test_offline_tick_never_calls_live_and_needs_explicit_gate(self, _):
        result = adapter.tick(**self.kw)
        self.assertEqual(result["action"], "WAIT_EXPLICIT_LIVE_APPROVAL")
        self.assertFalse(result["live_meta_called"])

    @patch.object(adapter, "_read_gate", return_value=("manual_review", None, False, date(2026, 10, 22)))
    def test_unknown_state_stops_before_credentials_or_runner(self, _):
        with patch.object(adapter, "load_three_api_environment") as loader:
            result = adapter.tick(**self.kw, live=True, confirm_owner_approval=True)
        loader.assert_not_called()
        self.assertEqual(result["action"], "MANUAL_REVIEW")

    @patch.object(adapter, "_read_gate", return_value=("absent", None, True, None))
    @patch.object(adapter, "run_manual_pilot", side_effect=RuntimeError("synthetic failure"))
    @patch.object(adapter, "load_three_api_environment", return_value={"synthetic": True})
    def test_live_exception_never_claims_that_meta_was_not_called(self, *_):
        from contextlib import redirect_stdout
        from io import StringIO
        output = StringIO()
        args = ["--source", str(self.kw["source"]),
                "--workspace", str(self.kw["workspace"]),
                "--credentials-dir", str(self.kw["credentials_dir"]),
                "--state-file", str(self.kw["state_file"]),
                "--live", "--confirm-owner-approval"]
        with redirect_stdout(output):
            code = adapter.main(args)
        result = json.loads(output.getvalue())
        self.assertEqual(code, 2)
        self.assertEqual(result["action"], "MANUAL_REVIEW")
        self.assertIsNone(result["live_meta_called"])
        self.assertEqual(result["live_meta_call_status"], "unknown_after_live_error")

    @patch.object(adapter, "_read_gate", return_value=("absent", None, True, None))
    @patch.object(adapter, "run_manual_pilot", return_value={
        "status": "page_committed", "committed_pages": 1, "account_failures": 0,
        "attempted_steps": 1, "verified_success_at": "2026-10-22T01:00:00+00:00"})
    @patch.object(adapter, "load_three_api_environment", return_value={"private": "secret"})
    @patch.object(adapter, "read_pilot_status", side_effect=[
        {"durable_receipts": 10, "lanes": {"A": {}, "B": {}, "C": {}}},
        {"durable_receipts": 11, "lanes": {"A": {}, "B": {}, "C": {}}},
    ])
    def test_first_verified_committed_success_persisted(self, status_reader, loader, runner, _):
        self.fake_existing_pilot_store()
        result = adapter.tick(**self.kw, live=True, confirm_owner_approval=True)
        runner.assert_called_once()
        self.assertEqual(runner.call_args.kwargs["max_pages"], 1)
        saved = json.loads(self.kw["state_file"].read_text())
        self.assertEqual(saved["first_verified_success_at"], "2026-10-22T01:00:00+00:00")
        self.assertTrue(result["first_success_persisted"])
        self.assertEqual(result["storage_verification"]["status"], "verified")
        self.assertEqual(result["storage_verification"]["receipt_delta"], 1)
        self.assertNotIn("secret", json.dumps(result))

    @patch.object(adapter, "_read_gate", return_value=("absent", None, True, None))
    @patch.object(adapter, "run_manual_pilot", return_value={
        "status": "no_ready_work", "committed_pages": 0, "account_failures": 0,
        "attempted_steps": 1, "verified_success_at": None})
    @patch.object(adapter, "load_three_api_environment", return_value={})
    @patch.object(adapter, "read_pilot_status", side_effect=[
        {"durable_receipts": 10, "lanes": {"A": {}, "B": {}, "C": {}}},
        {"durable_receipts": 10, "lanes": {"A": {}, "B": {}, "C": {}}},
    ])
    def test_no_ready_work_does_not_claim_meta_call(self, *_):
        self.fake_existing_pilot_store()
        result = adapter.tick(**self.kw, live=True, confirm_owner_approval=True)
        self.assertEqual(result["status"], "no_ready_work")
        self.assertEqual(result["committed_pages"], 0)
        self.assertFalse(result["live_meta_called"])
        self.assertEqual(result["storage_verification"]["status"], "verified")

    @patch.object(adapter, "_read_gate", return_value=("absent", None, True, None))
    @patch.object(adapter, "run_manual_pilot", return_value={
        "status": "account_failed", "committed_pages": 0, "account_failures": 1,
        "attempted_steps": 1, "verified_success_at": None})
    @patch.object(adapter, "load_three_api_environment", return_value={})
    def test_failed_page_does_not_persist_start_clock(self, *_):
        adapter.tick(**self.kw, live=True, confirm_owner_approval=True)
        self.assertFalse(self.kw["state_file"].exists())

    @patch.object(adapter, "_read_gate", return_value=("absent", None, True, None))
    @patch.object(adapter, "run_manual_pilot", return_value={
        "status": "page_committed", "committed_pages": 1, "account_failures": 0,
        "attempted_steps": 1, "verified_success_at": None})
    @patch.object(adapter, "load_three_api_environment", return_value={})
    @patch.object(adapter, "read_pilot_status", side_effect=[
        {"durable_receipts": 10, "lanes": {"A": {}, "B": {}, "C": {}}},
        {"durable_receipts": 10, "lanes": {"A": {}, "B": {}, "C": {}}},
    ])
    def test_receipt_delta_mismatch_is_reported_for_manual_review(self, *_):
        self.fake_existing_pilot_store()
        result = adapter.tick(**self.kw, live=True, confirm_owner_approval=True)
        self.assertEqual(result["storage_verification"]["status"], "receipt_delta_mismatch")
        self.assertEqual(result["storage_verification"]["receipt_delta"], 0)

    @patch.object(adapter, "_read_gate", return_value=("absent", None, True, None))
    @patch.object(adapter, "run_manual_pilot", return_value={
        "status": "page_committed", "committed_pages": 1, "account_failures": 0,
        "attempted_steps": 1, "verified_success_at": None})
    @patch.object(adapter, "load_three_api_environment", return_value={})
    @patch.object(adapter, "read_pilot_status", return_value={
        "durable_receipts": 1, "lanes": {"A": {}, "B": {}, "C": {}}})
    def test_empty_pilot_bootstrap_compares_first_durable_receipt(self, *_):
        adapter.run_manual_pilot.side_effect = self.fake_bootstrap_runner
        result = adapter.tick(**self.kw, live=True, confirm_owner_approval=True)
        self.assertEqual(result["storage_verification"]["status"], "verified")
        self.assertEqual(result["storage_verification"]["receipt_delta"], 1)

    @patch.object(adapter, "_read_gate", return_value=("absent", None, True, None))
    @patch.object(adapter, "run_manual_pilot")
    @patch.object(adapter, "load_three_api_environment")
    @patch.object(adapter, "read_pilot_status", side_effect=RuntimeError("offline fixture"))
    def test_unreadable_receipt_baseline_stops_before_runner(self, status_reader, loader, runner, _):
        self.fake_existing_pilot_store()
        result = adapter.tick(**self.kw, live=True, confirm_owner_approval=True)
        runner.assert_not_called()
        loader.assert_not_called()
        self.assertFalse(result["live_meta_called"])
        self.assertEqual(result["storage_verification"]["status"], "baseline_unavailable")

    def test_three_page_hard_cap(self):
        with self.assertRaises(ValueError):
            adapter.tick(**{**self.kw, "max_pages": 4})

    def test_n8n_export_is_inactive_and_bounded(self):
        workflow = json.loads((Path(__file__).parents[1] /
                               "kq3_294_collection" /
                               "n8n_p6_daily_collection.json").read_text(encoding="utf-8"))
        self.assertFalse(workflow["active"])
        self.assertEqual(workflow["id"], "p6-daily-collection-v1")
        error_workflow = json.loads((Path(__file__).parents[1] /
                                     "kq3_294_collection" /
                                     "n8n_p6_error_alert.json").read_text(encoding="utf-8"))
        self.assertEqual(workflow["settings"]["errorWorkflow"], error_workflow["id"])
        types = {node["type"] for node in workflow["nodes"]}
        self.assertIn("n8n-nodes-base.scheduleTrigger", types)
        self.assertIn("n8n-nodes-base.executeWorkflowTrigger", types)
        manual = next(n for n in workflow["nodes"]
                      if n["type"] == "n8n-nodes-base.executeWorkflowTrigger")
        self.assertEqual(manual["parameters"]["inputSource"], "passthrough")
        command = next(n["parameters"]["command"] for n in workflow["nodes"]
                       if n["type"] == "n8n-nodes-base.executeCommand")
        self.assertRegex(command, re.compile(r"p6_n8n_tick\.ps1", re.I))
        self.assertTrue(command.isascii())
        launcher = (Path(__file__).parents[1] / "raw_v2" /
                    "p6_n8n_tick.ps1").read_text(encoding="utf-8")
        self.assertRegex(launcher, r"'--max-pages',\s*'3'")
        self.assertIn("OHORA_P6_LIVE_APPROVED", launcher)
        self.assertNotRegex(launcher, r"--live\s+--confirm-owner-approval\s*$")
        self.assertNotRegex(command, re.compile(r"EA[A-Za-z0-9]{35,}"))

    def test_workflow_validates_result_and_accepts_external_cursor_states(self):
        workflow = json.loads((Path(__file__).parents[1] /
                               "kq3_294_collection" /
                               "n8n_p6_daily_collection.json").read_text(encoding="utf-8"))
        nodes = {node["name"]: node for node in workflow["nodes"]}
        validate = nodes["Validate P6 result and classify progress"]
        self.assertEqual(validate["type"], "n8n-nodes-base.code")
        code = validate["parameters"]["jsCode"]
        for state in ("resume_required", "coverage_gap", "resume_ready"):
            self.assertIn(state, code)
        self.assertIn("committed_pages", code)
        self.assertIn("alert_required", code)
        self.assertIn("storage_verification", code)
        self.assertIn("receipt_delta", code)
        self.assertIn("lane_status", code)
        self.assertNotIn("stdout,", code)
        edges = workflow["connections"]["P6 read gates and run at most three pages"]["main"][0]
        self.assertEqual(edges[0]["node"], "Validate P6 result and classify progress")
        self.assertEqual(workflow["settings"]["errorWorkflow"], "p6-error-alert-v1")
        alert_edges = workflow["connections"]["Alert required?"]["main"]
        self.assertEqual(alert_edges[0][0]["node"], "Send redacted P6 alert")
        self.assertEqual(alert_edges[1][0]["node"], "Keep result in n8n execution")

    def test_failure_alert_redacts_exception_text_and_uses_configured_webhook(self):
        path = Path(__file__).parents[1] / "kq3_294_collection" / "n8n_p6_error_alert.json"
        workflow = json.loads(path.read_text(encoding="utf-8"))
        self.assertFalse(workflow["active"])
        nodes = {node["name"]: node for node in workflow["nodes"]}
        self.assertEqual(nodes["P6 workflow failed"]["type"],
                         "n8n-nodes-base.errorTrigger")
        redact = nodes["Redact failure details"]["parameters"]["jsCode"]
        self.assertNotIn("error.message", redact)
        self.assertNotIn("stack", redact)
        webhook = nodes["Send redacted failure alert"]["parameters"]
        self.assertEqual(webhook["url"], "={{ $env.OHORA_P6_ALERT_WEBHOOK }}")
        self.assertIn("Send redacted failure alert", workflow["connections"]["Redact failure details"]["main"][0][0]["node"])


if __name__ == "__main__":
    unittest.main()
