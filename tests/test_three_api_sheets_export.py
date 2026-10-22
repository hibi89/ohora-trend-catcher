"""Offline read-only n8n export contract; no Google credentials or HTTP."""
import json
import sqlite3
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "raw_v2"))
from three_api_sheets_export import export_observations


class SheetsExportTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.path = Path(self.tmp.name) / "raw.sqlite"
        with sqlite3.connect(self.path) as db:
            db.executescript("""
                CREATE TABLE accounts(account_key TEXT, country TEXT, ig_id TEXT);
                CREATE TABLE posts(post_id TEXT,account_key TEXT,timestamp TEXT,
                    media_type TEXT,media_product_type TEXT,caption TEXT,permalink TEXT);
                CREATE TABLE post_observations(id INTEGER PRIMARY KEY,post_id TEXT,
                    observed_at TEXT,run_id TEXT,request_id TEXT,like_count INTEGER,
                    comments_count INTEGER,like_present INTEGER,comments_present INTEGER);
                CREATE TABLE three_api_raw_receipts(capture_id TEXT,account_key TEXT,
                    payload_json TEXT);
                INSERT INTO accounts VALUES ('P50_US_001','US','1234');
                INSERT INTO posts VALUES ('p1','P50_US_001','2026-10-20T12:00:00Z',
                    'VIDEO','REELS','blue nails','https://instagram.com/p/example/');
                INSERT INTO post_observations VALUES (1,'p1','2026-10-22T01:00:00Z',
                    'batch-1','req-1',42,3,1,1);
                INSERT INTO post_observations VALUES (2,'p1','2026-10-22T02:00:00Z',
                    'batch-2','req-2',52,4,1,1);
            """)
            for n, view in ((1, 100), (2, None)):
                post = {"id": "p1"}
                if view is not None:
                    post["view_count"] = view
                db.execute("INSERT INTO three_api_raw_receipts VALUES (?,?,?)",
                           (f"cap-{n}", "P50_US_001", json.dumps({
                               "request_id": f"req-{n}", "posts": [post],
                               "post_field_status": {"p1": {
                                   "view_count": "returned" if view is not None else "absent"
                               }},
                           })))

    def test_incremental_idempotent_and_preserves_missing_views(self):
        first = export_observations(self.path, limit=1)
        self.assertEqual(len(first["rows"]), 1)
        row = first["rows"][0]
        self.assertEqual(row["observation_key"], "req-1:p1")
        self.assertEqual(row["account_id"], "1234")
        self.assertEqual(row["country"], "US")
        self.assertEqual(row["reels_view_count"], 100)
        self.assertEqual(row["reels_view_count_status"], "returned")
        self.assertEqual(first["next_after_id"], 1)
        second = export_observations(self.path, after_id=first["next_after_id"])
        self.assertEqual(second["rows"][0]["observation_key"], "req-2:p1")
        self.assertIsNone(second["rows"][0]["reels_view_count"])
        self.assertEqual(second["rows"][0]["reels_view_count_status"], "absent")
        self.assertEqual(export_observations(self.path, after_id=2)["rows"], [])
        self.assertEqual(export_observations(self.path)["rows"], export_observations(self.path)["rows"])

    def test_invalid_cursor_and_missing_file_fail_closed(self):
        with self.assertRaises(ValueError):
            export_observations(self.path, after_id=-1)
        with self.assertRaises(ValueError):
            export_observations(self.path, limit=0)
        with self.assertRaises(sqlite3.OperationalError):
            export_observations(Path(self.tmp.name) / "missing.sqlite")

    def test_unverified_capture_is_not_exported(self):
        with sqlite3.connect(self.path) as db:
            db.execute(
                "INSERT INTO post_observations VALUES (3,'p1',"
                "'2026-10-22T03:00:00Z','batch-3','unverified',"
                "60,5,1,1)"
            )
        result = export_observations(self.path, after_id=2)
        self.assertEqual(result["rows"], [])
        self.assertEqual(result["next_after_id"], 2)


    def test_unverified_gap_then_verified_row_preserves_incremental_order(self):
        with sqlite3.connect(self.path) as db:
            db.execute(
                "INSERT INTO post_observations VALUES (3,'p1',"
                "'2026-10-22T03:00:00Z','batch-3','unverified',60,5,1,1)"
            )
            db.execute(
                "INSERT INTO post_observations VALUES (4,'p1',"
                "'2026-10-22T04:00:00Z','batch-4','req-4',61,6,1,1)"
            )
            db.execute(
                "INSERT INTO three_api_raw_receipts VALUES (?,?,?)",
                ("cap-4", "P50_US_001", json.dumps({
                    "request_id": "req-4", "posts": [{"id": "p1"}]
                }))
            )
        result = export_observations(self.path, after_id=2, limit=1)
        self.assertEqual([r["observation_id"] for r in result["rows"]], [4])
        self.assertEqual(result["next_after_id"], 4)
        self.assertEqual(export_observations(
            self.path, after_id=4, limit=1)["rows"], [])

    def test_explicit_zero_is_not_missing(self):
        with sqlite3.connect(self.path) as db:
            payload = json.dumps({
                "request_id": "req-2",
                "posts": [{"id": "p1", "view_count": 0}],
                "post_field_status": {"p1": {"view_count": "returned"}},
            })
            db.execute("UPDATE three_api_raw_receipts SET payload_json=? "
                       "WHERE capture_id='cap-2'", (payload,))
        result = export_observations(self.path, after_id=1)
        self.assertEqual(result["rows"][0]["reels_view_count"], 0)
        self.assertEqual(result["rows"][0]["reels_view_count_status"], "returned")

    def test_export_does_not_mutate_raw(self):
        before = self.path.read_bytes()
        export_observations(self.path)
        self.assertEqual(self.path.read_bytes(), before)


if __name__ == "__main__":
    unittest.main()
