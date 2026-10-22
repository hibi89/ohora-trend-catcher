"""P50 deterministic offline tests: manual continuous A/B/C rounds."""
import sys
import json
import sqlite3
import tempfile
from datetime import datetime, timedelta, timezone
from pathlib import Path
import unittest

sys.path.insert(0,str(Path(__file__).resolve().parents[1]/"raw_v2"))
from p50_cycle_engine_v2 import CyclePlanner

T = datetime(2026,10,23,1,0,tzinfo=timezone.utc)

def new_planner(directory):
    p = CyclePlanner(Path(directory)/"planner.sqlite")
    for lane in ("A","B","C"):
        p.set_usage(lane,1,now=T)
    return p

def posts(n):
    return [{"id":str(i)} for i in range(n)]

class CycleTests(unittest.TestCase):
    def test_strict_a_b_c_order_then_63_minute_wait(self):
        with tempfile.TemporaryDirectory() as d:
            p=new_planner(d)
            previous={}
            for lane in ("A","B","C"):
                for ordinal in range(1,51):
                    j=p.next_job(now=T)
                    self.assertIsNotNone(j)
                    self.assertEqual((j["lane"],j["ordinal"],j["round"],j["limit"]),
                                     (lane,ordinal,1,8))
                    self.assertEqual(j["cursor"],previous.get(ordinal))
                    next_cursor=f"{lane}{ordinal}"
                    p.acknowledge(j,receipt_id=f"r-{lane}-{ordinal}",
                                  posts=posts(8),next_cursor=next_cursor,now=T)
                    previous[ordinal]=next_cursor
            self.assertEqual(p.pause(now=T)["reason"],"round_wait")
            self.assertEqual(datetime.fromisoformat(p.pause(now=T)["until"]),
                             T+timedelta(minutes=63))
            self.assertIsNone(p.next_job(now=T+timedelta(minutes=62)))
            j=p.next_job(now=T+timedelta(minutes=63))
            self.assertEqual((j["round"],j["lane"],j["ordinal"],j["cursor"]),
                             (2,"A",1,"C1"))
            p.db.close()

    def test_short_page_fills_exactly_eight_before_next_account(self):
        with tempfile.TemporaryDirectory() as d:
            p=new_planner(d)
            j=p.next_job(now=T)
            self.assertEqual(j["limit"],8)
            p.acknowledge(j,receipt_id="one",posts=posts(3),next_cursor="cursor3",now=T)
            j=p.next_job(now=T)
            self.assertEqual((j["lane"],j["ordinal"],j["limit"],j["cursor"]),
                             ("A",1,5,"cursor3"))
            self.assertEqual(j["cycle"],2)
            p.acknowledge(j,receipt_id="two",posts=posts(5),next_cursor="cursor8",now=T)
            j=p.next_job(now=T)
            self.assertEqual((j["lane"],j["ordinal"],j["limit"]),
                             ("A",2,8))
            p.db.close()

    def test_halted_lane_does_not_jump_to_another_lane(self):
        with tempfile.TemporaryDirectory() as d:
            p=new_planner(d)
            j=p.next_job(now=T)
            p.acknowledge(j,receipt_id="one",posts=posts(8),next_cursor="next",now=T)
            p.set_usage("A",86,now=T)
            self.assertIsNone(p.next_job(now=T))
            self.assertEqual(p.pause(now=T)["reason"],"quota_hold")
            self.assertEqual(p.pause(now=T)["lane"],"A")
            p.set_usage("A",1,now=T+timedelta(minutes=63))
            j=p.next_job(now=T+timedelta(minutes=63))
            self.assertEqual((j["lane"],j["ordinal"]),("A",2))
            p.db.close()

    def test_restart_retains_partial_slot(self):
        with tempfile.TemporaryDirectory() as d:
            p=new_planner(d)
            j=p.next_job(now=T)
            p.acknowledge(j,receipt_id="one",posts=posts(3),next_cursor="c3",now=T)
            p.db.close()
            p=CyclePlanner(Path(d)/"planner.sqlite")
            j=p.next_job(now=T+timedelta(minutes=1))
            self.assertEqual((j["lane"],j["ordinal"],j["limit"],j["cursor"]),
                             ("A",1,5,"c3"))
            p.db.close()

    def test_no_year_cutoff_and_finished_accounts_skipped(self):
        with tempfile.TemporaryDirectory() as d:
            p=new_planner(d)
            j=p.next_job(now=T)
            p.acknowledge(j,receipt_id="old",posts=posts(8),next_cursor="next",
                          oldest_published_at=datetime(2018,1,1,tzinfo=timezone.utc),
                          now=T)
            self.assertEqual(p.db.execute("SELECT finished FROM accounts WHERE ordinal=1").fetchone()[0],0)
            j=p.next_job(now=T)
            self.assertEqual(j["ordinal"],2)
            p.acknowledge(j,receipt_id="done",posts=posts(2),next_cursor=None,
                          exhausted=True,now=T)
            j=p.next_job(now=T)
            self.assertEqual(j["ordinal"],3)
            p.db.close()

    def test_all_accounts_exhausted_stops(self):
        with tempfile.TemporaryDirectory() as d:
            p=new_planner(d)
            for ordinal in range(1,51):
                j=p.next_job(now=T)
                self.assertEqual((j["lane"],j["ordinal"]),("A",ordinal))
                p.acknowledge(j,receipt_id=str(ordinal),posts=posts(1),
                              next_cursor=None,exhausted=True,now=T)
            self.assertIsNone(p.next_job(now=T))
            self.assertEqual(p.pause(now=T)["reason"],"complete")
            self.assertEqual(p.summary()["finished_accounts"],50)
            p.db.close()

    def test_more_than_twenty_rounds_are_allowed(self):
        with tempfile.TemporaryDirectory() as d:
            p=new_planner(d)
            p._schedule()
            for number in range(1,21):
                p.db.execute("""INSERT INTO jobs(cycle,lane,ordinal,state,post_count)
                    VALUES (?,'A',1,'done',8)""",(number,))
            p.db.execute("UPDATE schedule_state SET round_no=21 WHERE id=1")
            j=p.next_job(now=T)
            self.assertEqual((j["round"],j["cycle"],j["lane"],j["ordinal"]),
                             (21,21,"A",1))
            p.db.close()

    def test_recover_false_terminal_full_page_from_existing_raw(self):
        with tempfile.TemporaryDirectory() as d:
            p=new_planner(d)
            first=p.next_job(now=T)
            p.acknowledge(first,receipt_id="saved",posts=posts(8),
                          next_cursor=None,exhausted=True,now=T)
            self.assertEqual(p.summary()["finished_accounts"],1)
            raw=sqlite3.connect(":memory:")
            raw.execute("""CREATE TABLE pages(cycle INTEGER,lane TEXT,
                ordinal INTEGER,response_json TEXT)""")
            bd={"media":{"data":posts(8),"paging":{
                "cursors":{"after":"validafter"}}}}
            raw.execute("INSERT INTO pages VALUES (?,?,?,?)",
                        (1,"A",1,json.dumps(bd)))
            self.assertEqual(p.reopen_false_exhausted_accounts(raw),[1])
            self.assertEqual(p.reopen_false_exhausted_accounts(raw),[])
            account=p.db.execute("SELECT cursor,finished FROM accounts WHERE ordinal=1").fetchone()
            self.assertEqual((account["cursor"],account["finished"]),("validafter",0))
            # A has already fetched its 8-post slot. Do not double-collect.
            second=p.next_job(now=T)
            self.assertEqual((second["lane"],second["ordinal"]),("A",2))
            raw.close()
            p.db.close()

    def test_does_not_reopen_proven_short_terminal_page(self):
        with tempfile.TemporaryDirectory() as d:
            p=new_planner(d)
            first=p.next_job(now=T)
            p.acknowledge(first,receipt_id="saved",posts=posts(3),
                          next_cursor=None,exhausted=True,now=T)
            raw=sqlite3.connect(":memory:")
            raw.execute("""CREATE TABLE pages(cycle INTEGER,lane TEXT,
                ordinal INTEGER,response_json TEXT)""")
            bd={"media":{"data":posts(3),"paging":{
                "cursors":{"after":"maybeafter"}}}}
            raw.execute("INSERT INTO pages VALUES (?,?,?,?)",
                        (1,"A",1,json.dumps(bd)))
            self.assertEqual(p.reopen_false_exhausted_accounts(raw),[])
            self.assertEqual(p.summary()["finished_accounts"],1)
            raw.close()
            p.db.close()

    def test_reserved_job_is_not_duplicated(self):
        with tempfile.TemporaryDirectory() as d:
            p=new_planner(d)
            j=p.next_job(now=T)
            self.assertIsNone(p.next_job(now=T))
            self.assertEqual(p.pause(now=T)["reason"],"reserved")
            self.assertEqual(p.summary()["reserved_requests"],1)
            p.db.close()


if __name__ == "__main__":
    unittest.main()
