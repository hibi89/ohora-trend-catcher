"""Synthetic 365/730 day cutoff proof, no Meta/production data/HTTP.

These tests MUST NEVER treat a minimal synthetic history cursor, a NULL
cursor, or the oldest timestamp alone as real-world coverage evidence.
"""
from datetime import timedelta
from pathlib import Path
import tempfile
import unittest

from test_three_api_epochs import NOW,P3EpochTests
from three_api_request_spec import build_request_spec
from three_api_raw_mirror import OfflineRawMirror
from three_api_history_offline_bridge import OfflineHistoryRawBridge, OfflineHistoryBridgeError
from three_api_history_cutoff_audit import (
    audit_synthetic_history_cutoff, OfflineHistoryCoverageError,
)


class SyntheticHistoryCutoffAuditTests(unittest.TestCase):
    def setUp(self):
        self.fixture=P3EpochTests(methodName="test_frozen_294_x_three_pages_two_rounds")
        self.fixture.setUp()
        self.history=self.fixture.open_history_fixture()
        self.temp=tempfile.TemporaryDirectory()
        self.mirror=OfflineRawMirror(
            Path(self.temp.name)/"three_api_raw_mirror.sqlite"
        )
        self.bridge=OfflineHistoryRawBridge(self.fixture.store,self.mirror)

    def tearDown(self):
        self.mirror.close()
        self.temp.cleanup()
        self.fixture.tearDown()

    @staticmethod
    def posts(numbers,days):
        return [{"id":str(number),"timestamp":
                (NOW-timedelta(days=day)).isoformat()}
                for number,day in zip(numbers,days)]

    def append(self, target, suffix, posts, *, next_cursor, terminal="continued"):
        checkpoint=self.fixture.store.history_checkpoint(target,"KR_001")
        page=self.fixture.store.schedule_one_history_page(
            target,"KR_001","A",at=NOW
        )
        request="req-cutoff-"+suffix
        self.fixture.store.bind_history(
            page["job_id"],request,
            build_request_spec("A","nail_001",after=checkpoint["cursor"]),
            at=NOW,
        )
        self.bridge.stage_capture(
            page["job_id"],request,observed_at=NOW,posts=posts,
            next_cursor=next_cursor,terminal_basis=terminal,
        )
        answer=self.bridge.reconcile_one(page["job_id"])
        self.assertEqual(answer["status"],"committed")
        return page

    def audit(self,target=None):
        return audit_synthetic_history_cutoff(
            self.fixture.store,self.mirror,target or self.history,"KR_001"
        )

    def test_history_cursor_by_itself_never_claims_coverage(self):
        before=tuple(db.total_changes for db in (
            self.fixture.db,self.mirror.db
        ))
        result=self.audit()
        self.assertEqual(result["pages_checked"],0)
        self.assertFalse(result["synthetic_cutoff_verified"])
        self.assertFalse(result["production_coverage_verified"])
        self.assertEqual(result["coverage_status"],"insufficient_evidence")
        self.assertEqual(tuple(db.total_changes for db in (
            self.fixture.db,self.mirror.db
        )),before)

    def test_365_crossing_needs_immutable_root_and_raw_receipts(self):
        first=self.append(self.history,"365-first",
            self.posts([5101,5102,5103,5104],[290,310,340,350]),
            next_cursor="after_365_first")
        midway=self.audit()
        self.assertEqual(midway["pages_checked"],1)
        self.assertEqual(midway["posts_checked"],4)
        self.assertEqual(midway["coverage_status"],"insufficient_depth")
        second=self.append(self.history,"365-second",
            self.posts([5105,5106],[360,366]),
            next_cursor=None,terminal="verified_terminal")
        changes=(self.fixture.db.total_changes,self.mirror.db.total_changes)
        result=self.audit()
        self.assertTrue(result["synthetic_cutoff_verified"])
        self.assertFalse(result["production_coverage_verified"])
        self.assertEqual(result["coverage_status"],"cutoff_reached")
        self.assertEqual(result["pages_checked"],2)
        self.assertEqual(result["posts_checked"],6)
        self.assertEqual(result["oldest_published_at"],
                         (NOW-timedelta(days=366)).isoformat().replace("+00:00","Z"))
        self.assertEqual((self.fixture.db.total_changes,self.mirror.db.total_changes),changes)
        self.assertEqual(self.audit()["coverage_status"],"cutoff_reached")
        self.assertEqual(self.fixture.db.execute(
            "SELECT status FROM coverage_ledger WHERE epoch_id=? AND account_key='KR_001'",
            (self.history,)
        ).fetchone()[0],"partial")
        self.assertEqual(self.fixture.db.execute(
            "SELECT COUNT(*) FROM page_evidence_v2 WHERE job_id IN (?,?)",
            (first["job_id"],second["job_id"])
        ).fetchone()[0],2)

    def test_verified_terminal_before_cutoff_has_separate_reason(self):
        self.append(self.history,"short-terminal",
            self.posts([5201,5202],[100,200]),
            next_cursor=None,terminal="verified_terminal")
        report=self.audit()
        self.assertEqual(report["coverage_status"],"verified_terminal")
        self.assertTrue(report["synthetic_cutoff_verified"])
        self.assertFalse(report["production_coverage_verified"])

    def test_365_and_730_are_independent_target_epochs(self):
        target730=self.fixture.store.create_history_epoch(
            self.fixture.digest,anchor_utc=NOW,
            cutoff_utc=NOW-timedelta(days=730)
        )
        self.fixture.store.initialize_history_from_verified_daily(
            target730,self.fixture.daily,"KR_001",at=NOW
        )
        self.append(self.history,"365-other-target",
            self.posts([5301,5302,5303,5304],[300,320,350,366]),
            next_cursor="365_independent")
        first365=self.audit()
        self.assertTrue(first365["synthetic_cutoff_verified"])
        self.append(target730,"730-first",
            self.posts([5301,5302,5303,5304],[300,320,350,366]),
            next_cursor="730_next")
        first730=self.audit(target730)
        self.assertEqual(first730["coverage_status"],"insufficient_depth")
        self.assertFalse(first730["synthetic_cutoff_verified"])
        self.append(target730,"730-second",
            self.posts([5305,5306],[700,735]),
            next_cursor=None,terminal="verified_terminal")
        result=self.audit(target730)
        self.assertEqual(result["coverage_status"],"cutoff_reached")
        self.assertTrue(result["synthetic_cutoff_verified"])
        self.assertEqual(self.audit()["coverage_status"],"cutoff_reached")
        self.assertNotEqual(
            self.fixture.store.history_checkpoint(self.history,"KR_001")["cursor"],
            self.fixture.store.history_checkpoint(target730,"KR_001")["cursor"])

    def test_missing_timestamp_fails_closed_even_if_terminal(self):
        self.append(self.history,"missing-date",
            [{"id":"5401"}],next_cursor=None,terminal="verified_terminal")
        changes=(self.fixture.db.total_changes,self.mirror.db.total_changes)
        with self.assertRaises(OfflineHistoryCoverageError):
            self.audit()
        self.assertEqual((self.fixture.db.total_changes,self.mirror.db.total_changes),changes)

    def test_nonmonotonic_publication_order_fails_closed(self):
        self.append(self.history,"out-of-order",
            self.posts([5501,5502],[260,240]),
            next_cursor=None,terminal="verified_terminal")
        result=self.audit()
        self.assertFalse(result["synthetic_cutoff_verified"])
        self.assertEqual(result["coverage_status"],"insufficient_evidence")

    def test_raw_mirror_tampering_fails_closed_without_writes(self):
        self.append(self.history,"raw-corrupt",
            self.posts([5601,5602],[360,366]),
            next_cursor=None,terminal="verified_terminal")
        self.assertTrue(self.audit()["synthetic_cutoff_verified"])
        self.mirror.db.execute(
            "UPDATE raw_post_observations SET comments_present=1 WHERE post_id='5601'"
        )
        before=self.fixture.db.total_changes
        result=self.audit()
        self.assertFalse(result["synthetic_cutoff_verified"])
        self.assertEqual(result["coverage_status"],"insufficient_evidence")
        self.assertEqual(self.fixture.db.total_changes,before)

    def test_unfinished_next_history_page_prevents_completion_claim(self):
        self.append(self.history,"pending-first",
            self.posts([5701,5702,5703,5704],[360,366,370,380]),
            next_cursor="pending_next")
        self.assertTrue(self.audit()["synthetic_cutoff_verified"])
        self.fixture.store.schedule_one_history_page(
            self.history,"KR_001","B",at=NOW
        )
        result=self.audit()
        self.assertFalse(result["synthetic_cutoff_verified"])
        self.assertEqual(result["coverage_status"],"insufficient_evidence")


    def test_duplicate_raw_receipt_id_fails_closed(self):
        first=self.append(self.history,"receipt-first",
            self.posts([6101,6102,6103,6104],[300,320,340,350]),
            next_cursor="receipt_next")
        second=self.append(self.history,"receipt-second",
            self.posts([6105,6106],[360,366]),
            next_cursor=None,terminal="verified_terminal")
        self.assertTrue(self.audit()["synthetic_cutoff_verified"])
        receipt=self.fixture.db.execute(
            "SELECT raw_receipt_id FROM page_evidence_v2 WHERE job_id=?",
            (first["job_id"],)).fetchone()[0]
        self.fixture.db.execute(
            "UPDATE page_evidence_v2 SET raw_receipt_id=? WHERE job_id=?",
            (receipt,second["job_id"]))
        before=(self.fixture.db.total_changes,self.mirror.db.total_changes)
        report=self.audit()
        self.assertFalse(report["synthetic_cutoff_verified"])
        self.assertFalse(report["production_coverage_verified"])
        self.assertEqual((self.fixture.db.total_changes,self.mirror.db.total_changes),before)


    def test_cursor_self_loop_tampering_fails_closed(self):
        page=self.append(self.history,"cursor-tamper",
            self.posts([6201,6202],[360,366]),
            next_cursor=None,terminal="verified_terminal")
        self.assertTrue(self.audit()["synthetic_cutoff_verified"])
        self.fixture.db.execute(
            "UPDATE page_evidence_v2 SET cursor_out=cursor_in WHERE job_id=?",
            (page["job_id"],))
        result=self.audit()
        self.assertFalse(result["synthetic_cutoff_verified"])
        self.assertFalse(result["production_coverage_verified"])


    def test_last_receipt_checkpoint_tampering_fails_closed(self):
        self.append(self.history,"checkpoint-receipt",
            self.posts([6301,6302],[360,366]),
            next_cursor=None,terminal="verified_terminal")
        self.assertTrue(self.audit()["synthetic_cutoff_verified"])
        self.fixture.db.execute(
            "UPDATE cursor_checkpoints SET last_receipt='wrong-receipt' "
            "WHERE epoch_id=? AND account_key='KR_001' AND stream='history'",
            (self.history,))
        before=(self.fixture.db.total_changes,self.mirror.db.total_changes)
        report=self.audit()
        self.assertFalse(report["synthetic_cutoff_verified"])
        self.assertFalse(report["production_coverage_verified"])
        self.assertEqual((self.fixture.db.total_changes,self.mirror.db.total_changes),before)


    def test_checkpoint_observed_at_tampering_fails_closed(self):
        self.append(self.history,"checkpoint-time",
            self.posts([6401,6402],[360,366]),
            next_cursor=None,terminal="verified_terminal")
        self.assertTrue(self.audit()["synthetic_cutoff_verified"])
        self.fixture.db.execute(
            "UPDATE cursor_checkpoints SET updated_at='2020-01-01T00:00:00Z' "
            "WHERE epoch_id=? AND account_key='KR_001' AND stream='history'",
            (self.history,))
        report=self.audit()
        self.assertFalse(report["synthetic_cutoff_verified"])
        self.assertFalse(report["production_coverage_verified"])


    def test_checkpoint_last_media_id_tampering_fails_closed(self):
        self.append(self.history,"checkpoint-anchor",
            self.posts([6501,6502],[360,366]),
            next_cursor=None,terminal="verified_terminal")
        self.assertTrue(self.audit()["synthetic_cutoff_verified"])
        self.fixture.db.execute(
            "UPDATE cursor_checkpoints SET anchor_post_id='forged-media' "
            "WHERE epoch_id=? AND account_key='KR_001' AND stream='history'",
            (self.history,))
        report=self.audit()
        self.assertFalse(report["synthetic_cutoff_verified"])
        self.assertFalse(report["production_coverage_verified"])


    def test_reconcile_after_raw_promoted_before_checkpoint_is_idempotent(self):
        checkpoint=self.fixture.store.history_checkpoint(self.history,"KR_001")
        page=self.fixture.store.schedule_one_history_page(self.history,"KR_001","A",at=NOW)
        request="req-restart-after-raw"
        self.fixture.store.bind_history(
            page["job_id"],request,
            build_request_spec("A","nail_001",after=checkpoint["cursor"]),at=NOW)
        posts=self.posts([6701,6702],[360,366])
        self.bridge.stage_capture(page["job_id"],request,observed_at=NOW,
            posts=posts,next_cursor=None,terminal_basis="verified_terminal")
        receipt="history."+page["job_id"]
        self.mirror.stage_page(receipt,"KR_001",NOW.isoformat(),posts)
        self.mirror.promote_page(receipt)
        self.assertFalse(self.audit()["synthetic_cutoff_verified"])
        self.bridge.reconcile_one(page["job_id"])
        self.assertTrue(self.audit()["synthetic_cutoff_verified"])
        count_before=self.mirror.db.execute(
            "SELECT COUNT(*) FROM raw_post_observations WHERE request_id=?",
            (receipt,)).fetchone()[0]
        self.bridge.reconcile_one(page["job_id"])
        self.assertEqual(self.mirror.db.execute(
            "SELECT COUNT(*) FROM raw_post_observations WHERE request_id=?",
            (receipt,)).fetchone()[0],count_before)
        self.assertFalse(self.audit()["production_coverage_verified"])


    def test_reconcile_from_staged_only_after_restart(self):
        checkpoint=self.fixture.store.history_checkpoint(self.history,"KR_001")
        page=self.fixture.store.schedule_one_history_page(self.history,"KR_001","A",at=NOW)
        request="req-restart-staged-only"
        self.fixture.store.bind_history(
            page["job_id"],request,
            build_request_spec("A","nail_001",after=checkpoint["cursor"]),at=NOW)
        self.bridge.stage_capture(page["job_id"],request,observed_at=NOW,
            posts=self.posts([6801,6802],[360,366]),
            next_cursor=None,terminal_basis="verified_terminal")
        self.assertFalse(self.audit()["synthetic_cutoff_verified"])
        self.assertEqual(self.mirror.db.execute(
            "SELECT COUNT(*) FROM page_receipts WHERE request_id=?",
            ("history."+page["job_id"],)).fetchone()[0],0)
        self.bridge.reconcile_pending()
        self.assertTrue(self.audit()["synthetic_cutoff_verified"])
        self.assertEqual(self.mirror.db.execute(
            "SELECT COUNT(*) FROM page_receipts WHERE request_id=?",
            ("history."+page["job_id"],)).fetchone()[0],1)
        self.assertFalse(self.audit()["production_coverage_verified"])


    def test_tampered_staged_capture_blocks_replay_without_raw_write(self):
        checkpoint=self.fixture.store.history_checkpoint(self.history,"KR_001")
        page=self.fixture.store.schedule_one_history_page(self.history,"KR_001","A",at=NOW)
        request="req-restart-tampered"
        self.fixture.store.bind_history(
            page["job_id"],request,
            build_request_spec("A","nail_001",after=checkpoint["cursor"]),at=NOW)
        self.bridge.stage_capture(page["job_id"],request,observed_at=NOW,
            posts=self.posts([6901,6902],[360,366]),
            next_cursor=None,terminal_basis="verified_terminal")
        self.fixture.db.execute(
            "UPDATE offline_history_capture SET source_digest='tampered' WHERE job_id=?",
            (page["job_id"],))
        before=self.mirror.db.total_changes
        with self.assertRaises(OfflineHistoryBridgeError):
            self.bridge.reconcile_one(page["job_id"])
        self.assertEqual(self.mirror.db.total_changes,before)
        self.assertFalse(self.audit()["synthetic_cutoff_verified"])
        self.assertFalse(self.audit()["production_coverage_verified"])


    def test_reconcile_pending_second_pass_keeps_one_raw_receipt(self):
        checkpoint=self.fixture.store.history_checkpoint(self.history,"KR_001")
        page=self.fixture.store.schedule_one_history_page(self.history,"KR_001","A",at=NOW)
        request="req-restart-repeat-pending"
        self.fixture.store.bind_history(
            page["job_id"],request,
            build_request_spec("A","nail_001",after=checkpoint["cursor"]),at=NOW)
        self.bridge.stage_capture(page["job_id"],request,observed_at=NOW,
            posts=self.posts([7001,7002],[360,366]),
            next_cursor=None,terminal_basis="verified_terminal")
        self.bridge.reconcile_pending()
        self.assertTrue(self.audit()["synthetic_cutoff_verified"])
        first=self.mirror.db.execute(
            "SELECT COUNT(*) FROM raw_post_observations WHERE request_id=?",
            ("history."+page["job_id"],)).fetchone()[0]
        self.bridge.reconcile_pending()
        second=self.mirror.db.execute(
            "SELECT COUNT(*) FROM raw_post_observations WHERE request_id=?",
            ("history."+page["job_id"],)).fetchone()[0]
        self.assertEqual(first,2)
        self.assertEqual(second,first)
        self.assertFalse(self.audit()["production_coverage_verified"])


    def test_stage_capture_rejects_conflicting_replay_before_raw_write(self):
        checkpoint=self.fixture.store.history_checkpoint(self.history,"KR_001")
        page=self.fixture.store.schedule_one_history_page(self.history,"KR_001","A",at=NOW)
        request="req-stage-conflict"
        self.fixture.store.bind_history(
            page["job_id"],request,
            build_request_spec("A","nail_001",after=checkpoint["cursor"]),at=NOW)
        self.bridge.stage_capture(page["job_id"],request,observed_at=NOW,
            posts=self.posts([7101,7102],[360,366]),
            next_cursor=None,terminal_basis="verified_terminal")
        before=self.mirror.db.total_changes
        with self.assertRaises(OfflineHistoryBridgeError):
            self.bridge.stage_capture(page["job_id"],request,observed_at=NOW,
                posts=self.posts([7101,7103],[360,366]),
                next_cursor=None,terminal_basis="verified_terminal")
        self.assertEqual(self.mirror.db.total_changes,before)
        self.assertFalse(self.audit()["synthetic_cutoff_verified"])
        self.bridge.reconcile_one(page["job_id"])
        self.assertTrue(self.audit()["synthetic_cutoff_verified"])


if __name__=="__main__":
    unittest.main()
