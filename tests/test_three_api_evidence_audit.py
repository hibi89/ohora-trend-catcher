"""No-network, read-only page evidence audit regressions."""
from pathlib import Path
import sqlite3
import sys
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "raw_v2"))
from three_api_evidence_audit import audit_page_evidence


class EvidenceAuditTests(unittest.TestCase):
    def setUp(self):
        self.db = sqlite3.connect(":memory:")
        self.db.row_factory = sqlite3.Row
        self.addCleanup(self.db.close)
        self.db.executescript("""
            CREATE TABLE jobs(id INTEGER PRIMARY KEY, account_ordinal INTEGER,
                              page INTEGER, state TEXT);
            CREATE TABLE page_evidence(
                account_ordinal INTEGER, page INTEGER, job_id INTEGER,
                receipt TEXT, requested_cursor TEXT, response_cursor TEXT,
                terminal INTEGER);
        """)

    def insert(self, page, *, account=1, receipt=None, requested=None,
               response=None, terminal=0, state="done", job_page=None):
        job_id = account * 10 + page
        self.db.execute("INSERT INTO jobs VALUES (?,?,?,?)",
                        (job_id, account, page if job_page is None else job_page, state))
        self.db.execute("INSERT INTO page_evidence VALUES (?,?,?,?,?,?,?)",
                        (account, page, job_id, receipt or f"receipt-{job_id}",
                         requested, response, terminal))

    def test_empty_database_is_not_live_authorization(self):
        result = audit_page_evidence(self.db)
        self.assertTrue(result["valid"])
        self.assertFalse(result["runtime_authorized"])
        self.assertFalse(result["external_raw_receipts_verified"])

    def test_valid_three_page_cursor_chain(self):
        self.insert(1, response="cursor-1")
        self.insert(2, requested="cursor-1", response="cursor-2")
        self.insert(3, requested="cursor-2", terminal=1)
        result = audit_page_evidence(self.db)
        self.assertTrue(result["valid"])
        self.assertEqual(result["pages_checked"], 3)

    def test_missing_predecessor_fails_closed(self):
        self.insert(2, requested="orphan")
        self.assertIn("missing_predecessor", audit_page_evidence(self.db)["findings"])

    def test_cursor_mismatch_and_terminal_predecessor(self):
        self.insert(1, response="secret-cursor", terminal=1)
        self.insert(2, requested="wrong")
        result = audit_page_evidence(self.db)
        self.assertIn("cursor_chain_mismatch", result["findings"])
        self.assertNotIn("secret-cursor", repr(result))

    def test_job_not_done_is_rejected(self):
        self.insert(1, state="leased")
        self.assertIn("job_evidence_mismatch", audit_page_evidence(self.db)["findings"])

    def test_head_cursor_and_duplicate_receipt_are_rejected(self):
        self.insert(1, requested="bad", receipt="duplicate")
        self.insert(1, account=2, receipt="duplicate")
        result = audit_page_evidence(self.db)
        self.assertIn("head_has_cursor", result["findings"])
        self.assertIn("duplicate_receipt", result["findings"])

    def test_does_not_write_or_open_http(self):
        self.insert(1)
        before = self.db.total_changes
        audit_page_evidence(self.db)
        self.assertEqual(self.db.total_changes, before)


if __name__ == "__main__":
    unittest.main()
