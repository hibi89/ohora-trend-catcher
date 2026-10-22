"""Offline structural safety tests for P6 Sheets manual n8n workflow."""
import json
import unittest
from pathlib import Path

WORKFLOW = Path(__file__).resolve().parents[1] / "kq3_294_collection" / "n8n_p6_sheets_manual_pilot.json"


class ManualSheetsPilotTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        workflow = json.loads(WORKFLOW.read_text(encoding="utf-8"))
        cls.workflow = workflow
        cls.nodes = {node["name"]: node for node in workflow["nodes"]}

    def test_inactive_manual_only(self):
        self.assertFalse(self.workflow["active"])
        types = {n["type"] for n in self.nodes.values()}
        self.assertIn("n8n-nodes-base.manualTrigger", types)
        self.assertNotIn("n8n-nodes-base.scheduleTrigger", types)
        self.assertNotIn("n8n-nodes-base.httpRequest", types)

    def test_read_only_export(self):
        cmd = self.nodes["Read-only RAW export"]["parameters"]["command"]
        self.assertIn("three_api_sheets_export.py", cmd)
        self.assertIn("OHORA_P6_RAW_DB", cmd)
        self.assertIn("OHORA_P6_SHEETS_AFTER_ID", cmd)
        self.assertNotIn("--live", cmd)

    def test_upsert_key(self):
        params = self.nodes["Sheets upsert by observation_key"]["parameters"]
        self.assertEqual(params["operation"], "appendOrUpdate")
        self.assertEqual(params["columns"]["matchingColumns"], ["observation_key"])

    def test_no_checkpoint_fields_written_to_sheets(self):
        js = self.nodes["Validate receipt-backed rows"]["parameters"]["jsCode"]
        self.assertIn("return data.rows.map(row=>({json:row}))", js)
        self.assertNotIn("_batch_next_after_id", js)

    def test_no_automatic_checkpoint_persistence(self):
        js = self.nodes["Manual checkpoint approval required"]["parameters"]["jsCode"]
        self.assertIn("NOT_AUTOMATED", js)
        self.assertNotIn("setWorkflowStaticData", js)


if __name__ == "__main__":
    unittest.main()
