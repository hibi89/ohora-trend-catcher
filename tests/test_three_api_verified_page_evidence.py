"""Offline end-to-end checks for durable RAW-backed page evidence."""
from dataclasses import replace
from datetime import timedelta
from pathlib import Path
import sqlite3
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "raw_v2"))
sys.path.insert(0, str(ROOT / "tests"))

from three_api_adapter_guard import execute_guarded
from three_api_capture import CaptureJournal
from three_api_contract_executor import ThreeApiContractExecutor
from three_api_coordinator import Coordinator
from three_api_job_binding import JobRequestBindingStore
from three_api_raw_writer import ContractRawWriter
from three_api_verified_page_evidence import (
    VerifiedPageEvidenceError, VerifiedRawPageEvidenceStore,
)
from test_three_api_contract_executor import (
    FakeTransport, OBSERVED_AT, SCHEMA, make_request, make_response,
)


class VerifiedPageEvidenceTests(unittest.TestCase):
    def setUp(self):
        self.folder = tempfile.TemporaryDirectory()
        self.addCleanup(self.folder.cleanup)
        self.root = Path(self.folder.name)
        self.coordinator = Coordinator(self.root / "coordinator.sqlite")
        self.addCleanup(self.coordinator.db.close)
        self.coordinator.seed()
        self.capture = CaptureJournal(str(self.root / "captures.sqlite"))
        self.addCleanup(self.capture.close)
        self.db = sqlite3.connect(":memory:", isolation_level=None)
        self.addCleanup(self.db.close)
        self.db.executescript(SCHEMA)
        self.db.execute("INSERT INTO accounts VALUES(?)", ("KR_001",))
        self.raw = ContractRawWriter(self.db)
        self.evidence = VerifiedRawPageEvidenceStore(
            self.coordinator, self.capture, self.raw,
        )
        self.bindings = JobRequestBindingStore(self.coordinator)
        self.request = make_request()
        self.job = self.coordinator.lease_for_dispatch("A", now=OBSERVED_AT)
        self.assertIsNotNone(self.job)
        self.assertEqual(self.job["account_ordinal"], 1)

    def capture_one(self, response=None):
        transport = FakeTransport(response or make_response(self.request))
        executor = ThreeApiContractExecutor(
            transport, self.capture, self.raw, clock=lambda: OBSERVED_AT,
        )
        execute_guarded(
            self.job, self.request, self.evidence, executor,
            bindings=self.bindings, now=OBSERVED_AT,
        )
        return transport

    def test_real_sqlite_capture_raw_cursor_commit(self):
        transport = self.capture_one()
        result = self.bindings.commit_captured_page(
            self.job, self.request, self.evidence,
            self.capture, self.raw, now=OBSERVED_AT,
        )
        self.assertTrue(result["terminal"])
        self.assertEqual(transport.calls, 1)
        self.assertEqual(self.coordinator.db.execute(
            "SELECT state FROM jobs WHERE id=?", (self.job["id"],)
        ).fetchone()[0], "done")
        self.assertEqual(self.db.execute(
            "SELECT COUNT(*) FROM post_observations"
        ).fetchone()[0], 1)

    def test_cursor_chain_unlocks_next_lane_after_raw_commit(self):
        base = make_response(self.request)
        payload = dict(base.payload)
        business = dict(payload["business_discovery"])
        media = dict(business["media"])
        media["paging"] = {
            "next": "https://graph.facebook.com/synthetic_next",
            "cursors": {"after": "synthetic-page-2"},
        }
        business["media"] = media
        payload["business_discovery"] = business
        response = replace(base, payload=payload, next_cursor="synthetic-page-2")
        transport = self.capture_one(response)
        edge = self.bindings.commit_captured_page(
            self.job, self.request, self.evidence,
            self.capture, self.raw, now=OBSERVED_AT,
        )
        self.assertFalse(edge["terminal"])
        self.assertEqual(self.evidence.required_cursor(1, 2), "synthetic-page-2")
        next_job = self.coordinator.lease_for_dispatch("C", now=OBSERVED_AT)
        self.assertEqual(next_job["page"], 2)
        self.assertEqual(transport.calls, 1)

    def test_missing_capture_does_not_complete_job(self):
        self.bindings.reserve(self.job, self.request, self.evidence,
                              now=OBSERVED_AT)
        with self.assertRaisesRegex(VerifiedPageEvidenceError, "Capture"):
            self.evidence.commit_page(
                self.job["id"], self.job["lease_id"], "rawv2:fake",
                {"data": []}, now=OBSERVED_AT,
            )
        self.assertEqual(self.coordinator.db.execute(
            "SELECT state FROM jobs WHERE id=?", (self.job["id"],)
        ).fetchone()[0], "leased")

    def test_wrong_receipt_or_forged_page_cannot_advance_cursor(self):
        self.capture_one()
        record = self.capture.get_capture_for_request(self.request.request_id)
        raw_id = self.raw.get_raw_receipt(record.capture_id).raw_receipt_id
        for receipt, payload in [
            ("rawv2:forged", {"data": list(record.normalized_posts)}),
            (raw_id, {"data": []}),
        ]:
            with self.subTest(receipt=receipt, payload=payload):
                with self.assertRaises(VerifiedPageEvidenceError):
                    self.evidence.commit_page(
                        self.job["id"], self.job["lease_id"],
                        receipt, payload, now=OBSERVED_AT,
                    )
        self.assertIsNone(self.coordinator.db.execute(
            "SELECT receipt FROM page_evidence WHERE job_id=?",
            (self.job["id"],),
        ).fetchone())

    def test_binding_digest_mismatch_does_not_commit_page(self):
        self.capture_one()
        record = self.capture.get_capture_for_request(self.request.request_id)
        self.coordinator.db.execute(
            "UPDATE job_request_bindings SET request_digest=? WHERE job_id=?",
            ("forged-digest", self.job["id"]),
        )
        with self.assertRaisesRegex(VerifiedPageEvidenceError, "binding"):
            self.evidence.commit_page(
                self.job["id"], self.job["lease_id"],
                self.raw.get_raw_receipt(record.capture_id).raw_receipt_id,
                {"data": list(record.normalized_posts)},
                now=OBSERVED_AT,
            )
        self.assertEqual(self.coordinator.db.execute(
            "SELECT COUNT(*) FROM page_evidence"
        ).fetchone()[0], 0)

    def test_restart_reconciles_without_refetch(self):
        transport = self.capture_one()
        self.coordinator.db.close()
        self.coordinator = Coordinator(self.root / "coordinator.sqlite")
        self.addCleanup(self.coordinator.db.close)
        self.evidence = VerifiedRawPageEvidenceStore(
            self.coordinator, self.capture, self.raw,
        )
        self.bindings = JobRequestBindingStore(self.coordinator)
        later = OBSERVED_AT + timedelta(minutes=16)
        recovered = self.coordinator.lease_for_recovery("A", now=later)
        self.assertEqual(recovered["id"], self.job["id"])
        edge = self.bindings.commit_captured_page(
            recovered, self.request, self.evidence,
            self.capture, self.raw, now=later,
        )
        self.assertTrue(edge["terminal"])
        self.assertEqual(transport.calls, 1)
        self.assertEqual(self.db.execute(
            "SELECT COUNT(*) FROM post_observations"
        ).fetchone()[0], 1)

    def test_staging_injection_and_fake_ports_rejected(self):
        with self.assertRaises(VerifiedPageEvidenceError):
            self.evidence.commit_staged_page(
                self.job["id"], self.job["lease_id"], {"data": []},
            )
        with self.assertRaises(VerifiedPageEvidenceError):
            self.evidence.commit_page(
                self.job["id"], self.job["lease_id"], "receipt",
                {"data": []}, staged_posts=[],
            )
        with self.assertRaises(VerifiedPageEvidenceError):
            VerifiedRawPageEvidenceStore(self.coordinator, object(), self.raw)


if __name__ == "__main__":
    unittest.main()
