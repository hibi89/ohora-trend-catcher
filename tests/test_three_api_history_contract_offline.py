"""Real-schema P1/P2 history receipt path with supplied OFFLINE fixtures.

The public request/executor must continue refusing any history HTTP dispatch.
All tests use an isolated P3 SQLite, temp CaptureJournal and in-memory RAW v2.
"""
from dataclasses import replace
from datetime import datetime
from pathlib import Path
import json
import sqlite3
import sys
import tempfile
import unittest
from unittest import mock

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/"raw_v2"))
sys.path.insert(0,str(ROOT/"tests"))

from test_three_api_epochs import P3EpochTests,NOW,DAY
from test_three_api_contract_executor import (
    TARGET_ID,make_response,FakeTransport,
)
from test_three_api_raw_writer import SCHEMA
from test_three_api_transport import references,transport
from three_api_request_spec import build_request_spec
from three_api_runtime_contract import (
    CONTRACT_VERSION,RequestEnvelope,digest_sha256,canonical_bytes,
    commit_raw_and_confirm,
)
from three_api_capture import CaptureJournal
from three_api_raw_writer import ContractRawWriter
from three_api_contract_executor import (
    ThreeApiContractExecutor,ContractExecutionError,
)
from three_api_transport import MetaTransport
from three_api_epoch_contract_projection import OfflineEpochContractProjector,OfflineEpochContractError
from three_api_history_fixture_ingress import OfflineHistoryFixtureIngress,OfflineHistoryFixtureError


class OfflineHistoryP1P2Tests(unittest.TestCase):
    def setUp(self):
        self.fx=P3EpochTests(methodName="test_frozen_294_x_three_pages_two_rounds")
        self.fx.setUp()
        self.history=self.fx.open_history_fixture()
        self.tmp=tempfile.TemporaryDirectory()
        self.capture=CaptureJournal(str(Path(self.tmp.name)/"offline_history_capture.sqlite"))
        self.raw_db=sqlite3.connect(":memory:",isolation_level=None)
        self.raw_db.executescript(SCHEMA)
        self.raw_db.execute("INSERT INTO accounts VALUES(?)",("KR_001",))
        self.raw=ContractRawWriter(self.raw_db)
        self.projector=OfflineEpochContractProjector(self.fx.store,self.capture,self.raw)
        self.ingress=OfflineHistoryFixtureIngress(self.fx.store,self.capture,self.raw)
        self.job=self.fx.store.schedule_one_history_page(
            self.history,"KR_001","A",at=NOW
        )
        self.request=self.make_request(self.job,"req-history-offline-1","offline_hist_cursor")
        self.response=self.make_response(self.request)

    def tearDown(self):
        self.capture.close()
        self.raw_db.close()
        self.tmp.cleanup()
        self.fx.tearDown()

    def make_request(self,job,request_id,irrelevant_tag):
        checkpoint=self.fx.store.history_checkpoint(self.history,"KR_001")
        spec=build_request_spec(
            job["lane"],"nail_001",after=checkpoint["cursor"],limit=4
        )
        self.fx.store.bind_history(job["job_id"],request_id,spec,at=NOW)
        return RequestEnvelope(
            contract_version=CONTRACT_VERSION,
            request_id=request_id,run_id="run-history-offline",
            roster_digest=self.fx.digest,day_kst=DAY,
            account_ordinal=1,account_key="KR_001",username="nail_001",
            lane=job["lane"],phase="history",page=job["page_seq"],
            cursor=checkpoint["cursor"],requested_at=NOW,
            request_spec=spec,
            request_spec_digest=digest_sha256({
                "api_version":"v26.0",
                "target_ig_user_id":TARGET_ID,"spec":spec,
            }),api_version="v26.0",target_ig_user_id=TARGET_ID,
            history_offline_only=True,
        )

    @staticmethod
    def make_response(request):
        response=make_response(request)
        payload=json.loads(json.dumps(response.payload))
        payload["business_discovery"]["username"]="nail_001"
        return replace(response,payload=payload,received_at=NOW)

    def test_real_schema_history_ingress_and_checkpoint_replay(self):
        receipt=self.ingress.save_supplied_response(
            self.job["job_id"],self.request,self.response
        )
        self.assertEqual(receipt["epoch_committed"],False)
        self.assertEqual(receipt["history_transport_enabled"],False)
        self.assertEqual(self.fx.store.history_checkpoint(
            self.history,"KR_001")["page_seq"],0)
        self.assertEqual(self.raw_db.execute(
            "SELECT COUNT(*) FROM three_api_contract_receipts"
        ).fetchone()[0],1)
        self.assertEqual(self.raw_db.execute(
            "SELECT COUNT(*) FROM query_observations"
        ).fetchone()[0],1)
        self.assertEqual(
            self.projector.project_history_page(self.job["job_id"])["status"],
            "committed"
        )
        self.assertEqual(
            self.projector.project_history_page(self.job["job_id"])["status"],
            "already_committed"
        )
        checkpoint=self.fx.store.history_checkpoint(self.history,"KR_001")
        self.assertEqual(checkpoint["status"],"terminal")
        self.assertEqual(checkpoint["page_seq"],1)
        self.assertEqual(checkpoint["last_receipt"],receipt["raw_receipt_id"])
        self.assertEqual(self.raw_db.execute(
            "SELECT COUNT(*) FROM post_observations"
        ).fetchone()[0],1)
        self.assertEqual(self.fx.db.execute(
            "SELECT COUNT(*) FROM coverage_ledger WHERE epoch_id=? "
            "AND status='verified'",(self.history,)
        ).fetchone()[0],0)
        self.assertEqual(self.fx.db.execute("PRAGMA foreign_key_check").fetchall(),[])

    def test_existing_front_canonical_bytes_are_unchanged(self):
        from test_three_api_contract_executor import make_request
        front=make_request()
        self.assertNotIn("history_offline_only",canonical_bytes(front).decode())
        self.assertIn('"history_offline_only":true',
                      canonical_bytes(self.request).decode())

    def test_live_meta_transport_and_executor_fail_closed_before_intent(self):
        calls=[]
        client=transport(sender=lambda *_a,**_k: calls.append(1))
        with self.assertRaises(Exception) as ctx:
            client.send(self.request)
        self.assertEqual(ctx.exception.category,"approval_denied")
        self.assertEqual(calls,[])
        generic=FakeTransport(self.response)
        executor=ThreeApiContractExecutor(
            generic,self.capture,self.raw,clock=lambda:NOW
        )
        with self.assertRaises(ContractExecutionError):
            executor.execute(self.request)
        self.assertEqual(generic.calls,0)
        self.assertIsNone(self.capture.get_intent_outcome(self.request.request_id))

    def test_unflagged_history_and_flagged_front_cannot_be_constructed(self):
        with self.assertRaises(ValueError):
            replace(self.request,history_offline_only=False)
        from test_three_api_contract_executor import make_request
        with self.assertRaises(ValueError):
            replace(make_request(),history_offline_only=True)

    def test_wrong_request_source_does_not_write_p1_or_p2(self):
        bad=replace(self.request,request_id="req-wrong-history")
        before=(self.capture._db.total_changes,self.raw_db.total_changes)
        with self.assertRaises(OfflineHistoryFixtureError):
            self.ingress.save_supplied_response(self.job["job_id"],bad,self.response)
        self.assertEqual((self.capture._db.total_changes,
                          self.raw_db.total_changes),before)
        self.assertEqual(self.fx.store.history_checkpoint(
            self.history,"KR_001")["page_seq"],0)

    def test_capture_before_raw_failure_recovers_from_saved_bytes_no_http(self):
        with mock.patch.object(
            self.raw,"write_capture",side_effect=OSError("offline crash")
        ):
            with self.assertRaises(OSError):
                self.ingress.save_supplied_response(
                    self.job["job_id"],self.request,self.response)
        self.assertIsNotNone(
            self.capture.get_capture_for_request(self.request.request_id)
        )
        self.assertEqual(self.fx.store.history_checkpoint(
            self.history,"KR_001")["page_seq"],0)
        with self.assertRaises(OfflineEpochContractError):
            self.projector.project_history_page(self.job["job_id"])
        record=self.capture.get_capture_for_request(self.request.request_id)
        commit_raw_and_confirm(self.capture,self.raw,record)
        self.assertEqual(self.projector.project_history_page(
            self.job["job_id"])["status"],"committed")
        self.assertEqual(self.raw_db.execute(
            "SELECT COUNT(*) FROM post_observations"
        ).fetchone()[0],1)

    def test_tampered_real_raw_observation_does_not_advance_history(self):
        self.ingress.save_supplied_response(
            self.job["job_id"],self.request,self.response
        )
        self.raw_db.execute(
            "UPDATE post_observations SET like_count=999 "
            "WHERE request_id=?",(self.request.request_id,)
        )
        with self.assertRaises(OfflineEpochContractError):
            self.projector.project_history_page(self.job["job_id"])
        self.assertEqual(self.fx.store.history_checkpoint(
            self.history,"KR_001")["page_seq"],0)

    def test_replay_history_fixture_with_altered_safe_body_is_rejected(self):
        self.ingress.save_supplied_response(
            self.job["job_id"],self.request,self.response)
        changed=json.loads(json.dumps(self.response.payload))
        changed["business_discovery"]["media"]["data"][0]["caption"]="changed"
        with self.assertRaises(OfflineHistoryFixtureError):
            self.ingress.save_supplied_response(
                self.job["job_id"],self.request,
                replace(self.response,payload=changed)
            )
        self.assertEqual(self.fx.store.history_checkpoint(
            self.history,"KR_001")["page_seq"],0)


if __name__=="__main__":
    unittest.main()
