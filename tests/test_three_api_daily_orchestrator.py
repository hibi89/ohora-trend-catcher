"""Synthetic-only 294-account scheduler integration and restart regression."""
from datetime import datetime, timedelta, timezone
from pathlib import Path
import json
import sys
import tempfile
import unittest
from unittest import mock

sys.path.insert(0,str(Path(__file__).resolve().parents[1]/"raw_v2"))
from three_api_daily_orchestrator import (
    OfflineDailyOrchestrator, OfflineDailyScheduleError,
)
from three_api_daily_gap import DailyGapLedger
from three_api_evidence import PageEvidenceStore

NOW=datetime(2026,10,19,10,0,tzinfo=timezone.utc)
DAY="2026-10-19"


def accounts():
    return [{"ordinal":i,"key":f"KR_{i:03}","username":f"nails_{i}"}
            for i in range(1,295)]


def envelope(ids, after=None, **extra):
    p={"data":[{"id":str(i),"caption":"synthetic",**extra} for i in ids]}
    if after:
        p["paging"]={"next":"offline-next-page","cursors":{"after":after}}
    return p


def fixture(ids, after=None, observed="2026-10-19T18:00:00+09:00",**extra):
    return {"payload":envelope(ids,after,**extra),"observed_at":observed}


class OfflineDailyOrchestratorTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        root=Path(self.temp.name)
        self.source=root/"source.sqlite"
        self.ledger=root/"gap.sqlite"
        self.mirror=root/"three_api_raw_mirror.sqlite"
        self.front={(1,1,None):fixture([115,114,113,112],"p2",like_count=0)}
        self.catchup={}
        self.objects=[]

    def create(self, day=DAY, roster=None, front=None, catchup=None):
        runner=OfflineDailyOrchestrator(
            self.source,self.ledger,self.mirror,
            accounts() if roster is None else roster,day,
            self.front if front is None else front,
            self.catchup if catchup is None else catchup,
        )
        self.addCleanup(runner.close)
        self.objects.append(runner)
        return runner

    def test_294_binding_and_front_priority(self):
        runner=self.create()
        report=runner.coverage()
        self.assertEqual((report["accounts"],report["daily_rows"],report["front_incomplete"]),
                         (294,294,294))
        self.assertEqual(report["action_counts"],{"front":294})
        result=runner.step("A",now=NOW)
        self.assertEqual(result["status"],"front_committed")
        self.assertEqual((result["account_ordinal"],result["page"]),(1,1))
        self.assertEqual(runner.coverage()["front_incomplete"],294)
        self.assertEqual(runner.coverage()["front_pages"],1)
        self.assertEqual(runner.mirror.summary()["raw_posts"],4)

    def test_missing_fixture_does_not_lease_or_skip_other_account(self):
        runner=self.create(front={})
        self.assertEqual(runner.step("A",now=NOW)["status"],"front_fixture_missing")
        self.assertEqual(runner.step("A",now=NOW)["status"],"front_fixture_missing")
        self.assertEqual(runner.coordinator.db.execute(
            "SELECT COUNT(*) FROM jobs WHERE state='leased'").fetchone()[0],0)
        self.assertEqual(runner.coverage()["front_incomplete"],294)
        self.assertEqual(runner.mirror.summary()["raw_posts"],0)

    def test_direct_raw_projection_from_normalized_child_page(self):
        self.front[(1,1,None)]=fixture([115],None,media_type="IMAGE",
                                        extra_unapproved="NOT_PERSIST",
                                        like_count=0)
        runner=self.create()
        self.assertEqual(runner.step("A",now=NOW)["status"],"front_committed")
        row=runner.mirror.db.execute(
            "SELECT like_count,like_present FROM raw_post_observations").fetchone()
        self.assertEqual(tuple(row),(0,1))
        self.assertNotIn("NOT_PERSIST","\n".join(runner.mirror.db.iterdump()))

    def test_source_commit_crash_recovers_without_fixture_refetch(self):
        runner=self.create()
        job=runner.coordinator.lease("A",now=NOW)
        self.assertEqual(job["account_ordinal"],1)
        normalized=envelope([115,114,113,112],"p2")
        # A synthetic predecessor clock is durably recorded before page commit.
        runner.coordinator.db.execute(
            "INSERT INTO offline_fixture_clock VALUES (?,?,?)",
            (job["id"],"2026-10-19T09:00:00+00:00",
             __import__("hashlib").sha256(json.dumps(
                 normalized,sort_keys=True,ensure_ascii=False,
                 separators=(",",":")).encode()).hexdigest())
        )
        runner.evidence.commit_staged_page(
            job["id"],job["lease_id"],normalized,now=NOW)
        self.assertEqual(runner.mirror.summary()["raw_posts"],0)
        runner.close()
        restarted=self.create(front={})
        r=restarted.reconcile()
        self.assertEqual(r["mirror_posts"],4)
        self.assertEqual(r["front_pages"],1)
        self.assertEqual(restarted.mirror.db.execute(
            "SELECT COUNT(*) FROM raw_post_observations").fetchone()[0],4)
        self.assertEqual(restarted.reconcile()["mirror_posts"],4)

    def test_raw_failure_halts_lane_then_replay_restores(self):
        runner=self.create()
        with mock.patch.object(runner.raw,"project_page",side_effect=RuntimeError("fake failure")):
            result=runner.step("A",now=NOW)
        self.assertEqual(result["status"],"front_blocked")
        self.assertEqual(runner.mirror.summary()["raw_posts"],0)
        self.assertEqual(runner.coordinator.db.execute(
            "SELECT halted FROM lanes WHERE lane='A'").fetchone()[0],1)
        self.assertEqual(runner.reconcile()["mirror_posts"],4)
        self.assertEqual(runner.coordinator.db.execute(
            "SELECT COUNT(*) FROM page_evidence").fetchone()[0],1)

    def test_invalid_fixture_does_not_commit_page_or_advance(self):
        bad=fixture([115,114,113,112],"p2")
        bad["payload"]["data"][0]["like_count"]=-5
        runner=self.create(front={(1,1,None):bad})
        self.assertEqual(runner.step("A",now=NOW)["status"],"front_blocked")
        self.assertEqual(runner.coordinator.db.execute(
            "SELECT COUNT(*) FROM page_evidence").fetchone()[0],0)
        self.assertEqual(runner.ledger.status("KR_001",DAY)["front_pages"],0)
        self.assertEqual(runner.mirror.summary()["raw_posts"],0)

    def test_coverage_reports_no_false_294_complete(self):
        runner=self.create()
        runner.step("A",now=NOW)
        c=runner.coverage()
        self.assertEqual(c["accounts"],294)
        self.assertEqual(c["front_incomplete"],294)
        self.assertLess(c["mirror_posts"],294)
        self.assertEqual(c["unresolved_gap_windows"],0)

    def test_day_binding_refuses_attribution_to_different_date(self):
        runner=self.create()
        runner.step("A",now=NOW)
        runner.close()
        with self.assertRaises(Exception):
            self.create(day="2026-10-20",front={})

    def test_official_roster_identity_change_refused(self):
        runner=self.create()
        runner.close()
        changed=accounts()
        changed[0]["username"]="renamed"
        with self.assertRaises(Exception):
            self.create(roster=changed,front={})

    def test_wrong_roster_size_refused_before_db_write(self):
        with self.assertRaises(OfflineDailyScheduleError):
            self.create(roster=accounts()[:293])
        self.assertFalse(self.source.exists())

    def test_shared_or_production_path_refused(self):
        for path in (self.source,self.ledger):
            with self.subTest(path=path),self.assertRaises(OfflineDailyScheduleError):
                OfflineDailyOrchestrator(self.source,self.ledger,path,accounts(),DAY,{}, {})
        with self.assertRaises(OfflineDailyScheduleError):
            OfflineDailyOrchestrator(self.source,self.ledger,
                self.source.parent/"collection.sqlite",accounts(),DAY,{}, {})

    def test_recovery_refuses_missing_capture_clock(self):
        runner=self.create()
        job=runner.coordinator.lease("A",now=NOW)
        runner.evidence.commit_staged_page(
            job["id"],job["lease_id"],envelope([115],"p2"),now=NOW)
        self.assertEqual(runner.step("B",now=NOW)["status"],"recovery_blocked")
        self.assertEqual(runner.mirror.summary()["raw_posts"],0)

    def test_invalid_lane_and_naive_time_refused(self):
        runner=self.create()
        with self.assertRaises(OfflineDailyScheduleError):
            runner.step("D",now=NOW)
        with self.assertRaises(OfflineDailyScheduleError):
            runner.step("A",now=datetime(2026,10,19))
        with self.assertRaises(OfflineDailyScheduleError):
            runner.step("A",now=NOW,stage_only=1)

    def test_history_is_never_dispatched(self):
        runner=self.create(front={})
        for account in accounts():
            # Synthetic completed first pages; no full RAW claim is made.
            runner.ledger.record_page(
                account["key"],DAY,phase="front",post_ids=[str(20000+account["ordinal"])]
            )
        result=runner.step("A",now=NOW)
        self.assertEqual(result["status"],"history_held_for_review")
        self.assertEqual(result["eligible_accounts"],294)
        self.assertEqual(runner.coordinator.db.execute(
            "SELECT COUNT(*) FROM page_evidence").fetchone()[0],0)

    def test_global_front_blocks_catchup_until_all_294_done(self):
        baseline=DailyGapLedger(self.ledger)
        baseline.seed_previous_head("KR_001","100",day="2026-10-18")
        baseline.close()
        self.catchup[("KR_001",DAY,"p4")]=envelope([103,102,101,100])
        runner=self.create(front={})
        for cursor,ids,nxt in (
            (None,[115,114,113,112],"p2"),
            ("p2",[111,110,109,108],"p3"),
            ("p3",[107,106,105,104],"p4"),
        ):
            runner.ledger.record_page("KR_001",DAY,phase="front",
                requested_cursor=cursor,post_ids=[str(i) for i in ids],next_cursor=nxt)
        self.assertEqual(runner.step("B",now=NOW)["status"],"front_fixture_missing")
        self.assertEqual(runner.coordinator.db.execute(
            "SELECT COUNT(*) FROM page_evidence").fetchone()[0],0)
        for account in accounts()[1:]:
            runner.ledger.record_page(account["key"],DAY,phase="front",
                post_ids=[str(20000+account["ordinal"])])
        self.assertEqual(runner.coverage()["front_incomplete"],0)
        # All accounts have been given explicit synthetic front proofs;
        # catch-up now may run as a separate, older-window priority.
        result=runner.step("A",now=NOW)
        self.assertEqual(result["status"],"applied")
        self.assertEqual(result["phase"],"catchup")
        self.assertEqual(runner.mirror.summary()["raw_posts"],4)
        self.assertEqual(runner.ledger.status("KR_001",DAY)["status"],"verified")

    def test_catchup_crash_stage_replays_before_history(self):
        baseline=DailyGapLedger(self.ledger)
        baseline.seed_previous_head("KR_001","100",day="2026-10-18")
        baseline.close()
        self.catchup[("KR_001",DAY,"p4")]=envelope([103,102,101,100])
        runner=self.create(front={})
        for cursor,ids,nxt in (
            (None,[115,114,113,112],"p2"),
            ("p2",[111,110,109,108],"p3"),
            ("p3",[107,106,105,104],"p4"),
        ):
            runner.ledger.record_page("KR_001",DAY,phase="front",
                requested_cursor=cursor,post_ids=[str(i) for i in ids],next_cursor=nxt)
        for account in accounts()[1:]:
            runner.ledger.record_page(account["key"],DAY,phase="front",
                post_ids=[str(20000+account["ordinal"])])
        self.assertEqual(runner.step("A",now=NOW,stage_only=True)["status"],
                         "staged_unapplied")
        runner.close()
        back=self.create(front={},catchup={})
        self.assertEqual(back.step("B",now=NOW)["status"],"history_held_for_review")
        self.assertEqual(back.mirror.summary()["raw_posts"],4)
        self.assertEqual(back.ledger.status("KR_001",DAY)["status"],"verified")

    def test_manual_review_not_masked_as_verified(self):
        runner=self.create(front={})
        for account in accounts():
            runner.ledger.record_page(account["key"],DAY,phase="front",
                post_ids=[str(20000+account["ordinal"])])
        runner.ledger.db.execute(
            "UPDATE daily_window SET status='needs_review' WHERE account_key='KR_001'"
        )
        self.assertEqual(runner.step("A",now=NOW)["status"],
                         "manual_gap_review_required")
