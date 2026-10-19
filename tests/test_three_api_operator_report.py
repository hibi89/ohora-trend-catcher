"""Read-only synthetic 294-account operational diagnostic regression tests.

The diagnostics themselves must never fetch a fixture, change SQLite state,
read Meta credentials or mislabel synthetic coverage as real production data.
"""
from datetime import datetime,timedelta,timezone
from pathlib import Path
import sys
import tempfile
import unittest
from unittest import mock

sys.path.insert(0,str(Path(__file__).resolve().parents[1]/"raw_v2"))
from three_api_daily_orchestrator import OfflineDailyOrchestrator,OfflineDailyScheduleError
from three_api_daily_gap import DailyGapLedger
from test_three_api_daily_orchestrator import accounts,fixture,envelope,NOW,DAY


class OperatorReportTests(unittest.TestCase):
    def setUp(self):
        tmp=tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        root=Path(tmp.name)
        self.source=root/"source.sqlite"
        self.ledger=root/"ledger.sqlite"
        self.mirror=root/"three_api_raw_mirror.sqlite"
        self.front={(1,1,None):fixture([115,114,113,112],"p2")}
        self.catchup={}
        self.obj=None

    def create(self,front=None,catchup=None):
        obj=OfflineDailyOrchestrator(
            self.source,self.ledger,self.mirror,accounts(),DAY,
            self.front if front is None else front,
            self.catchup if catchup is None else catchup)
        self.addCleanup(obj.close)
        self.obj=obj
        return obj

    @staticmethod
    def dump(obj):
        return tuple("\n".join(conn.iterdump()) for conn in
                     (obj.coordinator.db,obj.ledger.db,obj.mirror.db))

    def test_initial_294_accounts_missing_report_is_bounded_read_only(self):
        obj=self.create()
        before=self.dump(obj)
        report=obj.operator_report(now=NOW)
        self.assertEqual(before,self.dump(obj))
        self.assertEqual(report["mode"],"SYNTHETIC_OFFLINE_MANUAL_ONLY")
        self.assertTrue(report["read_only"])
        self.assertFalse(report["live_meta_verified"])
        self.assertEqual(report["integrity"],"not_fully_revalidated")
        self.assertEqual(report["daily_front"]["roster_accounts"],294)
        self.assertEqual(report["daily_front"]["first_page_missing"],294)
        self.assertEqual(report["daily_front"]["first_page_committed"],0)
        self.assertEqual(report["daily_front"]["front_window_open"],294)
        self.assertEqual(report["daily_front"]["first_page_proof_gap_in_closed_windows"],0)
        self.assertIn("첫 페이지 확정 증거",report["daily_front"]["completion_warning"])
        self.assertEqual(len(report["daily_front"]["first_page_missing_sample"]),20)
        self.assertTrue(report["daily_front"]["first_page_missing_sample_truncated"])
        self.assertEqual(report["daily_front"]["first_page_missing_sample"][0],"KR_001")
        self.assertEqual(report["policy"]["next_phase_if_storage_verified"],"front")
        self.assertFalse(report["policy"]["unattended_execution_enabled"])
        self.assertFalse(report["storage"]["recovery_attention"])
        self.assertEqual({item["lane"] for item in report["lanes"]},set("ABC"))

    def test_one_committed_page_reduces_gap_without_claiming_day_complete(self):
        obj=self.create()
        self.assertEqual(obj.step("A",now=NOW)["status"],"front_committed")
        before=self.dump(obj)
        report=obj.operator_report(now=NOW)
        self.assertEqual(before,self.dump(obj))
        self.assertEqual(report["daily_front"]["first_page_missing"],293)
        self.assertEqual(report["daily_front"]["first_page_committed"],1)
        self.assertEqual(report["daily_front"]["source_front_page_proofs"],1)
        self.assertEqual(report["daily_front"]["front_window_open"],294)
        self.assertEqual(report["storage"]["raw_posts"],4)
        self.assertEqual(report["storage"]["raw_media"],4)
        self.assertEqual(report["storage"]["raw_observations"],4)
        self.assertEqual(report["storage"]["source_pages_without_raw_receipt"],0)
        self.assertFalse(report["storage"]["recovery_attention"])
        self.assertNotIn("KR_001",report["daily_front"]["first_page_missing_sample"])

    def test_lane_halt_and_reset_eligibility_respects_time_without_resetting(self):
        obj=self.create()
        obj.coordinator.observe_usage("A",85,now=NOW)
        obj.coordinator.observe_usage("B",None,now=NOW)
        before=self.dump(obj)
        early=obj.operator_report(now=NOW+timedelta(minutes=64))
        late=obj.operator_report(now=NOW+timedelta(minutes=65))
        self.assertEqual(before,self.dump(obj))
        a,b,c=late["lanes"]
        self.assertEqual(a["lane"],"A")
        self.assertTrue(a["halted"])
        self.assertEqual(a["reason"],"usage_limit")
        self.assertTrue(a["requires_manual_reset"])
        self.assertFalse(early["lanes"][0]["reset_time_reached"])
        self.assertTrue(a["reset_time_reached"])
        self.assertEqual(a["ready_at_utc"],
                         (NOW+timedelta(minutes=65)).isoformat())
        self.assertEqual(b["reason"],"missing_usage")
        self.assertTrue(b["reset_time_reached"])
        self.assertFalse(c["halted"])
        self.assertFalse(c["requires_manual_reset"])
        self.assertIsNone(c["ready_at_utc"])

    def test_restart_retains_lane_halt_and_report_does_not_mutate(self):
        obj=self.create()
        obj.coordinator.observe_usage("A",85,now=NOW)
        obj.close()
        reopened=self.create(front={})
        before=self.dump(reopened)
        report=reopened.operator_report(now=NOW+timedelta(minutes=66))
        self.assertEqual(before,self.dump(reopened))
        self.assertTrue(report["lanes"][0]["requires_manual_reset"])
        self.assertTrue(report["lanes"][0]["reset_time_reached"])
        self.assertEqual(report["daily_front"]["first_page_missing"],294)

    def test_active_and_expired_leases_reported_without_releasing(self):
        obj=self.create()
        first=obj.coordinator.lease("A",now=NOW)
        second=obj.coordinator.lease("A",now=NOW+timedelta(minutes=1))
        self.assertEqual((first["account_ordinal"],second["account_ordinal"]),(1,2))
        before=self.dump(obj)
        early=obj.operator_report(now=NOW+timedelta(minutes=14))
        late=obj.operator_report(now=NOW+timedelta(minutes=17))
        self.assertEqual(before,self.dump(obj))
        self.assertEqual(early["lanes"][0]["inflight_leases"],2)
        self.assertEqual(early["lanes"][0]["expired_leases"],0)
        self.assertEqual(late["lanes"][0]["expired_leases"],2)
        self.assertEqual(obj.coordinator.db.execute(
            "SELECT COUNT(*) FROM jobs WHERE state='leased'").fetchone()[0],2)

    def test_source_commit_before_raw_promotion_reports_recovery_attention(self):
        obj=self.create()
        with mock.patch.object(obj.mirror,"promote_page",side_effect=RuntimeError):
            self.assertEqual(obj.step("A",now=NOW)["status"],"front_blocked")
        before=self.dump(obj)
        report=obj.operator_report(now=NOW)
        self.assertEqual(before,self.dump(obj))
        self.assertEqual(report["daily_front"]["first_page_committed"],1)
        self.assertEqual(report["storage"]["source_pages_without_raw_receipt"],0)
        self.assertEqual(report["storage"]["source_pages_with_staged_raw_receipt"],1)
        self.assertEqual(report["storage"]["raw_staged_receipts"],1)
        self.assertEqual(report["storage"]["raw_posts"],0)
        self.assertTrue(report["storage"]["recovery_attention"])
        self.assertTrue(report["policy"]["must_reconcile_before_dispatch"])
        self.assertEqual(obj.reconcile()["mirror_posts"],4)
        recovered=obj.operator_report(now=NOW)
        self.assertFalse(recovered["storage"]["recovery_attention"])
        self.assertEqual(recovered["storage"]["raw_staged_receipts"],0)

    def test_source_commit_before_mirror_stage_reports_missing_raw(self):
        obj=self.create()
        with mock.patch.object(obj.raw,"project_page",side_effect=RuntimeError):
            self.assertEqual(obj.step("A",now=NOW)["status"],"front_blocked")
        report=obj.operator_report(now=NOW)
        self.assertEqual(report["storage"]["source_pages_without_raw_receipt"],1)
        self.assertEqual(report["storage"]["raw_staged_receipts"],0)
        self.assertTrue(report["storage"]["recovery_attention"])
        self.assertEqual(obj.reconcile()["mirror_posts"],4)
        self.assertFalse(obj.operator_report(now=NOW)["storage"]["recovery_attention"])

    def test_missing_clock_evidence_requires_attention_without_mutation(self):
        obj=self.create()
        self.assertEqual(obj.step("A",now=NOW)["status"],"front_committed")
        obj.coordinator.db.execute("DELETE FROM offline_fixture_clock")
        before=self.dump(obj)
        report=obj.operator_report(now=NOW)
        self.assertEqual(before,self.dump(obj))
        self.assertEqual(report["storage"]["source_pages_without_capture_clock"],1)
        self.assertTrue(report["policy"]["must_reconcile_before_dispatch"])
        self.assertEqual(obj.step("B",now=NOW)["status"],"recovery_blocked")

    def test_colliding_account_post_does_not_overwrite_raw_and_reports_staged(self):
        self.front[(151,1,None)]=fixture([115])
        obj=self.create()
        self.assertEqual(obj.step("A",now=NOW)["status"],"front_committed")
        self.assertEqual(obj.step("B",now=NOW)["status"],"front_blocked")
        before=self.dump(obj)
        report=obj.operator_report(now=NOW)
        self.assertEqual(before,self.dump(obj))
        self.assertEqual(report["daily_front"]["first_page_committed"],2)
        self.assertEqual(report["storage"]["raw_posts"],4)
        self.assertEqual(report["storage"]["raw_staged_receipts"],1)
        self.assertTrue(report["storage"]["recovery_attention"])
        self.assertEqual(obj.step("A",now=NOW)["status"],"recovery_blocked")

    def test_bad_b_account_other_lane_progress_visible_without_false_completion(self):
        self.front[(151,1,None)]=fixture([25101],comments_count=-1)
        self.front[(2,1,None)]=fixture([22001])
        obj=self.create()
        self.assertEqual(obj.step("B",now=NOW)["status"],"front_blocked")
        self.assertEqual(obj.step("A",now=NOW)["status"],"front_committed")
        self.assertEqual(obj.step("A",now=NOW)["status"],"front_committed")
        rep=obj.operator_report(now=NOW)
        self.assertTrue(rep["lanes"][1]["halted"])
        self.assertFalse(rep["lanes"][0]["halted"])
        self.assertEqual(rep["daily_front"]["first_page_committed"],2)
        self.assertEqual(rep["daily_front"]["first_page_missing"],292)
        self.assertEqual(rep["storage"]["raw_posts"],5)
        self.assertEqual(rep["policy"]["next_phase_if_storage_verified"],"front")

    def test_history_eligibility_report_remains_manual_only(self):
        obj=self.create(front={})
        for account in accounts():
            obj.ledger.record_page(
                account["key"],DAY,phase="front",
                post_ids=[str(20000+account["ordinal"])])
        before=self.dump(obj)
        report=obj.operator_report(now=NOW)
        self.assertEqual(before,self.dump(obj))
        self.assertEqual(report["daily_front"]["front_window_closed"],294)
        self.assertEqual(report["daily_front"]["first_page_committed"],0)
        self.assertEqual(report["daily_front"]["first_page_proof_gap_in_closed_windows"],294)
        self.assertIn("수집 완료로 표시하지 말고",report["daily_front"]["completion_warning"])
        self.assertEqual(report["policy"]["next_phase_if_storage_verified"],
                         "history_held")
        self.assertFalse(report["policy"]["unattended_execution_enabled"])

    def test_single_closed_window_without_source_proof_is_not_completion(self):
        obj=self.create(front={})
        obj.ledger.record_page("KR_001",DAY,phase="front",post_ids=["20001"])
        before=self.dump(obj)
        report=obj.operator_report(now=NOW)
        self.assertEqual(before,self.dump(obj))
        self.assertEqual(report["daily_front"]["front_window_closed"],1)
        self.assertEqual(report["daily_front"]["first_page_committed"],0)
        self.assertEqual(report["daily_front"]["first_page_proof_gap_in_closed_windows"],1)
        self.assertIn("수집 완료로 표시하지 말고",report["daily_front"]["completion_warning"])
        self.assertFalse(report["live_meta_verified"])

    def test_catchup_and_manual_review_counted_from_old_gap_windows(self):
        base=DailyGapLedger(self.ledger)
        base.seed_previous_head("KR_001","100",day="2026-10-18")
        base.close()
        obj=self.create(front={})
        for account in accounts():
            if account["ordinal"]==1:
                for cursor,ids,next_cursor in (
                    (None,["115","114","113","112"],"p2"),
                    ("p2",["111","110","109","108"],"p3"),
                    ("p3",["107","106","105","104"],"p4")
                ):
                    obj.ledger.record_page(
                        "KR_001",DAY,phase="front",
                        requested_cursor=cursor,post_ids=ids,next_cursor=next_cursor)
            else:
                obj.ledger.record_page(
                    account["key"],DAY,phase="front",
                    post_ids=[str(20000+account["ordinal"])])
        rep=obj.operator_report(now=NOW)
        self.assertEqual(rep["policy"]["next_phase_if_storage_verified"],"catchup")
        self.assertEqual(rep["gaps"]["catchup_pending_windows"],1)
        self.assertEqual(rep["gaps"]["manual_review_windows"],0)
        obj.ledger.db.execute(
            "UPDATE daily_window SET status='needs_review' WHERE account_key='KR_001'")
        blocked=obj.operator_report(now=NOW)
        self.assertEqual(blocked["policy"]["next_phase_if_storage_verified"],"manual_review")
        self.assertEqual(blocked["gaps"]["manual_review_windows"],1)

    def test_secret_like_lane_reason_sanitized_and_malformed_ready_at_unknown(self):
        obj=self.create()
        secret="Bearer TEST_PRIVATE_TOKEN_DO_NOT_LOG"
        obj.coordinator.db.execute(
            "UPDATE lanes SET halted=1,reason=?,ready_at=? WHERE lane='A'",
            (secret,"definitely-not-a-time"))
        before=self.dump(obj)
        result=obj.operator_report(now=NOW)
        self.assertEqual(before,self.dump(obj))
        self.assertNotIn(secret,str(result))
        self.assertEqual(result["lanes"][0]["reason"],"unknown")
        self.assertIsNone(result["lanes"][0]["ready_at_utc"])
        self.assertFalse(result["lanes"][0]["reset_time_reached"])
        self.assertTrue(result["lanes"][0]["requires_manual_reset"])

    def test_snapshot_binding_tamper_cannot_be_reported_as_healthy(self):
        obj=self.create()
        obj.coordinator.db.execute(
            "UPDATE daily_snapshot_binding SET roster_digest='changed'")
        with self.assertRaises(Exception):
            obj.operator_report(now=NOW)

    def test_naive_time_or_invalid_type_refused(self):
        obj=self.create()
        for v in (None,"2026-10-19",datetime(2026,10,19)):
            with self.subTest(v=v),self.assertRaises(OfflineDailyScheduleError):
                obj.operator_report(now=v)


if __name__=="__main__":
    unittest.main()
