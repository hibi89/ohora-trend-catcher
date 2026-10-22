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


    def test_a_then_b_then_c_with_cursor(self):
        with tempfile.TemporaryDirectory() as d:
            p=CyclePlanner(Path(d)/"planner.sqlite")
            for lane in ("A","B","C"):
                p.set_usage(lane,1)
            for ordinal in range(1,51):
                j=p.next_job()
                self.assertEqual((j["cycle"],j["lane"],j["ordinal"]),(1,"A",ordinal))
                p.acknowledge(j,receipt_id=f"a{ordinal}",posts=[{"id":str(ordinal)}],
                              next_cursor=f"a{ordinal}")
            j=p.next_job()
            self.assertEqual((j["cycle"],j["lane"],j["ordinal"],j["cursor"]),
                             (1,"B",1,"a1"))
            p.acknowledge(j,receipt_id="b1",posts=[],next_cursor="b1")
            p.db.close()

    def test_one_year_cutoff_skips_account(self):
        with tempfile.TemporaryDirectory() as d:
            p=CyclePlanner(Path(d)/"planner.sqlite",start_at=datetime(2026,10,23,tzinfo=timezone.utc))
            p.set_usage("A",1)
            j=p.next_job()
            p.acknowledge(j,receipt_id="first",posts=[{"id":"x"}],
                next_cursor="next",oldest_published_at=datetime(2025,1,1,tzinfo=timezone.utc))
            j2=p.next_job()
            self.assertEqual((j2["lane"],j2["ordinal"]),("A",2))
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
