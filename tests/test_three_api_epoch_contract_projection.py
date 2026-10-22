"""P4 P3 <-> real CaptureJournal / RAW v2 receipt projection, offline only.

All requests use an in-memory synthetic transport, a temp capture journal,
an isolated in-memory RAW v2 schema, and an in-memory P3 epoch database.
Never send Meta HTTP or open the operating collection database.
"""
from dataclasses import replace
from datetime import date, timedelta
from pathlib import Path
import json
import sqlite3
import sys
import tempfile
import unittest
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "raw_v2"))
sys.path.insert(0, str(ROOT / "tests"))

from test_three_api_contract_executor import (
    OBSERVED_AT, USERNAME, make_request, make_response, FakeTransport,
)
from test_three_api_raw_writer import SCHEMA
from test_three_api_epochs import official_shape
from three_api_capture import CaptureJournal
from three_api_contract_executor import (
    ThreeApiContractExecutor, capture_record_from_response,
)
from three_api_raw_writer import ContractRawWriter
from three_api_epochs import ThreeApiEpochStore
from three_api_epoch_contract_projection import (
    OfflineEpochContractProjector, OfflineEpochContractError,
)
from three_api_response_evidence import evidence_digest


class OfflineEpochContractProjectionTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.journal = CaptureJournal(str(Path(self.tmp.name) / "capture.sqlite"))
        self.rawdb = sqlite3.connect(":memory:", isolation_level=None)
        self.rawdb.executescript(SCHEMA)
        self.rawdb.execute("INSERT INTO accounts VALUES(?)", ("KR_001",))
        self.raw = ContractRawWriter(self.rawdb)
        self.p3db = sqlite3.connect(":memory:", isolation_level=None)
        self.p3db.row_factory = sqlite3.Row
        self.epochs = ThreeApiEpochStore(self.p3db)
        roster = official_shape()
        roster[0]["username"] = USERNAME
        self.digest = self.epochs.register_roster(roster, recorded_at=OBSERVED_AT)
        self.day = self.epochs.seed_daily(
            date(2026, 10, 19), self.digest, anchor_utc=OBSERVED_AT
        )
        self.job = self.p3db.execute(
            "SELECT job_id FROM jobs_v2 WHERE epoch_id=? "
            "AND account_ordinal=1 AND page_seq=1", (self.day,)
        ).fetchone()[0]
        self.request = replace(make_request(), roster_digest=self.digest)
        self.epochs.bind_daily(
            self.job, self.request.request_id, self.request.request_spec,
            at=OBSERVED_AT,
        )
        self.projector = OfflineEpochContractProjector(
            self.epochs, self.journal, self.raw
        )

    def tearDown(self):
        self.journal.close()
        self.rawdb.close()
        self.p3db.close()
        self.tmp.cleanup()

    def execute(self, request=None):
        request = self.request if request is None else request
        transport = FakeTransport(make_response(request))
        executor = ThreeApiContractExecutor(
            transport, self.journal, self.raw, clock=lambda: OBSERVED_AT,
        )
        return transport, executor.execute(request)

    def state(self):
        return self.p3db.execute(
            "SELECT state FROM jobs_v2 WHERE job_id=?", (self.job,)
        ).fetchone()[0]

    def restart_three_stores(self):
        """Snapshot the two in-memory databases, reopen all three on disk.

        GitHub actions and test runners stay offline. No transport object is
        retained, and this helper does not run any recovery automatically.
        """
        raw_path = Path(self.tmp.name) / "raw_v2_snapshot.sqlite"
        p3_path = Path(self.tmp.name) / "p3_epoch_snapshot.sqlite"
        raw_copy = sqlite3.connect(raw_path, isolation_level=None)
        p3_copy = sqlite3.connect(p3_path, isolation_level=None)
        try:
            self.rawdb.backup(raw_copy)
            self.p3db.backup(p3_copy)
        finally:
            raw_copy.close()
            p3_copy.close()
        self.journal.close()
        self.rawdb.close()
        self.p3db.close()
        self.journal = CaptureJournal(str(Path(self.tmp.name) / "capture.sqlite"))
        self.rawdb = sqlite3.connect(raw_path, isolation_level=None)
        self.raw = ContractRawWriter(self.rawdb)
        self.p3db = sqlite3.connect(p3_path, isolation_level=None)
        self.p3db.row_factory = sqlite3.Row
        self.epochs = ThreeApiEpochStore(self.p3db)
        self.projector = OfflineEpochContractProjector(
            self.epochs, self.journal, self.raw
        )

    def _save_synthetic_reel(self, *, views=42, include_views=True):
        """Exercise the real P1/P2 receipt writer, never the Meta network."""
        response = make_response(self.request)
        post = response.payload["business_discovery"]["media"]["data"][0]
        post["media_type"] = "VIDEO"
        post["media_product_type"] = "REELS"
        if include_views:
            post["view_count"] = views
        transport = FakeTransport(response)
        ThreeApiContractExecutor(
            transport, self.journal, self.raw, clock=lambda: OBSERVED_AT
        ).execute(self.request)
        self.assertEqual(transport.calls, 1)
        self.assertEqual(self.state(), "bound")

    def test_reel_view_count_zero_and_field_receipt_project_p3(self):
        self._save_synthetic_reel(views=0)
        fields = self.rawdb.execute(
            "SELECT status FROM field_observations "
            "WHERE request_id=? AND field_name='view_count'",
            (self.request.request_id,),
        ).fetchall()
        self.assertEqual(fields, [("returned",)])
        self.assertEqual(
            self.projector.project_front_page(self.job)["status"], "committed"
        )
        self.assertEqual(self.state(), "done")

    def test_reel_missing_views_remains_absent_not_zero(self):
        self._save_synthetic_reel(include_views=False)
        row = self.rawdb.execute(
            "SELECT s.full_values_json,s.field_states_json FROM post_checks c "
            "JOIN post_states s ON c.metrics_state_id=s.state_id "
            "WHERE c.request_id=?", (self.request.request_id,),
        ).fetchone()
        self.assertNotIn("view_count", json.loads(row[0]))
        self.assertEqual(json.loads(row[1])["view_count"], "absent")
        self.assertEqual(
            self.projector.project_front_page(self.job)["status"], "committed"
        )

    def test_reel_explicit_null_views_remains_null(self):
        self._save_synthetic_reel(views=None)
        row = self.rawdb.execute(
            "SELECT status FROM field_observations "
            "WHERE request_id=? AND field_name='view_count'",
            (self.request.request_id,),
        ).fetchone()
        self.assertEqual(row[0], "null")
        self.assertEqual(
            self.projector.project_front_page(self.job)["status"], "committed"
        )

    def test_reel_view_field_status_tampering_blocks_p3(self):
        self._save_synthetic_reel(views=123)
        self.rawdb.execute(
            "UPDATE field_observations SET status='absent' "
            "WHERE request_id=? AND field_name='view_count'",
            (self.request.request_id,),
        )
        previous = self.p3db.total_changes
        with self.assertRaisesRegex(
            OfflineEpochContractError, "RAW metric field observations incomplete"
        ):
            self.projector.project_front_page(self.job)
        self.assertEqual(self.state(), "bound")
        self.assertEqual(self.p3db.total_changes, previous)

    def test_reel_missing_field_observation_blocks_p3(self):
        self._save_synthetic_reel(views=77)
        self.rawdb.execute(
            "DELETE FROM field_observations "
            "WHERE request_id=? AND field_name='view_count'",
            (self.request.request_id,),
        )
        previous = self.p3db.total_changes
        with self.assertRaisesRegex(
            OfflineEpochContractError, "RAW metric field observations incomplete"
        ):
            self.projector.project_front_page(self.job)
        self.assertEqual(self.state(), "bound")
        self.assertEqual(self.p3db.total_changes, previous)

    def test_durable_capture_and_raw_receipt_advance_p3_only_once(self):
        transport, receipt = self.execute()
        self.assertEqual(transport.calls, 1)
        self.assertEqual(self.state(), "bound")
        self.assertEqual(
            self.projector.project_front_page(self.job)["status"], "committed"
        )
        self.assertEqual(self.state(), "done")
        self.assertEqual([
            row[0] for row in self.p3db.execute(
                "SELECT state FROM jobs_v2 WHERE epoch_id=? AND account_ordinal=1 "
                "ORDER BY page_seq", (self.day,)
            )
        ], ["done", "not_required", "not_required"])
        proof = self.p3db.execute(
            "SELECT source_evidence_id,raw_receipt_id,source_digest,"
            "ordered_ids_json FROM page_evidence_v2 WHERE job_id=?", (self.job,)
        ).fetchone()
        self.assertEqual(proof[0], "response-" + self.request.request_id)
        self.assertEqual(proof[1], receipt.raw_receipt_id)
        self.assertEqual(len(proof[2]), 64)
        self.assertEqual(proof[3], '["1234567890"]')
        changes = (
            self.p3db.total_changes, self.rawdb.total_changes,
            self.journal._db.total_changes
        )
        self.assertEqual(
            self.projector.project_front_page(self.job)["status"],
            "already_committed"
        )
        self.assertEqual(
            (self.p3db.total_changes, self.rawdb.total_changes,
             self.journal._db.total_changes), changes
        )
        self.assertEqual(transport.calls, 1)
        self.assertEqual(
            self.rawdb.execute("SELECT COUNT(*) FROM post_observations"
                               ).fetchone()[0], 1
        )

    def test_cursor_never_advances_before_raw_receipt(self):
        response = make_response(self.request)
        self.journal.save_intent(self.request)
        self.journal.save_response_evidence(response)
        record = capture_record_from_response(self.request, response)
        self.journal.save_capture(record)
        with self.assertRaises(OfflineEpochContractError):
            self.projector.project_front_page(self.job)
        self.assertEqual(self.state(), "bound")
        self.assertEqual(self.p3db.execute(
            "SELECT COUNT(*) FROM page_evidence_v2"
        ).fetchone()[0], 0)
        # Caller explicitly repairs stored capture -> RAW without any HTTP.
        from three_api_runtime_contract import commit_raw_and_confirm
        commit_raw_and_confirm(self.journal, self.raw, record)
        self.assertEqual(
            self.projector.project_front_page(self.job)["status"], "committed"
        )

    def test_unacknowledged_raw_receipt_cannot_advance_p3(self):
        transport = FakeTransport(make_response(self.request))
        executor = ThreeApiContractExecutor(
            transport, self.journal, self.raw, clock=lambda: OBSERVED_AT,
        )
        with mock.patch.object(
            self.journal, "confirm_processed",
            side_effect=OSError("offline injected ACK interruption")
        ):
            with self.assertRaises(OSError):
                executor.execute(self.request)
        self.assertEqual(transport.calls, 1)
        self.assertIsNotNone(self.raw.get_raw_receipt(
            "cap-" + self.request.request_id
        ))
        with self.assertRaises(OfflineEpochContractError):
            self.projector.project_front_page(self.job)
        self.assertEqual(self.state(), "bound")
        recovered = executor.recover(self.request.run_id)
        self.assertEqual(recovered["raw_receipts_recovered"], 1)
        self.assertEqual(transport.calls, 1)
        self.assertEqual(
            self.projector.project_front_page(self.job)["status"], "committed"
        )

    def test_cross_day_capture_cannot_be_misattributed(self):
        wrong = replace(
            self.request, day_kst=self.request.day_kst-timedelta(days=1)
        )
        self.execute(wrong)
        with self.assertRaises(OfflineEpochContractError):
            self.projector.project_front_page(self.job)
        self.assertEqual(self.state(), "bound")
        self.assertEqual(self.p3db.execute(
            "SELECT COUNT(*) FROM page_evidence_v2"
        ).fetchone()[0], 0)

    def test_safe_response_and_capture_must_share_original_media_content(self):
        """Even two independently well-formed digests cannot prove provenance."""
        self.execute()
        source = self.journal.get_response_evidence(self.request.request_id)
        safe_body = source["safe_body"]
        safe_body["business_discovery"]["media"]["data"][0]["caption"] = (
            "a different synthetic response after capture was committed"
        )
        self.journal._db.execute(
            "UPDATE response_evidence SET safe_body_json=?, body_digest=? "
            "WHERE request_id=?",
            (json.dumps(safe_body, ensure_ascii=False, sort_keys=True,
                        separators=(",", ":")).encode("utf-8"),
             evidence_digest(safe_body), self.request.request_id),
        )
        # The tampered retained safe body has a valid recomputed digest;
        # only cross-file source→CaptureRecord comparison detects it.
        self.assertEqual(
            self.journal.get_response_evidence(self.request.request_id)["body_digest"],
            evidence_digest(safe_body),
        )
        before = self.p3db.total_changes
        with self.assertRaisesRegex(
            OfflineEpochContractError, "response and CaptureRecord disagree"
        ):
            self.projector.project_front_page(self.job)
        self.assertEqual(self.p3db.total_changes, before)
        self.assertEqual(self.state(), "bound")

    def test_http_failure_evidence_cannot_be_relabelled_as_success(self):
        self.execute()
        self.journal._db.execute(
            "UPDATE response_evidence SET http_status=400 WHERE request_id=?",
            (self.request.request_id,),
        )
        before = self.p3db.total_changes
        with self.assertRaises(OfflineEpochContractError):
            self.projector.project_front_page(self.job)
        self.assertEqual(self.p3db.total_changes, before)
        self.assertEqual(self.state(), "bound")

    def test_corrupt_p2_versioned_content_cannot_advance_cursor(self):
        self.execute()
        self.rawdb.execute(
            "UPDATE post_states SET full_values_json='{}' WHERE state_id=("
            "SELECT content_state_id FROM post_checks WHERE request_id=?)",
            (self.request.request_id,),
        )
        before = self.p3db.total_changes
        with self.assertRaisesRegex(
            OfflineEpochContractError, "Versioned RAW post state differs"
        ):
            self.projector.project_front_page(self.job)
        self.assertEqual(self.p3db.total_changes, before)
        self.assertEqual(self.state(), "bound")

    def test_wrong_versioned_query_quality_blocks_p3(self):
        self.execute()
        self.rawdb.execute(
            "UPDATE query_observations SET quality_status='unverified' "
            "WHERE request_id=?", (self.request.request_id,),
        )
        with self.assertRaisesRegex(
            OfflineEpochContractError, "Versioned RAW query proof incomplete"
        ):
            self.projector.project_front_page(self.job)
        self.assertEqual(self.state(), "bound")

    def test_single_media_identity_corruption_blocks_p3(self):
        self.execute()
        self.rawdb.execute(
            "UPDATE post_media SET media_role='carousel_child' "
            "WHERE media_id='1234567890'"
        )
        with self.assertRaisesRegex(
            OfflineEpochContractError, "RAW media ownership or identity differs"
        ):
            self.projector.project_front_page(self.job)
        self.assertEqual(self.state(), "bound")

    def test_carousel_order_proof_and_media_identity(self):
        response = make_response(self.request)
        post = response.payload["business_discovery"]["media"]["data"][0]
        post["media_type"] = "CAROUSEL_ALBUM"
        post["children"] = {"data": [
            {"id": "88001", "media_type": "IMAGE",
             "media_url": "https://cdn.example.invalid/88001.jpg"},
            {"id": "88002", "media_type": "VIDEO",
             "media_url": "https://cdn.example.invalid/88002.mp4"},
        ]}
        executor = ThreeApiContractExecutor(
            FakeTransport(response), self.journal, self.raw,
            clock=lambda: OBSERVED_AT,
        )
        executor.execute(self.request)
        self.assertEqual(
            self.rawdb.execute(
                "SELECT COUNT(*) FROM media_structure_members"
            ).fetchone()[0], 2
        )
        original = self.rawdb.execute(
            "SELECT structure_state_id FROM post_checks WHERE request_id=?",
            (self.request.request_id,)
        ).fetchone()[0]
        self.rawdb.execute(
            "UPDATE media_structure_members SET media_id='88999' "
            "WHERE structure_state_id=? AND position=0", (original,),
        )
        before = self.p3db.total_changes
        with self.assertRaisesRegex(
            OfflineEpochContractError, "Per-observation carousel order differs"
        ):
            self.projector.project_front_page(self.job)
        self.assertEqual(self.p3db.total_changes, before)
        self.assertEqual(self.state(), "bound")

    def test_carousel_success_preserves_versioned_order_and_raw_identity(self):
        response = make_response(self.request)
        post = response.payload["business_discovery"]["media"]["data"][0]
        post["media_type"] = "CAROUSEL_ALBUM"
        post["children"] = {"data": [
            {"id": "88011", "media_type": "IMAGE"},
            {"id": "88012", "media_type": "VIDEO"},
        ]}
        executor = ThreeApiContractExecutor(
            FakeTransport(response), self.journal, self.raw,
            clock=lambda: OBSERVED_AT,
        )
        executor.execute(self.request)
        self.assertEqual(
            self.projector.project_front_page(self.job)["status"], "committed"
        )
        self.assertEqual(self.state(), "done")
        self.assertEqual([
            row[0] for row in self.rawdb.execute(
                "SELECT media_id FROM media_structure_members ORDER BY position"
            )
        ], ["88011", "88012"])

    def test_three_database_restore_commits_only_p3_without_http_replay(self):
        """Capture and RAW success survive a coordinated offline restart."""
        first_transport, receipt = self.execute()
        self.assertEqual(first_transport.calls, 1)
        self.assertEqual(self.state(), "bound")
        self.restart_three_stores()
        before = (self.rawdb.total_changes,self.journal._db.total_changes)
        self.assertEqual(
            self.projector.project_front_page(self.job)["status"], "committed"
        )
        self.assertEqual(self.state(), "done")
        self.assertEqual(self.rawdb.total_changes, before[0])
        self.assertEqual(self.journal._db.total_changes, before[1])
        self.assertEqual(
            self.projector.project_front_page(self.job)["status"],
            "already_committed",
        )
        self.assertEqual(self.p3db.execute(
            "SELECT raw_receipt_id FROM page_evidence_v2 WHERE job_id=?",
            (self.job,)
        ).fetchone()[0],receipt.raw_receipt_id)
        self.assertEqual(self.rawdb.execute(
            "PRAGMA integrity_check").fetchone()[0],"ok")
        self.assertEqual(self.rawdb.execute("PRAGMA foreign_key_check").fetchall(),[])
        self.assertEqual(self.p3db.execute("PRAGMA foreign_key_check").fetchall(),[])

    def test_three_database_restore_preserves_unconfirmed_raw_hold(self):
        """Crash after RAW but before Capture ACK requires manual local replay."""
        response=make_response(self.request)
        sender=FakeTransport(response)
        executor=ThreeApiContractExecutor(
            sender,self.journal,self.raw,clock=lambda:OBSERVED_AT
        )
        with mock.patch.object(
            self.journal, "confirm_processed",side_effect=OSError("injected")
        ):
            with self.assertRaises(OSError):
                executor.execute(self.request)
        self.assertEqual(sender.calls,1)
        self.assertEqual(self.state(),"bound")
        self.restart_three_stores()
        with self.assertRaises(OfflineEpochContractError):
            self.projector.project_front_page(self.job)
        self.assertEqual(self.state(),"bound")
        transport_after=FakeTransport(error=AssertionError("network forbidden"))
        recovered=ThreeApiContractExecutor(
            transport_after,self.journal,self.raw,clock=lambda:OBSERVED_AT
        ).recover(self.request.run_id)
        self.assertEqual(recovered["raw_receipts_recovered"],1)
        self.assertEqual(transport_after.calls,0)
        self.assertEqual(
            self.projector.project_front_page(self.job)["status"],"committed")
        self.assertEqual(self.state(),"done")
        self.assertEqual(self.rawdb.execute(
            "SELECT COUNT(*) FROM post_observations").fetchone()[0],1)

    def test_three_database_restore_missing_raw_never_auto_writes(self):
        """A retained source alone can never advance a recovered P3 cursor."""
        response=make_response(self.request)
        self.journal.save_intent(self.request)
        self.journal.save_response_evidence(response)
        record=capture_record_from_response(self.request,response)
        self.journal.save_capture(record)
        self.restart_three_stores()
        before=self.rawdb.total_changes
        with self.assertRaises(OfflineEpochContractError):
            self.projector.project_front_page(self.job)
        self.assertEqual(self.state(),"bound")
        self.assertEqual(self.rawdb.total_changes,before)
        recovered_record=self.journal.get_capture_for_request(
            self.request.request_id)
        from three_api_runtime_contract import commit_raw_and_confirm
        commit_raw_and_confirm(self.journal,self.raw,recovered_record)
        self.assertEqual(
            self.projector.project_front_page(self.job)["status"],"committed")
        self.assertEqual(self.state(),"done")

    def test_raw_observation_corruption_does_not_advance_p3(self):
        self.execute()
        self.rawdb.execute(
            "UPDATE post_observations SET like_count=999 "
            "WHERE request_id=?", (self.request.request_id,)
        )
        with self.assertRaises(OfflineEpochContractError):
            self.projector.project_front_page(self.job)
        self.assertEqual(self.state(), "bound")
        self.assertEqual(self.p3db.execute(
            "SELECT COUNT(*) FROM page_evidence_v2"
        ).fetchone()[0], 0)


if __name__ == "__main__":
    unittest.main()
