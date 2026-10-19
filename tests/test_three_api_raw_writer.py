"""Focused offline tests for the provisional RAW v2 writer.

Synthetic schema is reconstructed from the existing collector's documented
RAW v2 columns; no operational database is opened.
"""
import sqlite3
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "raw_v2"))
from three_api_raw_writer import write_capture, RawWriteError
from three_api_raw_recovery import replay_saved

SCHEMA = """
CREATE TABLE accounts(account_key TEXT PRIMARY KEY);
CREATE TABLE posts(post_id TEXT PRIMARY KEY,account_key TEXT REFERENCES accounts(account_key),
 username TEXT,caption TEXT,timestamp TEXT,permalink TEXT,media_type TEXT,media_product_type TEXT,
 media_url TEXT,thumbnail_url TEXT,first_seen_run TEXT);
CREATE TABLE post_media(media_id TEXT PRIMARY KEY,post_id TEXT REFERENCES posts(post_id),
 account_key TEXT,media_role TEXT,item_order INTEGER,media_type TEXT,media_url TEXT,thumbnail_url TEXT,first_seen_run TEXT);
CREATE TABLE post_observations(id INTEGER PRIMARY KEY,post_id TEXT REFERENCES posts(post_id),
 observed_at TEXT,run_id TEXT,like_count INTEGER,comments_count INTEGER,like_present INTEGER,
 comments_present INTEGER,request_id TEXT,UNIQUE(post_id,request_id));
CREATE TABLE carousel_status(post_id TEXT PRIMARY KEY REFERENCES posts(post_id),account_key TEXT,
 child_rows INTEGER,has_next_page TEXT,coverage TEXT,last_request_id TEXT);
CREATE TABLE account_observations(id INTEGER PRIMARY KEY,account_key TEXT,observed_at TEXT,
 run_id TEXT,followers_count INTEGER,follows_count INTEGER,media_count INTEGER,request_id TEXT,
 UNIQUE(account_key,request_id));
CREATE TABLE slots(slot_id INTEGER PRIMARY KEY,ready_at TEXT);
CREATE TABLE slot_accounts(slot_id INTEGER,account_key TEXT,request_id TEXT);
CREATE TABLE runs(run_id TEXT PRIMARY KEY,started_at TEXT,finished_at TEXT,api_version TEXT,
 mode TEXT,status TEXT,requests INTEGER,successes INTEGER,failures INTEGER,usage_max REAL);
CREATE TABLE request_log(run_id TEXT,request_id TEXT PRIMARY KEY,account_key TEXT,phase TEXT,
 started_at TEXT,finished_at TEXT,http_status INTEGER,status TEXT,records_received INTEGER,
 has_next_page TEXT,stop_reason TEXT,error_code TEXT,error_subcode TEXT,error_message TEXT,
 raw_response_path TEXT,requested_fields TEXT,usage_json TEXT);
CREATE TABLE field_observations(run_id TEXT,request_id TEXT,account_key TEXT,resource_type TEXT,
 media_type TEXT,field_name TEXT,status TEXT,item_id TEXT,
 PRIMARY KEY(request_id,resource_type,media_type,field_name,item_id));
"""

def fixture():
    db = sqlite3.connect(":memory:", isolation_level=None)
    db.executescript(SCHEMA)
    db.executemany("INSERT INTO accounts VALUES(?)", [("KR_001",),("US_001",)])
    db.execute("INSERT INTO slots VALUES(1,'2099-01-01T00:00:00+00:00')")
    return db

def capture():
    return {"capture_id":"test-capture-1","account_key":"KR_001",
            "observed_at":"2026-10-19T11:00:00+00:00",
            "posts":[{"id":"post-1","media_type":"IMAGE","like_count":0}]}

class RawWriterTests(unittest.TestCase):
    def setUp(self):
        self.db=fixture()
    def tearDown(self):
        self.db.close()
    def count(self,table):
        return self.db.execute("SELECT COUNT(*) FROM "+table).fetchone()[0]
    def test_replay_and_legacy_state(self):
        c=capture()
        self.assertEqual(write_capture(self.db,c),"committed")
        self.assertEqual(replay_saved(self.db,c,source_verified=True),"already_committed")
        self.assertEqual(self.count("post_observations"),1)
        self.assertEqual(self.db.execute("SELECT ready_at FROM slots").fetchone()[0],"2099-01-01T00:00:00+00:00")
        self.assertEqual(self.db.execute("PRAGMA foreign_key_check").fetchall(),[])
        self.assertEqual(self.db.execute("PRAGMA integrity_check").fetchone()[0],"ok")
    def test_conflicting_receipt_rolls_back(self):
        write_capture(self.db,capture())
        c=capture();c["observed_at"]="2026-10-19T12:00:00+00:00"
        with self.assertRaises(RawWriteError):
            write_capture(self.db,c)
        self.assertEqual(self.count("post_observations"),1)
    def test_cross_account_collision(self):
        write_capture(self.db,capture())
        c=capture();c["capture_id"]="test-capture-2";c["account_key"]="US_001"
        with self.assertRaises(RawWriteError):
            write_capture(self.db,c)
        self.assertEqual(self.count("post_observations"),1)
    def test_new_observation_and_null(self):
        write_capture(self.db,capture())
        c=capture();c["capture_id"]="test-capture-2";c["posts"][0].pop("like_count")
        self.assertEqual(write_capture(self.db,c),"committed")
        self.assertEqual(self.count("post_observations"),2)
        rows=self.db.execute("SELECT like_count,like_present FROM post_observations ORDER BY id").fetchall()
        self.assertEqual(rows,[(0,1),(None,0)])
    def test_carousel_only_children(self):
        c=capture()
        c["posts"]=[{"id":"album-1","media_type":"CAROUSEL_ALBUM",
                     "children":[{"media_id":"child-1","media_type":"IMAGE"}]}]
        write_capture(self.db,c)
        self.assertEqual(self.db.execute("SELECT media_id,media_role FROM post_media").fetchall(),
                         [("child-1","carousel_child")])
    def test_failed_commit_can_replay(self):
        c=capture();c["posts"][0]["children"]="invalid"
        with self.assertRaises(RawWriteError):
            write_capture(self.db,c)
        self.assertEqual(self.count("posts"),0)
        c["posts"][0].pop("children")
        self.assertEqual(replay_saved(self.db,c,source_verified=True),"committed")
    def test_source_verification_required(self):
        with self.assertRaises(RawWriteError):
            replay_saved(self.db,capture())
        self.assertEqual(self.count("posts"),0)

if __name__ == "__main__":
    unittest.main()
