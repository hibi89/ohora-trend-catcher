"""Offline regression for per-lane P50 usage recovery; never calls Meta."""
from datetime import datetime, timedelta, timezone
from contextlib import closing
from pathlib import Path
import json
import sqlite3
import sys
import tempfile
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "raw_v2"))

from three_api_p50_quota_recovery import (
    probe_lane, quota_from_caller_headers, release_verified_lane,
)


class QuotaRecoveryTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.db = Path(self.tmp.name) / "coordinator.sqlite"
        with closing(sqlite3.connect(self.db)) as db, db:
            db.execute("CREATE TABLE lanes(lane TEXT PRIMARY KEY, halted INTEGER, reason TEXT, ready_at TEXT)")
            for lane in "ABC":
                db.execute("INSERT INTO lanes VALUES(?,?,?,?)",
                           (lane, 1, "usage_limit", "2026-10-22T00:00:00+00:00"))
        self.now = datetime(2026, 10, 23, tzinfo=timezone.utc)
        self.ok = {"lane": "B", "http_status": 200, "usage_percent": 6,
                   "quota_verified": True}

    def test_scoped_telemetry(self):
        header = {"x-business-use-case-usage": json.dumps({
            "OTHER": [{"type": "instagram", "call_count": 99, "total_time": 99,
                       "total_cputime": 0, "estimated_time_to_regain_access": 0}],
            "CALLER": [{"type": "instagram", "call_count": 6, "total_time": 41,
                        "total_cputime": 0, "estimated_time_to_regain_access": 0}],
        })}
        self.assertEqual(quota_from_caller_headers(header, "CALLER"), 41)
        self.assertIsNone(quota_from_caller_headers(header, "MISSING"))
        self.assertIsNone(quota_from_caller_headers({}, "CALLER"))
        header["x-business-use-case-usage"] = "not-json"
        self.assertIsNone(quota_from_caller_headers(header, "CALLER"))

    def test_no_recovery_without_below_threshold(self):
        for value in [None, 85, 91]:
            observation = dict(self.ok, usage_percent=value)
            self.assertFalse(release_verified_lane(self.db, "B", observation, now=self.now))
        self.assertFalse(release_verified_lane(self.db, "B",
                         dict(self.ok, http_status=429), now=self.now))
        self.assertFalse(release_verified_lane(self.db, "B",
                         dict(self.ok, quota_verified=False), now=self.now))

    def test_only_selected_lane_released(self):
        self.assertTrue(release_verified_lane(self.db, "B", self.ok, now=self.now))
        with closing(sqlite3.connect(self.db)) as db, db:
            self.assertEqual(db.execute("SELECT lane,halted FROM lanes ORDER BY lane").fetchall(),
                             [("A", 1), ("B", 0), ("C", 1)])
        self.assertFalse(release_verified_lane(self.db, "B", self.ok, now=self.now))

    def test_verified_meta_recovery_overrides_local_cooldown_but_not_other_reasons(self):
        later = (self.now + timedelta(hours=1)).isoformat()
        with closing(sqlite3.connect(self.db)) as db, db:
            db.execute("UPDATE lanes SET ready_at=? WHERE lane='B'", (later,))
        self.assertTrue(release_verified_lane(self.db, "B", self.ok, now=self.now))
        with closing(sqlite3.connect(self.db)) as db, db:
            db.execute("UPDATE lanes SET halted=1,reason='missing_usage',ready_at=? WHERE lane='B'",
                       ("2026-10-22T00:00:00+00:00",))
        self.assertFalse(release_verified_lane(self.db, "B", self.ok, now=self.now))

    def test_probe_one_request_mocked(self):
        class Dummy:
            status = 200
            headers = {"x-business-use-case-usage": json.dumps({
                "123": [{"type": "instagram", "call_count": 2, "total_time": 5,
                         "total_cputime": 0, "estimated_time_to_regain_access": 0}]})}
            def __enter__(self): return self
            def __exit__(self, *args): pass
        calls = []
        def sender(request, timeout):
            calls.append((request.method, timeout))
            return Dummy()
        env = {"P6_META_IG_ID_B": "123", "P6_META_TOKEN_B": "test-token",
               "P6_META_GRAPH_VERSION": "v26.0"}
        result = probe_lane("B", env, sender=sender)
        self.assertEqual(calls, [("GET", 15)])
        self.assertTrue(result["quota_verified"])
        self.assertEqual(result["usage_percent"], 5)


if __name__ == "__main__":
    unittest.main()
