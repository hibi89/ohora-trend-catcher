import sys
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'raw_v2'))
from p50_quota_policy import choose_lane

class QuotaPolicyTests(unittest.TestCase):
    def test_lane_switch(self):
        self.assertEqual(choose_lane({'A':84,'B':1,'C':1})[0],'A')
        self.assertEqual(choose_lane({'A':85,'B':84,'C':1})[0],'B')
        self.assertEqual(choose_lane({'A':85,'B':85,'C':84})[0],'C')
    def test_cooldown(self):
        now=datetime(2026,10,23,tzinfo=timezone.utc)
        lane,until=choose_lane({'A':85,'B':85,'C':85},now=now)
        self.assertIsNone(lane)
        self.assertEqual(until,now+timedelta(minutes=63))
if __name__=='__main__':
    unittest.main()
