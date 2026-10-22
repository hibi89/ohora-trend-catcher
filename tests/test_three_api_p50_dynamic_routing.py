import sqlite3
import tempfile
import unittest
from datetime import datetime, timezone, timedelta
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "raw_v2"))
from three_api_p50_coordinator import P50Coordinator


class DynamicP50RoutingTests(unittest.TestCase):
    def test_a_then_b_then_c_and_preserve_bound(self):
        with tempfile.TemporaryDirectory() as root:
            db = P50Coordinator(Path(root) / "p50" / "coordinator.sqlite")
            try:
                db.seed()
                db.db.execute("""CREATE TABLE job_request_bindings (
                    job_id INTEGER PRIMARY KEY, request_id TEXT)""")
                db.db.execute("""CREATE TABLE page_evidence (
                    account_ordinal INTEGER, page INTEGER,
                    terminal INTEGER, response_cursor TEXT)""")
                now = datetime.now(timezone.utc)
                first = db.lease_dynamic_p50(now=now)
                self.assertEqual(first["lane"], "A")
                self.assertEqual(first["page"], 1)
                db.db.execute("INSERT INTO job_request_bindings VALUES (?,?)",
                              (first["id"], "bound"))
                db.db.execute("UPDATE lanes SET halted=1,reason='usage_limit' WHERE lane='A'")
                second = db.lease_dynamic_p50(now=now)
                self.assertEqual(second["lane"], "B")
                db.db.execute("UPDATE lanes SET halted=1,reason='usage_limit' WHERE lane='B'")
                third = db.lease_dynamic_p50(now=now)
                self.assertEqual(third["lane"], "C")
                db.db.execute("UPDATE lanes SET halted=1,reason='usage_limit' WHERE lane='C'")
                self.assertIsNone(db.lease_dynamic_p50(now=now))
                self.assertEqual(db.db.execute(
                    "SELECT lane FROM jobs WHERE id=?", (first["id"],)).fetchone()[0], "A")
                db.seed()
            finally:
                db.db.close()


if __name__ == "__main__":
    unittest.main()
