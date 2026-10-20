"""Full 294-account offline-day batch verification; absolutely no Meta calls.

This is a deterministic synthetic stress/integration test, not a production
throughput promise. It exercises the real manual orchestrator 294 front
accounts, two further front pages for a busy account, one catch-up page,
storage projection, and restart with immutable KST snapshot evidence.
"""
from datetime import datetime,timezone
from pathlib import Path
import tempfile
import unittest

from test_three_api_daily_orchestrator import accounts, envelope, fixture
from three_api_daily_gap import DailyGapLedger
from three_api_daily_orchestrator import OfflineDailyOrchestrator

NOW=datetime(2026,10,19,10,0,tzinfo=timezone.utc)
DAY="2026-10-19"


class FullRosterBatchTests(unittest.TestCase):
    def setUp(self):
        temp=tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        base=Path(temp.name)
        self.source=base/"source.sqlite"
        self.gap=base/"gap.sqlite"
        self.mirror=base/"three_api_raw_mirror.sqlite"
        self.front={(1,1,None):fixture([115,114,113,112],"p2")}
        for i in range(2,295):
            self.front[(i,1,None)]=fixture([30000+i],None)
        self.front[(1,2,"p2")]=fixture([111,110,109,108],"p3")
        self.front[(1,3,"p3")]=fixture([107,106,105,104],"p4")
        self.catchup={("KR_001",DAY,"p4"):envelope([103,102,101,100])}
        baseline=DailyGapLedger(self.gap)
        baseline.seed_previous_head("KR_001","100",day="2026-10-18")
        baseline.close()

    def open(self, front=None, catchup=None):
        obj=OfflineDailyOrchestrator(
            self.source,self.gap,self.mirror,accounts(),DAY,
            self.front if front is None else front,
            self.catchup if catchup is None else catchup,
        )
        self.addCleanup(obj.close)
        return obj

    def _drain_lane(self, runner, lane, max_steps):
        completed=0
        for _ in range(max_steps):
            result=runner.step(lane,now=NOW)
            if result["status"]=="front_committed":
                completed+=1
                continue
            if result["status"] in ("front_lane_unavailable","history_held_for_review"):
                return completed
            self.fail("Unexpected incomplete synthetic batch step: "+result["status"])
        self.fail("Synthetic batch exceeded strict max step budget")

    def test_294_accounts_three_lane_full_front_catchup_and_restart(self):
        runner=self.open()
        self.assertEqual(self._drain_lane(runner,"A",151),150)
        self.assertEqual(self._drain_lane(runner,"B",145),144)
        self.assertEqual(runner.coverage()["front_incomplete"],1)
        self.assertEqual(self._drain_lane(runner,"C",2),1)
        self.assertEqual(runner.step("B",now=NOW)["status"],"front_committed")
        self.assertEqual(runner.coverage()["front_incomplete"],0)
        self.assertEqual(runner.coverage()["unresolved_gap_windows"],1)
        self.assertEqual(runner.step("A",now=NOW)["status"],"applied")
        summary=runner.coverage()
        self.assertEqual((summary["accounts"],summary["daily_rows"]),(294,294))
        self.assertEqual(summary["front_pages"],296)
        self.assertEqual(summary["unresolved_gap_windows"],0)
        self.assertEqual(summary["front_incomplete"],0)
        self.assertEqual(summary["mirror_posts"],309)
        self.assertEqual(summary["mirror_pending_pages"],0)
        self.assertEqual(runner.mirror.summary()["raw_post_media"],309)
        self.assertEqual(runner.mirror.summary()["raw_post_observations"],309)
        self.assertEqual(runner.coordinator.db.execute(
            "SELECT COUNT(*) FROM page_evidence").fetchone()[0],296)
        self.assertEqual(runner.ledger.db.execute(
            "SELECT COUNT(*) FROM page_proof WHERE phase='catchup'").fetchone()[0],1)
        self.assertEqual(runner.ledger.status("KR_001",DAY)["status"],"verified")
        self.assertEqual(runner.step("A",now=NOW)["status"],"history_held_for_review")
        runner.close()
        resumed=self.open(front={},catchup={})
        self.assertEqual(resumed.reconcile()["mirror_posts"],309)
        self.assertEqual(resumed.step("B",now=NOW)["status"],"history_held_for_review")
        self.assertEqual(resumed.mirror.summary()["raw_post_observations"],309)

    def test_restart_does_not_duplicate_294_account_front_evidence(self):
        runner = self.open()
        self.assertEqual(self._drain_lane(runner, "A", 151), 150)
        self.assertEqual(self._drain_lane(runner, "B", 145), 144)
        self.assertEqual(runner.step("C", now=NOW)["status"], "front_committed")
        self.assertEqual(runner.step("B", now=NOW)["status"], "front_committed")
        self.assertEqual(runner.step("A", now=NOW)["status"], "applied")
        before = runner.coverage()
        evidence_before = runner.coordinator.db.execute(
            "SELECT COUNT(*) FROM page_evidence"
        ).fetchone()[0]
        runner.close()
        resumed = self.open(front={}, catchup={})
        after = resumed.reconcile()
        self.assertEqual(after["mirror_posts"], before["mirror_posts"])
        self.assertEqual(resumed.coordinator.db.execute(
            "SELECT COUNT(*) FROM page_evidence"
        ).fetchone()[0], evidence_before)
        self.assertEqual(resumed.mirror.summary()["raw_post_observations"], 309)

    def test_repeated_reconcile_does_not_inflate_full_roster_raw_counts(self):
        runner=self.open()
        self.assertEqual(self._drain_lane(runner,"A",151),150)
        self.assertEqual(self._drain_lane(runner,"B",145),144)
        self.assertEqual(runner.step("C",now=NOW)["status"],"front_committed")
        self.assertEqual(runner.step("B",now=NOW)["status"],"front_committed")
        self.assertEqual(runner.step("A",now=NOW)["status"],"applied")
        initial=runner.coverage()
        initial_raw=runner.mirror.summary()
        for attempt in range(3):
            with self.subTest(attempt=attempt):
                self.assertEqual(runner.reconcile()["mirror_posts"],309)
                self.assertEqual(runner.coverage()["front_pages"],
                                 initial["front_pages"])
                self.assertEqual(runner.mirror.summary()["raw_post_media"],
                                 initial_raw["raw_post_media"])
                self.assertEqual(runner.mirror.summary()["raw_post_observations"],
                                 initial_raw["raw_post_observations"])
        runner.close()
        restarted=self.open(front={},catchup={})
        for attempt in range(2):
            with self.subTest(restart_attempt=attempt):
                self.assertEqual(restarted.reconcile()["mirror_posts"],309)
                self.assertEqual(restarted.mirror.summary()["raw_post_media"],309)
                self.assertEqual(restarted.mirror.summary()["raw_post_observations"],309)

    def test_missing_account_294_blocks_global_gap_without_lying_about_coverage(self):
        self.front.pop((294,1,None))
        runner=self.open()
        self.assertEqual(self._drain_lane(runner,"A",151),150)
        complete=0
        for _ in range(144):
            result=runner.step("B",now=NOW)
            if result["status"]=="front_committed":
                complete+=1
                continue
            self.assertEqual(result["status"],"front_fixture_missing")
            break
        self.assertEqual(complete,143)
        self.assertEqual(runner.coverage()["front_incomplete"],2)
        self.assertEqual(runner.coordinator.db.execute(
            "SELECT COUNT(*) FROM page_evidence").fetchone()[0],293)
        self.assertEqual(runner.ledger.db.execute(
            "SELECT COUNT(*) FROM page_proof WHERE phase='catchup'").fetchone()[0],0)
        self.assertEqual(runner.coverage()["mirror_posts"],(150+143-1)+4)
        # Account1 page2 may progress, but a missing page1 for account294
        # must still prohibit ALL catch-up and history dispatches.
        self.assertEqual(runner.step("C",now=NOW)["status"],"front_committed")
        self.assertEqual(runner.coverage()["front_incomplete"],2)
        self.assertEqual(runner.ledger.db.execute(
            "SELECT COUNT(*) FROM page_proof WHERE phase='catchup'").fetchone()[0],0)


if __name__=="__main__":
    unittest.main()
