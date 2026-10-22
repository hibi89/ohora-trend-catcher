"""Offline tests for the read-only P50 saved-data inventory."""
import json
from pathlib import Path
import sqlite3
import sys
import tempfile
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "raw_v2"))
from p50_v2_data_report import report


class DataReportTests(unittest.TestCase):
    def test_counts_null_view_distinct_from_real_zero_and_redacts_urls(self):
        with tempfile.TemporaryDirectory() as d:
            root=Path(d)/"p50_v2"
            root.mkdir()
            raw=sqlite3.connect(root/"raw.sqlite")
            raw.executescript("""
                CREATE TABLE pages(cycle INT,lane TEXT,ordinal INT,
                    response_json TEXT,created_at TEXT);
                CREATE TABLE post_ids(post_id TEXT);
                CREATE TABLE post_observations(post_id TEXT,view_count INT,
                    like_count INT,comments_count INT,observed_at TEXT);
            """)
            response={"id":"profile1","username":"user_1","followers_count":500,
                      "media_count":9189,"media":{"data":[
                          {"id":"post1","caption":"hello","like_count":9,
                           "comments_count":1,"view_count":None,
                           "media_type":"VIDEO",
                           "media_url":"https://secret.invalid/path?token=DO_NOT_PRINT"},
                          {"id":"post2","view_count":0,"like_count":0,
                           "comments_count":0}]}}
            raw.execute("INSERT INTO pages VALUES(?,?,?,?,?)",
                        (1,"A",1,json.dumps(response),"2026-10-22T16:58:06+00:00"))
            raw.executemany("INSERT INTO post_ids VALUES (?)",[("post1",),("post2",)])
            raw.executemany("INSERT INTO post_observations VALUES (?,?,?,?,?)",[
                ("post1",None,9,1,"2026-10-23T01:58:06+09:00"),
                ("post2",0,0,0,"2026-10-23T01:58:06+09:00")])
            raw.commit()
            raw.close()
            planner=sqlite3.connect(root/"planner.sqlite")
            planner.executescript("""
                CREATE TABLE accounts(ordinal INTEGER,finished INT,
                    finished_reason TEXT,cursor TEXT);
                CREATE TABLE jobs(state TEXT);
                CREATE TABLE lanes(lane TEXT,usage REAL,halted INT,ready_at TEXT);
                CREATE TABLE schedule_state(round_no INT,lane_index INT,
                    ordinal INT,slot_count INT,ready_at TEXT);
            """)
            planner.execute("INSERT INTO accounts VALUES(1,0,NULL,'SECRET_CURSOR')")
            planner.execute("INSERT INTO jobs VALUES('done')")
            planner.execute("INSERT INTO lanes VALUES('A',35.0,0,NULL)")
            planner.execute("INSERT INTO schedule_state VALUES(1,0,2,0,NULL)")
            planner.commit()
            planner.close()
            result=report(root, include_details=True, sample_count=1)
            self.assertEqual(result["raw"]["page_receipts"],1)
            self.assertEqual(result["raw"]["post_occurrences"],2)
            self.assertEqual(result["raw"]["distinct_post_ids"],2)
            self.assertEqual(result["observations"]["view_count_missing_null"],1)
            self.assertEqual(result["observations"]["view_count_real_zero"],1)
            self.assertEqual(result["raw"]["raw_post_field_coverage"]["view_count"]["non_null"],1)
            self.assertEqual(result["raw"]["first_raw_saved_at_kst"],
                             "2026-10-23T01:58:06+09:00")
            self.assertEqual(result["planner"]["schedule"]["next_account_ordinal"],2)
            output=json.dumps(result)
            self.assertNotIn("DO_NOT_PRINT",output)
            self.assertNotIn("SECRET_CURSOR",output)


if __name__=="__main__":
    unittest.main()
