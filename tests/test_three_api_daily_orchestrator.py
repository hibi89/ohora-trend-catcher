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
        # Source committed, daily ledger/RAW not yet projected: audit must
        # expose the incomplete three-DB boundary without altering it.
        before=tuple(db.total_changes for db in (
            runner.coordinator.db,runner.ledger.db,runner.mirror.db))
        pending=runner.audit_storage()
        self.assertEqual(pending["source_pages"],1)
        self.assertEqual(pending["unverified_source"],1)
        self.assertFalse(pending["storage_verified"])
        self.assertEqual(tuple(db.total_changes for db in (
            runner.coordinator.db,runner.ledger.db,runner.mirror.db)),before)
        runner.close()
        restarted=self.create(front={})
        r=restarted.reconcile()
        self.assertEqual(r["mirror_posts"],4)
        repaired=restarted.audit_storage()
        self.assertEqual(repaired["verified_pages"],1)
        self.assertEqual(repaired["pages_requiring_attention"],0)
        self.assertTrue(repaired["storage_verified"])
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

    def test_lane_quota_85_and_cooldown_requires_explicit_reset(self):
        runner=self.create()
        self.assertTrue(runner.coordinator.observe_usage("A",85,now=NOW))
        self.assertEqual(runner.step("A",now=NOW)["status"],
                         "front_lane_unavailable")
        self.assertFalse(runner.coordinator.reset_after_verified_cooldown(
            "A",now=NOW+timedelta(minutes=64)))
        self.assertEqual(runner.step("A",now=NOW+timedelta(minutes=66))["status"],
                         "front_lane_unavailable")
        self.assertTrue(runner.coordinator.reset_after_verified_cooldown(
            "A",now=NOW+timedelta(minutes=65)))
        self.assertEqual(runner.step("A",now=NOW+timedelta(minutes=66))["status"],
                         "front_committed")
        self.assertEqual(runner.coordinator.db.execute(
            "SELECT COUNT(*) FROM page_evidence").fetchone()[0],1)

    def test_quota_stop_keeps_uncommitted_raw_and_evidence_empty(self):
        runner=self.create()
        self.assertTrue(runner.coordinator.observe_usage("A",85,now=NOW))
        for attempt in range(3):
            with self.subTest(attempt=attempt):
                self.assertEqual(runner.step("A",now=NOW)["status"],
                                 "front_lane_unavailable")
        self.assertEqual(runner.coordinator.db.execute(
            "SELECT COUNT(*) FROM page_evidence").fetchone()[0],0)
        self.assertEqual(runner.mirror.summary()["raw_posts"],0)
        self.assertEqual(runner.coverage()["front_pages"],0)
        self.assertEqual(runner.coverage()["front_incomplete"],294)

    def test_quota_stop_does_not_prevent_other_lane_commits(self):
        # The default fixture only covers account 1, assigned to lane A.
        # Supply deterministic account fixtures for the unaffected B lane.
        for ordinal in range(2,295):
            self.front[(ordinal,1,None)]=fixture([30000+ordinal],None)
        runner=self.create()
        self.assertTrue(runner.coordinator.observe_usage("A",85,now=NOW))
        self.assertEqual(runner.step("A",now=NOW)["status"],
                         "front_lane_unavailable")
        self.assertEqual(runner.step("B",now=NOW)["status"],"front_committed")
        self.assertEqual(runner.coordinator.db.execute(
            "SELECT COUNT(*) FROM page_evidence").fetchone()[0],1)
        self.assertEqual(runner.mirror.summary()["raw_posts"],4)
        self.assertEqual(runner.step("A",now=NOW)["status"],
                         "front_lane_unavailable")

    def test_lane_missing_usage_halts_only_one_lane(self):
        runner=self.create()
        self.assertTrue(runner.coordinator.observe_usage("A",None,now=NOW))
        self.assertEqual(runner.step("A",now=NOW)["status"],
                         "front_lane_unavailable")
        self.assertEqual(runner.step("B",now=NOW)["status"],
                         "front_fixture_missing")
        self.assertEqual(runner.coordinator.db.execute(
            "SELECT halted FROM lanes WHERE lane='B'").fetchone()[0],0)
        self.assertEqual(runner.coordinator.db.execute(
            "SELECT COUNT(*) FROM page_evidence").fetchone()[0],0)

    def test_leased_page_not_stolen_before_expiry_and_recoverable(self):
        runner=self.create()
        first=runner.coordinator.lease("A",now=NOW)
        self.assertEqual(first["account_ordinal"],1)
        second=runner.coordinator.lease("A",now=NOW+timedelta(minutes=14))
        self.assertEqual(second["account_ordinal"],2)
        runner.coordinator.db.execute(
            "UPDATE jobs SET state='done' WHERE id=?", (second["id"],))
        recovered=runner.coordinator.lease("A",now=NOW+timedelta(minutes=16))
        self.assertEqual(recovered["account_ordinal"],1)
        self.assertNotEqual(recovered["lease_id"],first["lease_id"])
        self.assertEqual(runner.coordinator.db.execute(
            "SELECT COUNT(*) FROM page_evidence").fetchone()[0],0)

    def test_restart_preserves_lane_cooldown(self):
        runner=self.create()
        runner.coordinator.observe_usage("A",85,now=NOW)
        runner.close()
        resumed=self.create()
        self.assertEqual(resumed.step("A",now=NOW+timedelta(minutes=66))["status"],
                         "front_lane_unavailable")
        self.assertTrue(resumed.coordinator.reset_after_verified_cooldown(
            "A",now=NOW+timedelta(minutes=66)))
        self.assertEqual(resumed.step("A",now=NOW+timedelta(minutes=66))["status"],
                         "front_committed")

    def test_tampered_staged_payload_blocks_new_capture_and_recovery(self):
        runner=self.create()
        self.assertEqual(runner.step("A",now=NOW)["status"],"front_committed")
        count=runner.mirror.summary()["raw_posts"]
        runner.coordinator.db.execute(
            "UPDATE staging_posts SET payload_json=? WHERE post_id=?",
            ('{"id":"115","caption":"tampered"}',"115"))
        self.assertEqual(runner.step("B",now=NOW)["status"],"recovery_blocked")
        self.assertEqual(runner.mirror.summary()["raw_posts"],count)
        self.assertEqual(runner.coordinator.db.execute(
            "SELECT COUNT(*) FROM page_evidence").fetchone()[0],1)

    def test_missing_capture_clock_blocks_replay_without_refetch(self):
        runner=self.create()
        self.assertEqual(runner.step("A",now=NOW)["status"],"front_committed")
        runner.coordinator.db.execute(
            "DELETE FROM offline_fixture_clock WHERE job_id=("
            "SELECT job_id FROM page_evidence WHERE account_ordinal=1 AND page=1)")
        self.assertEqual(runner.step("B",now=NOW)["status"],"recovery_blocked")
        self.assertEqual(runner.coordinator.db.execute(
            "SELECT COUNT(*) FROM page_evidence").fetchone()[0],1)

    def test_tampered_capture_clock_prevents_further_projection(self):
        runner=self.create()
        self.assertEqual(runner.step("A",now=NOW)["status"],"front_committed")
        runner.coordinator.db.execute(
            "UPDATE offline_fixture_clock SET observed_at=?",
            ("2026-10-19T11:00:00+00:00",))
        self.assertEqual(runner.step("B",now=NOW)["status"],"recovery_blocked")
        self.assertEqual(runner.coordinator.db.execute(
            "SELECT COUNT(*) FROM page_evidence").fetchone()[0],1)

    def test_bad_a_page_isolated_b_data_survives_then_a_manual_recovery(self):
        self.front[(2,1,None)]=fixture([22001],like_count=-1)
        self.front[(151,1,None)]=fixture([25101])
        self.front[(152,1,None)]=fixture([25201])
        runner=self.create()
        self.assertEqual(runner.step("A",now=NOW)["status"],"front_committed")
        before=runner.mirror.db.execute(
            "SELECT post_id,account_key FROM raw_posts WHERE post_id='115'"
        ).fetchone()
        self.assertEqual(tuple(before),("115","KR_001"))
        self.assertEqual(runner.step("A",now=NOW)["status"],"front_blocked")
        self.assertEqual(runner.coordinator.db.execute(
            "SELECT halted FROM lanes WHERE lane='A'").fetchone()[0],1)
        self.assertEqual(runner.step("B",now=NOW)["status"],"front_committed")
        self.assertEqual(runner.step("B",now=NOW)["status"],"front_committed")
        self.assertEqual(runner.mirror.summary()["raw_posts"],6)
        self.assertEqual(runner.mirror.summary()["raw_post_observations"],6)
        self.assertEqual(runner.coordinator.db.execute(
            "SELECT COUNT(*) FROM page_evidence").fetchone()[0],3)
        self.assertEqual(runner.step("A",now=NOW+timedelta(minutes=66))["status"],
                         "front_lane_unavailable")
        self.front[(2,1,None)]=fixture([22001],like_count=0)
        self.assertTrue(runner.coordinator.reset_after_verified_cooldown(
            "A",now=NOW+timedelta(minutes=66)))
        self.assertEqual(runner.step("A",now=NOW+timedelta(minutes=67))["status"],
                         "front_committed")
        self.assertEqual(runner.mirror.summary()["raw_posts"],7)
        self.assertEqual(runner.mirror.summary()["raw_post_observations"],7)
        self.assertEqual(runner.coordinator.db.execute(
            "SELECT COUNT(*) FROM page_evidence").fetchone()[0],4)
        self.assertEqual(tuple(runner.mirror.db.execute(
            "SELECT post_id,account_key FROM raw_posts WHERE post_id='115'"
        ).fetchone()),("115","KR_001"))

    def test_bad_b_lane_does_not_prevent_a_from_collecting_other_accounts(self):
        self.front[(151,1,None)]=fixture([25101],comments_count=-1)
        self.front[(2,1,None)]=fixture([22001])
        runner=self.create()
        self.assertEqual(runner.step("B",now=NOW)["status"],"front_blocked")
        self.assertEqual(runner.coordinator.db.execute(
            "SELECT halted FROM lanes WHERE lane='B'").fetchone()[0],1)
        self.assertEqual(runner.step("A",now=NOW)["status"],"front_committed")
        self.assertEqual(runner.step("A",now=NOW)["status"],"front_committed")
        self.assertEqual(runner.mirror.summary()["raw_posts"],5)
        self.assertEqual(runner.ledger.status("KR_151",DAY)["front_pages"],0)
        self.assertEqual(runner.ledger.status("KR_001",DAY)["front_pages"],1)
        self.assertEqual(runner.ledger.status("KR_002",DAY)["front_pages"],1)
        self.assertEqual(runner.coordinator.db.execute(
            "SELECT halted FROM lanes WHERE lane='A'").fetchone()[0],0)
        self.assertEqual(runner.coverage()["front_incomplete"],293)

    def test_failed_a_raw_promotion_replays_without_refetch_then_b_continues(self):
        self.front[(151,1,None)]=fixture([25101])
        runner=self.create()
        with mock.patch.object(runner.mirror,"promote_page",
                               side_effect=RuntimeError("synthetic transient failure")):
            self.assertEqual(runner.step("A",now=NOW)["status"],"front_blocked")
        self.assertEqual(runner.mirror.summary()["raw_posts"],0)
        self.assertEqual(runner.mirror.summary()["pending_pages"],1)
        self.assertEqual(runner.coordinator.db.execute(
            "SELECT COUNT(*) FROM page_evidence").fetchone()[0],1)
        self.front.clear()
        self.front[(151,1,None)]=fixture([25101])
        self.assertEqual(runner.step("B",now=NOW)["status"],"front_committed")
        self.assertEqual(runner.mirror.summary()["raw_posts"],5)
        self.assertEqual(runner.mirror.summary()["raw_post_observations"],5)
        self.assertEqual(runner.mirror.summary()["pending_pages"],0)
        self.assertEqual(runner.coordinator.db.execute(
            "SELECT COUNT(*) FROM page_evidence").fetchone()[0],2)
        self.assertEqual(runner.step("A",now=NOW)["status"],
                         "front_lane_unavailable")
        self.assertEqual(runner.reconcile()["mirror_posts"],5)

    def test_cross_account_id_collision_keeps_old_raw_and_globally_blocks(self):
        self.front[(151,1,None)]=fixture([115])
        runner=self.create()
        self.assertEqual(runner.step("A",now=NOW)["status"],"front_committed")
        old_counts=runner.mirror.summary().copy()
        self.assertEqual(runner.step("B",now=NOW)["status"],"front_blocked")
        self.assertEqual(runner.mirror.summary()["raw_posts"],old_counts["raw_posts"])
        self.assertEqual(runner.mirror.summary()["raw_post_observations"],
                         old_counts["raw_post_observations"])
        self.assertEqual(runner.mirror.summary()["raw_post_media"],
                         old_counts["raw_post_media"])
        self.assertEqual(tuple(runner.mirror.db.execute(
            "SELECT account_key,post_id FROM raw_posts WHERE post_id='115'"
        ).fetchone()),("KR_001","115"))
        self.assertEqual(runner.mirror.summary()["pending_pages"],1)
        self.assertEqual(runner.coordinator.db.execute(
            "SELECT halted FROM lanes WHERE lane='B'").fetchone()[0],1)
        self.assertEqual(runner.step("A",now=NOW)["status"],"recovery_blocked")
        self.assertEqual(runner.mirror.summary()["raw_posts"],4)
        self.assertEqual(runner.ledger.status("KR_151",DAY)["front_pages"],1)
        self.assertEqual(runner.coverage()["front_incomplete"],293)

    def test_malformed_b_carousel_blocks_b_but_c_can_stage_verified_page2(self):
        invalid=fixture([25101],media_type="CAROUSEL_ALBUM")
        invalid["payload"]["data"][0]["children"]={
            "data":[{"id":"25102","media_type":"IMAGE"}],
            "paging":{"next":"https://example.invalid/next?secret=DROP"}
        }
        self.front[(151,1,None)]=invalid
        self.front[(1,2,"p2")]=fixture([111])
        runner=self.create()
        self.assertEqual(runner.step("A",now=NOW)["status"],"front_committed")
        self.assertEqual(runner.step("B",now=NOW)["status"],"front_blocked")
        self.assertEqual(runner.step("C",now=NOW)["status"],"front_committed")
        self.assertEqual(runner.mirror.summary()["raw_posts"],5)
        self.assertEqual(runner.ledger.status("KR_151",DAY)["front_pages"],0)
        self.assertEqual(runner.ledger.status("KR_001",DAY)["front_pages"],2)
        self.assertEqual(runner.coordinator.db.execute(
            "SELECT halted FROM lanes WHERE lane='C'").fetchone()[0],0)
        dump="\n".join(runner.coordinator.db.iterdump())
        self.assertNotIn("DROP",dump)

    def test_p4_three_db_audit_proves_existing_media_without_writes(self):
        page=fixture([115],"p2",like_count=0,media_type="CAROUSEL_ALBUM")
        page["payload"]["data"][0]["children"]={
            "data":[{"id":"116","media_type":"IMAGE",
                     "media_url":"https://cdn.example.invalid/nail.jpg"}]
        }
        runner=self.create(front={(1,1,None):page})
        self.assertEqual(runner.step("A",now=NOW)["status"],"front_committed")
        before=tuple(db.total_changes for db in (
            runner.coordinator.db,runner.ledger.db,runner.mirror.db))
        audit=runner.audit_storage()
        self.assertEqual(audit["mode"],"SYNTHETIC_OFFLINE_READ_ONLY_AUDIT")
        self.assertEqual(audit["source_pages"],1)
        self.assertEqual(audit["verified_pages"],1)
        self.assertEqual(audit["pages_requiring_attention"],0)
        self.assertTrue(audit["storage_verified"])
        self.assertFalse(audit["history_dispatch_enabled"])
        self.assertFalse(audit["unattended_execution_enabled"])
        self.assertEqual(tuple(db.total_changes for db in (
            runner.coordinator.db,runner.ledger.db,runner.mirror.db)),before)

    def test_p4_three_db_audit_refuses_empty_collection_claim(self):
        runner=self.create(front={})
        audit=runner.audit_storage()
        self.assertEqual(audit["source_pages"],0)
        self.assertEqual(audit["verified_pages"],0)
        self.assertFalse(audit["storage_verified"])

    def test_p4_three_db_audit_detects_mirror_payload_tampering(self):
        runner=self.create()
        self.assertEqual(runner.step("A",now=NOW)["status"],"front_committed")
        request="front.2026-10-19.001.p1"
        runner.mirror.db.execute(
            "UPDATE page_receipts SET payload_json='[]' WHERE request_id=?",
            (request,))
        audit=runner.audit_storage()
        self.assertEqual(audit["receipt_mismatch"],1)
        self.assertEqual(audit["verified_pages"],0)
        self.assertFalse(audit["storage_verified"])
        self.assertEqual(runner.mirror.summary()["raw_posts"],4)

    def test_p4_three_db_audit_detects_missing_raw_observation(self):
        runner=self.create()
        self.assertEqual(runner.step("A",now=NOW)["status"],"front_committed")
        runner.mirror.db.execute(
            "DELETE FROM raw_post_observations WHERE request_id=? AND post_id=?",
            ("front.2026-10-19.001.p1","115"))
        audit=runner.audit_storage()
        self.assertEqual(audit["observation_mismatch"],1)
        self.assertEqual(audit["verified_pages"],0)
        self.assertFalse(audit["storage_verified"])

    def test_p4_three_db_audit_staged_receipt_is_not_auto_promoted(self):
        runner=self.create()
        self.assertEqual(runner.step("A",now=NOW)["status"],"front_committed")
        runner.mirror.db.execute(
            "UPDATE page_receipts SET state='staged' WHERE request_id=?",
            ("front.2026-10-19.001.p1",))
        before=runner.mirror.db.total_changes
        audit=runner.audit_storage()
        self.assertEqual(audit["staged_receipts"],1)
        self.assertEqual(audit["verified_pages"],0)
        self.assertFalse(audit["storage_verified"])
        self.assertEqual(runner.mirror.db.total_changes,before)

    def test_p4_three_db_audit_separates_clock_loss_and_orphan_receipt(self):
        runner=self.create()
        self.assertEqual(runner.step("A",now=NOW)["status"],"front_committed")
        runner.coordinator.db.execute("DELETE FROM offline_fixture_clock")
        runner.mirror.stage_page(
            "front.2026-10-19.294.p3","KR_294",
            "2026-10-19T18:00:00+09:00",[{"id":"999999"}])
        audit=runner.audit_storage()
        self.assertEqual(audit["missing_clock"],1)
        self.assertEqual(audit["orphan_front_receipts"],1)
        self.assertEqual(audit["verified_pages"],0)
        self.assertFalse(audit["storage_verified"])

    def test_p4_operator_report_explicitly_opts_in_to_integrity(self):
        runner=self.create()
        self.assertEqual(runner.step("A",now=NOW)["status"],"front_committed")
        ordinary=runner.operator_report(now=NOW)
        self.assertNotIn("storage_integrity",ordinary)
        before=tuple(db.total_changes for db in (
            runner.coordinator.db,runner.ledger.db,runner.mirror.db))
        checked=runner.operator_report(now=NOW,include_storage_audit=True)
        self.assertEqual(checked["storage_integrity"]["source_pages"],1)
        self.assertEqual(checked["storage_integrity"]["verified_pages"],1)
        self.assertEqual(checked["storage_integrity"]["pages_requiring_attention"],0)
        self.assertEqual(tuple(db.total_changes for db in (
            runner.coordinator.db,runner.ledger.db,runner.mirror.db)),before)
        with self.assertRaises(OfflineDailyScheduleError):
            runner.operator_report(now=NOW,include_storage_audit="true")

    def test_p4_audit_detects_missing_media_parent(self):
        runner=self.create()
        self.assertEqual(runner.step("A",now=NOW)["status"],"front_committed")
        runner.mirror.db.execute(
            "DELETE FROM raw_post_media WHERE media_id='115' AND media_role='parent'")
        report=runner.operator_report(now=NOW,include_storage_audit=True)
        self.assertEqual(report["storage_integrity"]["observation_mismatch"],1)
        self.assertEqual(report["storage_integrity"]["verified_pages"],0)
        self.assertFalse(report["storage_integrity"]["storage_verified"])

    def test_p4_audit_survives_restart_without_fixture_refetch(self):
        runner=self.create()
        self.assertEqual(runner.step("A",now=NOW)["status"],"front_committed")
        runner.close()
        restarted=self.create(front={},catchup={})
        before=restarted.mirror.db.total_changes
        audit=restarted.audit_storage()
        self.assertTrue(audit["storage_verified"])
        self.assertEqual(audit["verified_pages"],1)
        self.assertEqual(restarted.mirror.db.total_changes,before)
        self.assertEqual(restarted.coordinator.db.execute(
            "SELECT COUNT(*) FROM page_evidence").fetchone()[0],1)

    def test_manual_review_not_masked_as_verified(self):
        runner=self.create(front={})
        for account in accounts():
            runner.ledger.record_page(account["key"],DAY,phase="front",
                post_ids=[str(20000+account["ordinal"])])
        runner.ledger.db.execute(
            "UPDATE daily_window SET status='needs_review' WHERE account_key='KR_001'"
        )
        decision=runner.step("A",now=NOW)
        self.assertEqual(decision["status"],"manual_gap_review_required")
        self.assertEqual(decision["review_accounts"],1)
        self.assertEqual(decision["history_eligible_accounts"],293)
        self.assertFalse(decision["history_dispatch_enabled"])
        before_changes=runner.ledger.db.total_changes
        report=runner.operator_report(now=NOW)
        self.assertEqual(report["policy"]["next_phase_if_storage_verified"],
                         "manual_review")
        self.assertEqual(report["policy"]["manual_review_accounts"],1)
        self.assertEqual(report["policy"]["history_eligible_accounts"],293)
        self.assertFalse(report["policy"]["history_dispatch_enabled"])
        self.assertFalse(report["policy"]["unattended_execution_enabled"])
        self.assertEqual(runner.ledger.db.total_changes,before_changes)
        self.assertEqual(runner.coordinator.db.execute(
            "SELECT COUNT(*) FROM page_evidence").fetchone()[0],0)
        self.assertEqual(runner.mirror.summary()["raw_posts"],0)
        # The same account-scoped decision survives process restart.
        runner.close()
        back=self.create(front={},catchup={})
        reopened=back.step("B",now=NOW)
        self.assertEqual(reopened["review_accounts"],1)
        self.assertEqual(reopened["history_eligible_accounts"],293)
        self.assertEqual(reopened["status"],"manual_gap_review_required")
        self.assertEqual(back.ledger.status("KR_001",DAY)["status"],
                         "needs_review")

    def test_front_barrier_does_not_prematurely_report_history_eligibility(self):
        runner=self.create(front={})
        runner.ledger.db.execute(
            "UPDATE daily_window SET status='needs_review' WHERE account_key='KR_001'"
        )
        report=runner.operator_report(now=NOW)
        self.assertEqual(report["policy"]["next_phase_if_storage_verified"],
                         "front")
        self.assertEqual(report["policy"]["manual_review_accounts"],0)
        self.assertEqual(report["policy"]["history_eligible_accounts"],0)
        self.assertEqual(runner.step("A",now=NOW)["status"],
                         "front_fixture_missing")
