"""Offline durable job/request binding regressions: no real sockets or Meta."""
from dataclasses import replace
from datetime import timedelta
from pathlib import Path
import sqlite3
import sys
import tempfile
import unittest
from types import SimpleNamespace

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "raw_v2"))
sys.path.insert(0, str(ROOT / "tests"))

from three_api_coordinator import Coordinator
from three_api_evidence import PageEvidenceStore
from three_api_job_binding import JobBindingError, JobRequestBindingStore
from three_api_adapter_guard import execute_guarded, AdapterGuardError
from three_api_capture import CaptureJournal
from three_api_contract_executor import ThreeApiContractExecutor
from three_api_raw_writer import ContractRawWriter
from three_api_runtime_contract import CONTRACT_VERSION, TransportFailure
from three_api_contract_executor import ContractExecutionError
from test_three_api_contract_executor import (
    FakeTransport, OBSERVED_AT, SCHEMA, make_request, make_response,
)


class JobRequestBindingTests(unittest.TestCase):
    def setUp(self):
        self.folder = tempfile.TemporaryDirectory()
        self.addCleanup(self.folder.cleanup)
        self.root = Path(self.folder.name)
        self.coordinator = Coordinator(self.root / "coordinator.sqlite")
        self.coordinator.seed()
        self.evidence = PageEvidenceStore(self.coordinator)
        self.bindings = JobRequestBindingStore(self.coordinator)
        self.start = OBSERVED_AT
        self.request = make_request()
        self.job = self.coordinator.lease("A", now=self.start)
        self.assertEqual(self.job["account_ordinal"], 1)
        self.addCleanup(self.coordinator.db.close)

    def test_first_dispatch_only_even_after_expired_lease_and_new_id(self):
        calls = []
        executor = SimpleNamespace(
            execute=lambda request: calls.append(request.request_id) or "receipt")
        with self.assertRaises(AdapterGuardError):
            execute_guarded(self.job, self.request, self.evidence, executor)
        self.assertEqual(calls, [])
        result = execute_guarded(
            self.job, self.request, self.evidence, executor,
            bindings=self.bindings, now=self.start)
        self.assertEqual(result, "receipt")
        self.assertEqual(calls, [self.request.request_id])
        self.assertEqual(self.bindings.lookup(self.job["id"])["request_id"],
                         self.request.request_id)

        future = self.start + timedelta(minutes=16)
        reissued = self.coordinator.lease("A", now=future)
        self.assertEqual(reissued["id"], self.job["id"])
        self.assertNotEqual(reissued["lease_id"], self.job["lease_id"])
        with self.assertRaises(JobBindingError):
            self.bindings.dispatch_once(
                reissued, self.request, self.evidence, executor, now=future)
        different_request_id = replace(self.request, request_id="req-" + "d" * 64)
        with self.assertRaises(JobBindingError):
            self.bindings.dispatch_once(
                reissued, different_request_id, self.evidence, executor, now=future)
        self.assertEqual(calls, [self.request.request_id])

    def test_reservation_survives_restart_even_without_capture_or_intent(self):
        self.bindings.reserve(self.job, self.request, self.evidence, now=self.start)
        self.coordinator.db.close()
        self.coordinator = Coordinator(self.root / "coordinator.sqlite")
        self.addCleanup(self.coordinator.db.close)
        self.evidence = PageEvidenceStore(self.coordinator)
        self.bindings = JobRequestBindingStore(self.coordinator)
        future = self.start + timedelta(minutes=16)
        reissued = self.coordinator.lease("A", now=future)
        self.assertEqual(reissued["id"], self.job["id"])
        capture = CaptureJournal(str(self.root / "capture.sqlite"))
        try:
            with self.assertRaises(JobBindingError):
                self.bindings.recover_captured(
                    reissued, self.request, self.evidence, capture,
                    SimpleNamespace(), now=future)
            calls = []
            with self.assertRaises(JobBindingError):
                self.bindings.dispatch_once(
                    reissued, replace(self.request, request_id="req-" + "e" * 64),
                    self.evidence, SimpleNamespace(execute=lambda r: calls.append(r)),
                    now=future)
            self.assertEqual(calls, [])
            self.assertIsNone(capture.get_intent_outcome(self.request.request_id))
        finally:
            capture.close()

    def test_unknown_http_outcome_restart_blocks_new_request_id(self):
        journal = CaptureJournal(str(self.root / "capture.sqlite"))
        db = sqlite3.connect(":memory:", isolation_level=None)
        db.executescript(SCHEMA)
        db.execute("INSERT INTO accounts VALUES(?)", ("KR_001",))
        failure = TransportFailure(
            CONTRACT_VERSION, self.request.request_id, "outcome_unknown",
            "account", False, None, "Synthetic ambiguous HTTP result", True)
        first_transport = FakeTransport(error=failure)
        executor = ThreeApiContractExecutor(
            first_transport, journal, ContractRawWriter(db),
            clock=lambda: self.start)
        try:
            with self.assertRaises(ContractExecutionError):
                execute_guarded(
                    self.job, self.request, self.evidence, executor,
                    bindings=self.bindings, now=self.start)
            self.assertEqual(first_transport.calls, 1)
            self.assertEqual(journal.get_intent_outcome(self.request.request_id),
                             "unknown")
        finally:
            journal.close()
            db.close()

        self.coordinator.db.close()
        self.coordinator = Coordinator(self.root / "coordinator.sqlite")
        self.addCleanup(self.coordinator.db.close)
        self.evidence = PageEvidenceStore(self.coordinator)
        self.bindings = JobRequestBindingStore(self.coordinator)
        future = self.start + timedelta(minutes=16)
        reissued = self.coordinator.lease("A", now=future)
        self.assertEqual(reissued["id"], self.job["id"])
        calls = []
        new_request = replace(self.request, request_id="req-" + "f" * 64)
        with self.assertRaises(JobBindingError):
            execute_guarded(
                reissued, new_request, self.evidence,
                SimpleNamespace(execute=lambda r: calls.append(r)),
                bindings=self.bindings, now=future)
        self.assertEqual(calls, [])
        self.assertEqual(self.bindings.lookup(reissued["id"])["request_id"],
                         self.request.request_id)

    def test_job_bound_capture_raw_and_page_completion_end_to_end(self):
        journal = CaptureJournal(str(self.root / "capture.sqlite"))
        db = sqlite3.connect(":memory:", isolation_level=None)
        db.executescript(SCHEMA)
        db.execute("INSERT INTO accounts VALUES(?)", ("KR_001",))
        transport = FakeTransport(make_response(self.request))
        executor = ThreeApiContractExecutor(
            transport, journal, ContractRawWriter(db),
            clock=lambda: self.start)
        try:
            receipt = execute_guarded(
                self.job, self.request, self.evidence, executor,
                bindings=self.bindings, now=self.start)
            self.assertEqual(receipt.status, "committed")
            other_request = replace(self.request, request_id="req-" + "d" * 64)
            with self.assertRaises(JobBindingError):
                self.bindings.commit_captured_page(
                    self.job, other_request, self.evidence,
                    journal, ContractRawWriter(db), now=self.start)
            self.assertEqual(self.coordinator.db.execute(
                "SELECT COUNT(*) FROM page_evidence"
            ).fetchone()[0], 0)

            edge = self.bindings.commit_captured_page(
                self.job, self.request, self.evidence,
                journal, ContractRawWriter(db), now=self.start)
            self.assertTrue(edge["terminal"])
            self.assertEqual(transport.calls, 1)
            self.assertEqual(self.coordinator.db.execute(
                "SELECT state FROM jobs WHERE id=?", (self.job["id"],)
            ).fetchone()[0], "done")
            saved = self.coordinator.db.execute(
                "SELECT receipt FROM page_evidence WHERE job_id=?",
                (self.job["id"],)).fetchone()
            self.assertEqual(saved[0], receipt.raw_receipt_id)
            self.assertEqual(db.execute(
                "SELECT COUNT(*) FROM post_observations"
            ).fetchone()[0], 1)
            with self.assertRaises(JobBindingError):
                self.bindings.commit_captured_page(
                    self.job, self.request, self.evidence,
                    journal, ContractRawWriter(db), now=self.start)
            self.assertEqual(self.coordinator.db.execute(
                "SELECT COUNT(*) FROM page_evidence"
            ).fetchone()[0], 1)
        finally:
            journal.close()
            db.close()

    def test_verified_capture_cursor_unlocks_second_page(self):
        journal = CaptureJournal(str(self.root / "capture.sqlite"))
        db = sqlite3.connect(":memory:", isolation_level=None)
        db.executescript(SCHEMA)
        db.execute("INSERT INTO accounts VALUES(?)", ("KR_001",))
        base = make_response(self.request)
        payload = dict(base.payload)
        business = dict(payload["business_discovery"])
        media = dict(business["media"])
        media["paging"] = {
            "next": "https://graph.facebook.com/next",
            "cursors": {"after": "cursor2"},
        }
        business["media"] = media
        payload["business_discovery"] = business
        response = replace(base, payload=payload, next_cursor="cursor2")
        transport = FakeTransport(response)
        executor = ThreeApiContractExecutor(
            transport, journal, ContractRawWriter(db),
            clock=lambda: self.start)
        try:
            execute_guarded(
                self.job, self.request, self.evidence, executor,
                bindings=self.bindings, now=self.start)
            result = self.bindings.commit_captured_page(
                self.job, self.request, self.evidence, journal,
                ContractRawWriter(db), now=self.start)
            self.assertFalse(result["terminal"])
            self.assertEqual(result["next_cursor"], "cursor2")
            self.assertEqual(self.evidence.required_cursor(1, 2), "cursor2")
            second = self.coordinator.lease("C", now=self.start)
            self.assertIsNotNone(second)
            self.assertEqual(
                (second["account_ordinal"], second["page"], second["lane"]),
                (1, 2, "C"))
            self.assertEqual(transport.calls, 1)
        finally:
            journal.close()
            db.close()

    def test_dispatch_ready_counts_only_unbound_unblocked_first_pages(self):
        at_start = self.coordinator.dispatch_ready_diagnostics(now=self.start)
        lanes = {row["lane"]: row for row in at_start["lanes"]}
        self.assertEqual(lanes["A"]["eligible_jobs"], 149)
        self.assertEqual(lanes["B"]["eligible_jobs"], 144)
        self.assertEqual(lanes["C"]["eligible_jobs"], 0)
        self.assertEqual(lanes["A"]["dispatchable_now"], 149)
        self.bindings.reserve(self.job, self.request, self.evidence, now=self.start)
        later = self.start + timedelta(minutes=16)
        after = self.coordinator.dispatch_ready_diagnostics(now=later)
        lanes = {row["lane"]: row for row in after["lanes"]}
        self.assertEqual(lanes["A"]["eligible_jobs"], 149)
        self.assertEqual(lanes["A"]["dispatchable_now"], 149)
        self.assertEqual(lanes["C"]["eligible_jobs"], 0)
        self.assertEqual(self.coordinator.db.execute(
            "SELECT state FROM jobs WHERE id=?", (self.job["id"],)
        ).fetchone()[0], "leased")

    def test_dispatch_ready_counts_respect_lane_halt_without_mutation(self):
        self.coordinator.observe_usage("A", 85, now=self.start)
        report = self.coordinator.dispatch_ready_diagnostics(now=self.start)
        lanes = {row["lane"]: row for row in report["lanes"]}
        self.assertFalse(lanes["A"]["lane_ready"])
        self.assertEqual(lanes["A"]["eligible_jobs"], 149)
        self.assertEqual(lanes["A"]["dispatchable_now"], 0)
        self.assertEqual(lanes["B"]["dispatchable_now"], 144)
        self.assertEqual(lanes["C"]["dispatchable_now"], 0)

    def test_recovery_alias_never_consumes_fresh_work(self):
        later = self.start + timedelta(minutes=16)
        self.assertIsNone(self.coordinator.lease_for_recovery("A", now=later))
        fresh = self.coordinator.lease_for_dispatch("A", now=later)
        self.assertEqual(fresh["account_ordinal"], 1)
        self.assertIsNone(self.coordinator.lease_for_recovery("A", now=later))
        self.bindings.reserve(fresh, self.request, self.evidence, now=later)
        renewed = self.coordinator.lease_for_recovery(
            "A", now=later + timedelta(minutes=16))
        self.assertEqual(renewed["id"], fresh["id"])

    def test_bound_only_recovery_does_not_lease_unbound_jobs(self):
        later = self.start + timedelta(minutes=16)
        self.assertIsNone(self.coordinator.lease_bound_for_recovery("A", now=later))
        self.bindings.reserve(self.job, self.request, self.evidence, now=self.start)
        recovered = self.coordinator.lease_bound_for_recovery("A", now=later)
        self.assertEqual(recovered["id"], self.job["id"])
        self.assertIsNone(self.coordinator.lease_bound_for_recovery("A", now=later))
        fresh = self.coordinator.lease_for_dispatch("A", now=later)
        self.assertEqual(fresh["account_ordinal"], 2)
        with self.assertRaises(ValueError):
            self.coordinator.lease("A", now=later, skip_bound=True, only_bound=True)

    def test_explicit_lease_apis_separate_dispatch_from_recovery(self):
        self.bindings.reserve(self.job, self.request, self.evidence, now=self.start)
        later = self.start + timedelta(minutes=16)
        fresh = self.coordinator.lease_for_dispatch("A", now=later)
        self.assertEqual(fresh["account_ordinal"], 2)
        self.assertIsNone(self.bindings.lookup(fresh["id"]))
        recovery = self.coordinator.lease_for_recovery("A", now=later)
        self.assertEqual(recovery["id"], self.job["id"])
        self.assertEqual(self.bindings.lookup(recovery["id"])["request_id"],
                         self.request.request_id)
        with self.assertRaises(JobBindingError):
            self.bindings.dispatch_once(
                recovery, self.request, self.evidence,
                SimpleNamespace(execute=lambda r: self.fail("replayed HTTP")),
                now=later)

    def test_mixed_recovery_and_fresh_dispatch_do_not_collide(self):
        # One request was reserved, then its lease expired. A different
        # unbound job can dispatch while the first remains recover-only.
        self.bindings.reserve(self.job, self.request, self.evidence, now=self.start)
        later = self.start + timedelta(minutes=16)
        fresh = self.coordinator.lease("A", now=later, skip_bound=True)
        self.assertEqual(fresh["account_ordinal"], 2)
        recovery = self.coordinator.lease("A", now=later)
        self.assertEqual(recovery["id"], self.job["id"])
        self.assertNotEqual(recovery["id"], fresh["id"])
        snapshot = self.coordinator.dispatch_ready_diagnostics(now=later)
        a = next(row for row in snapshot["lanes"] if row["lane"] == "A")
        self.assertEqual(a["eligible_jobs"], 148)
        self.assertEqual(a["dispatchable_now"], 148)
        self.assertEqual(self.bindings.lookup(recovery["id"])["request_id"],
                         self.request.request_id)
        self.assertIsNone(self.bindings.lookup(fresh["id"]))
        self.assertEqual(self.coordinator.db.execute(
            "SELECT COUNT(*) FROM jobs WHERE state='leased'"
        ).fetchone()[0], 2)

    def test_bound_work_cannot_starve_dispatch_across_repeated_expiry(self):
        self.bindings.reserve(self.job, self.request, self.evidence, now=self.start)
        for minutes in (16, 32, 48):
            now = self.start + timedelta(minutes=minutes)
            candidate = self.coordinator.lease_for_dispatch("A", now=now)
            # Unbound expired leases may be selected again; a lease is not
            # evidence of a transport attempt or a completed page.
            self.assertEqual(candidate["account_ordinal"], 2)
            self.assertNotEqual(candidate["id"], self.job["id"])
            # The bound first job remains blocked from any new dispatch.
            self.assertEqual(self.bindings.lookup(self.job["id"])["request_id"],
                             self.request.request_id)
        self.assertEqual(self.coordinator.db.execute(
            "SELECT COUNT(*) FROM job_request_bindings"
        ).fetchone()[0], 1)

    def test_read_only_dispatch_diagnostics_classifies_bound_and_expired(self):
        initial = self.coordinator.dispatch_diagnostics(now=self.start)
        a = next(row for row in initial["lanes"] if row["lane"] == "A")
        self.assertEqual(a["bound_recovery"], 0)
        self.assertEqual(a["unbound"], 294)
        self.assertEqual(a["active_leases"], 1)
        self.bindings.reserve(self.job, self.request, self.evidence, now=self.start)
        later = self.start + timedelta(minutes=16)
        snapshot = self.coordinator.dispatch_diagnostics(now=later)
        a = next(row for row in snapshot["lanes"] if row["lane"] == "A")
        self.assertEqual(a["bound_recovery"], 1)
        self.assertEqual(a["unbound"], 293)
        self.assertEqual(a["active_leases"], 0)
        self.assertEqual(a["expired_leases"], 1)
        self.assertEqual(self.coordinator.db.execute(
            "SELECT state FROM jobs WHERE id=?", (self.job["id"],)
        ).fetchone()[0], "leased")

    def test_diagnostics_persist_across_restart_without_releasing_job(self):
        self.bindings.reserve(self.job, self.request, self.evidence, now=self.start)
        self.coordinator.db.close()
        self.coordinator = Coordinator(self.root / "coordinator.sqlite")
        self.addCleanup(self.coordinator.db.close)
        snapshot = self.coordinator.dispatch_diagnostics(
            now=self.start + timedelta(minutes=16))
        a = next(row for row in snapshot["lanes"] if row["lane"] == "A")
        self.assertEqual(a["bound_recovery"], 1)
        self.assertEqual(a["expired_leases"], 1)

    def test_dispatch_lease_skips_expired_bound_job_without_starving_lane(self):
        self.bindings.reserve(self.job, self.request, self.evidence, now=self.start)
        later = self.start + timedelta(minutes=16)
        fresh = self.coordinator.lease("A", now=later, skip_bound=True)
        self.assertIsNotNone(fresh)
        self.assertEqual(fresh["account_ordinal"], 2)
        self.assertEqual(fresh["page"], 1)
        self.assertIsNone(self.bindings.lookup(fresh["id"]))
        recovery = self.coordinator.lease("A", now=later)
        self.assertIsNotNone(recovery)
        self.assertEqual(recovery["id"], self.job["id"])
        self.assertNotEqual(recovery["lease_id"], self.job["lease_id"])
        calls = []
        with self.assertRaises(JobBindingError):
            self.bindings.dispatch_once(
                recovery, replace(self.request, request_id="req-" + "d" * 64),
                self.evidence,
                SimpleNamespace(execute=lambda request: calls.append(request)),
                now=later)
        self.assertEqual(calls, [])

    def test_dispatch_skip_persists_across_coordinator_restart(self):
        self.bindings.reserve(self.job, self.request, self.evidence, now=self.start)
        self.coordinator.db.close()
        self.coordinator = Coordinator(self.root / "coordinator.sqlite")
        self.addCleanup(self.coordinator.db.close)
        self.evidence = PageEvidenceStore(self.coordinator)
        self.bindings = JobRequestBindingStore(self.coordinator)
        fresh = self.coordinator.lease(
            "A", now=self.start + timedelta(minutes=16), skip_bound=True)
        self.assertEqual(fresh["account_ordinal"], 2)
        self.assertEqual(self.bindings.lookup(self.job["id"])["request_id"],
                         self.request.request_id)

    def test_lease_must_be_live_before_reservation(self):
        with self.assertRaises(JobBindingError):
            self.bindings.reserve(
                self.job, self.request, self.evidence,
                now=self.start + timedelta(minutes=16))
        self.assertIsNone(self.bindings.lookup(self.job["id"]))

    def test_immutable_capture_recovery_after_restart_never_calls_transport(self):
        capture_path = self.root / "capture.sqlite"
        raw_path = self.root / "test_raw.sqlite"
        journal = CaptureJournal(str(capture_path))
        db = sqlite3.connect(raw_path, isolation_level=None)
        db.executescript(SCHEMA)
        db.execute("INSERT INTO accounts VALUES(?)", ("KR_001",))
        first_transport = FakeTransport(make_response(self.request))
        executor = ThreeApiContractExecutor(
            first_transport, journal, ContractRawWriter(db),
            clock=lambda: self.start)
        original = journal.confirm_processed

        def interrupted_ack(capture_id, receipt_id):
            raise OSError("offline simulated process interruption")

        journal.confirm_processed = interrupted_ack
        try:
            with self.assertRaises(OSError):
                self.bindings.dispatch_once(
                    self.job, self.request, self.evidence, executor, now=self.start)
        finally:
            journal.confirm_processed = original
            journal.close()
            db.close()

        self.coordinator.db.close()
        self.coordinator = Coordinator(self.root / "coordinator.sqlite")
        self.addCleanup(self.coordinator.db.close)
        self.evidence = PageEvidenceStore(self.coordinator)
        self.bindings = JobRequestBindingStore(self.coordinator)
        future = self.start + timedelta(minutes=16)
        reissued = self.coordinator.lease("A", now=future)
        journal = CaptureJournal(str(capture_path))
        db = sqlite3.connect(raw_path, isolation_level=None)
        try:
            receipt = self.bindings.recover_captured(
                reissued, self.request, self.evidence, journal,
                ContractRawWriter(db), now=future)
            self.assertEqual(receipt.status, "already_committed")
            self.assertEqual(first_transport.calls, 1)
            self.assertEqual(db.execute(
                "SELECT COUNT(*) FROM three_api_contract_receipts"
            ).fetchone()[0], 1)
            self.assertEqual(db.execute(
                "SELECT COUNT(*) FROM post_observations"
            ).fetchone()[0], 1)
            self.assertEqual(journal.get_intent_outcome(self.request.request_id),
                             "captured")
            page = self.bindings.commit_captured_page(
                reissued, self.request, self.evidence, journal,
                ContractRawWriter(db), now=future)
            self.assertTrue(page["terminal"])
            self.assertEqual(self.coordinator.db.execute(
                "SELECT state FROM jobs WHERE id=?", (reissued["id"],)
            ).fetchone()[0], "done")
            self.assertEqual(self.coordinator.db.execute(
                "SELECT COUNT(*) FROM page_evidence"
            ).fetchone()[0], 1)
            with self.assertRaises(JobBindingError):
                self.bindings.dispatch_once(
                    reissued, self.request, self.evidence,
                    SimpleNamespace(execute=lambda r: self.fail("HTTP replay")),
                    now=future)
        finally:
            journal.close()
            db.close()


if __name__ == "__main__":
    unittest.main()
