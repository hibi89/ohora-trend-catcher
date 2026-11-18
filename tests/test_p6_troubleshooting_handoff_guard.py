"""Offline policy checks for the cross-chat troubleshooting handoff.

This guard cannot determine whether an AI actually wrote a useful incident.
It prevents accidental removal of the instructions and top-level pointers.
"""
from pathlib import Path
import re
import unittest

ROOT = Path(__file__).resolve().parents[1]
LEDGER = "docs/P6_TROUBLESHOOTING_LEDGER.md"


class P6TroubleshootingHandoffGuard(unittest.TestCase):
    def read(self, name):
        return (ROOT / name).read_text(encoding="utf-8")

    def test_successor_agents_are_directed_to_incident_ledger(self):
        instructions = self.read("AGENTS.md")
        protocol = self.read("docs/AI_WORK_UNIT_PROTOCOL.md")
        self.assertIn("MANDATORY — Troubleshooting", instructions)
        self.assertIn(LEDGER, instructions)
        self.assertIn("P6-NNN", instructions)
        self.assertIn("commit", instructions.lower())
        self.assertIn("P6_TROUBLESHOOTING_LEDGER.md", protocol)
        self.assertIn("채팅방", protocol)

    def test_current_handoff_links_survive_chat_restart(self):
        for name in ("STATUS.md", "HANDOFF.md"):
            with self.subTest(file=name):
                head = self.read(name)[:2000]
                self.assertIn(LEDGER, head)
                self.assertIn("AGENTS.md", head)

    def test_incident_index_matches_unique_detailed_records(self):
        ledger = self.read(LEDGER)
        table = re.findall(r"^\| (P6-\d{3}) \|", ledger, re.MULTILINE)
        details = re.findall(r"^### (P6-\d{3}) —", ledger, re.MULTILINE)
        self.assertGreaterEqual(len(table), 14)
        self.assertEqual(len(table), len(set(table)))
        self.assertEqual(len(details), len(set(details)))
        self.assertEqual(set(table), set(details))
        self.assertIn("RAW_V2_2026-10-19_TROUBLESHOOTING_CHRONICLE.md", ledger)

    def test_legacy_chronicle_and_frozen_design_are_kept(self):
        self.assertTrue(
            (ROOT / "docs/RAW_V2_2026-10-19_TROUBLESHOOTING_CHRONICLE.md").is_file()
        )
        ledger = self.read(LEDGER)
        self.assertIn("P6_FINAL_COLLECTION_DESIGN_2026-10-20.md", ledger)
        self.assertIn("DESIGN_FREEZE_NOTICE.md", ledger)


if __name__ == "__main__":
    unittest.main()
