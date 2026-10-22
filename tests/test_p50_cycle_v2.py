"""Offline contract tests for standalone P50 cycle planner."""
import sys
import tempfile
from pathlib import Path
from datetime import datetime, timezone, timedelta
import unittest

sys.path.insert(0,str(Path(__file__).resolve().parents[1]/"raw_v2"))
from p50_cycle_engine_v2 import CyclePlanner


class CycleTests(unittest.TestCase):
    def test_fractional_quota_and_immediate_b_switch(self):
        with tempfile.TemporaryDirectory() as d:
            p=CyclePlanner(Path(d)/'planner.sqlite')
            p.set_usage('A',86.0)
            p.set_usage('B',1.0)
            p.set_usage('C',1.0)
            j=p.next_job()
            self.assertEqual((j['lane'],j['ordinal']),('B',1))
            self.assertEqual(p.summary()['lanes'][0]['halted'],1)
            p.db.close()


    def test_account_one_until_exhaustion_then_two(self):
        with tempfile.TemporaryDirectory() as d:
            p=CyclePlanner(Path(d)/"planner.sqlite")
            for lane in ("A","B","C"):
                p.set_usage(lane,1)
            for i in range(25):
                j=p.next_job()
                self.assertEqual((j["ordinal"],j["lane"],j["limit"]),(1,"A",8))
                self.assertEqual(j["cursor"],None if i==0 else f"c{i-1}")
                p.acknowledge(j,receipt_id=f"r{i}",posts=[{"id":str(i)}],
                              next_cursor=f"c{i}")
            p.set_usage("A",86)
            j=p.next_job()
            self.assertEqual((j["ordinal"],j["lane"],j["cursor"]),(1,"B","c24"))
            p.acknowledge(j,receipt_id="last",posts=[{"id":"last"}],
                          next_cursor=None,exhausted=True)
            j=p.next_job()
            self.assertEqual((j["ordinal"],j["lane"],j["cursor"]),(2,"B",None))
            p.db.close()

    def test_no_year_cutoff(self):
        with tempfile.TemporaryDirectory() as d:
            p=CyclePlanner(Path(d)/"planner.sqlite",
                           start_at=datetime(2026,10,23,tzinfo=timezone.utc))
            p.set_usage("A",1)
            j=p.next_job()
            p.acknowledge(j,receipt_id="old",posts=[{"id":"old"}],
                          next_cursor="next",
                          oldest_published_at=datetime(2020,1,1,tzinfo=timezone.utc))
            j=p.next_job()
            self.assertEqual((j["ordinal"],j["cursor"]),(1,"next"))
            p.db.close()

    def test_reserved_request_not_resent(self):
        with tempfile.TemporaryDirectory() as d:
            p=CyclePlanner(Path(d)/"planner.sqlite")
            p.set_usage("A",1)
            j=p.next_job()
            self.assertIsNone(p.next_job())
            self.assertEqual(p.summary()["reserved_requests"],1)
            p.db.close()


if __name__=="__main__":
    unittest.main()
