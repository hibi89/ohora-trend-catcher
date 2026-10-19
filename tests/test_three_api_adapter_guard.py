"""Offline preflight guard tests."""
import importlib.util
from pathlib import Path
import sys
import tempfile
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "raw_v2"))
from three_api_coordinator import Coordinator
from three_api_evidence import PageEvidenceStore

source = Path(__file__).resolve().parents[1] / "raw_v2" / "three_api_adapter_guard.py"
spec = importlib.util.spec_from_file_location("adapter_guard", source)
guard = importlib.util.module_from_spec(spec)
spec.loader.exec_module(guard)


class GuardTests(unittest.TestCase):
    def test_identity_and_cursor(self):
        job = {"id": 1, "state": "leased", "lease_id": "x",
               "page": 2, "account_ordinal": 1, "lane": "C", "phase": "catchup"}
        request = SimpleNamespace(
            account_ordinal=1, page=2, lane="C", phase="catchup",
            cursor="cursor2", request_spec=SimpleNamespace(after="cursor2"))
        evidence = SimpleNamespace(required_cursor=lambda account, page: "cursor2")
        self.assertTrue(guard.validate_job_request(job, request, evidence))
        request.cursor = "different"
        with self.assertRaises(guard.AdapterGuardError):
            guard.validate_job_request(job, request, evidence)

    def test_rejects_malformed_request_without_leaking_error(self):
        job = {"id": 1, "state": "leased", "lease_id": "x",
               "page": 1, "account_ordinal": 1, "lane": "A", "phase": "front"}
        evidence = SimpleNamespace(required_cursor=lambda account, page: None)
        with self.assertRaises(guard.AdapterGuardError):
            guard.validate_job_request(job, SimpleNamespace(), evidence)

    def test_rejects_history_even_when_request_matches(self):
        job = {"id": 1, "state": "leased", "lease_id": "x",
               "page": 2, "account_ordinal": 1, "lane": "C", "phase": "history"}
        request = SimpleNamespace(account_ordinal=1, page=2, lane="C",
                                  phase="history", cursor="cursor2",
                                  request_spec=SimpleNamespace(after="cursor2"))
        evidence = SimpleNamespace(required_cursor=lambda account, page: "cursor2")
        with self.assertRaises(guard.AdapterGuardError):
            guard.validate_job_request(job, request, evidence)

    def test_guarded_bridge_calls_executor_once(self):
        job = {"id": 1, "state": "leased", "lease_id": "lease",
               "page": 1, "account_ordinal": 1, "lane": "A", "phase": "front"}
        request = SimpleNamespace(account_ordinal=1, page=1, lane="A",
                                  phase="front", cursor=None,
                                  request_spec=SimpleNamespace(after=None))
        evidence = SimpleNamespace(required_cursor=lambda account, page: None)
        calls = []
        executor = SimpleNamespace(execute=lambda value: calls.append(value) or "receipt")
        self.assertEqual(guard.execute_guarded(job, request, evidence, executor),
                         "receipt")
        self.assertEqual(calls, [request])
        request.lane = "B"
        with self.assertRaises(guard.AdapterGuardError):
            guard.execute_guarded(job, request, evidence, executor)
        self.assertEqual(calls, [request])

    def test_raw_completion_requires_matching_durable_receipt(self):
        request = SimpleNamespace(request_id="req-one")
        capture = SimpleNamespace(capture_id="cap-one", capture_digest="a" * 64,
                                  request=request)
        receipt = SimpleNamespace(capture_id="cap-one",
                                  capture_digest="a" * 64,
                                  raw_receipt_id="raw-one", status="committed")
        cap_port = SimpleNamespace(
            get_capture_for_request=lambda key: capture)
        raw_port = SimpleNamespace(
            get_raw_receipt=lambda key: receipt)
        self.assertEqual(guard.verify_raw_completion(
            request, receipt, cap_port, raw_port), "raw-one")
        missing = SimpleNamespace(get_raw_receipt=lambda key: None)
        with self.assertRaises(guard.AdapterGuardError):
            guard.verify_raw_completion(request, receipt, cap_port, missing)
        mismatched = SimpleNamespace(
            get_raw_receipt=lambda key: SimpleNamespace(
                capture_id="cap-one", capture_digest="a" * 64,
                raw_receipt_id="raw-other", status="committed"))
        with self.assertRaises(guard.AdapterGuardError):
            guard.verify_raw_completion(request, receipt, cap_port, mismatched)

    def test_page_commit_requires_verified_raw(self):
        job = {"id": 5, "state": "leased", "lease_id": "lease",
               "page": 1, "account_ordinal": 1, "lane": "A", "phase": "front"}
        request = SimpleNamespace(request_id="req-one", account_ordinal=1,
                                  page=1, lane="A", phase="front", cursor=None,
                                  request_spec=SimpleNamespace(after=None))
        capture = SimpleNamespace(capture_id="cap-one", capture_digest="a" * 64,
                                  request=request)
        receipt = SimpleNamespace(capture_id="cap-one",
                                  capture_digest="a" * 64,
                                  raw_receipt_id="raw-one", status="committed")
        cap_port = SimpleNamespace(get_capture_for_request=lambda key: capture)
        raw_port = SimpleNamespace(get_raw_receipt=lambda key: receipt)
        calls = []
        evidence = SimpleNamespace(
            required_cursor=lambda account, page: None,
            commit_page=lambda *args, **kwargs: calls.append((args, kwargs)) or "edge")
        self.assertEqual(guard.commit_verified_page(
            job, request, receipt, cap_port, raw_port, evidence,
            response={"data": []}), "edge")
        self.assertEqual(calls[0][0][:3], (5, "lease", "raw-one"))
        self.assertIsNone(calls[0][1]["requested_cursor"])
        calls.clear()
        missing = SimpleNamespace(get_raw_receipt=lambda key: None)
        with self.assertRaises(guard.AdapterGuardError):
            guard.commit_verified_page(
                job, request, receipt, cap_port, missing, evidence,
                response={"data": []})
        self.assertEqual(calls, [])

    def test_real_sqlite_page_completion_is_atomic_and_not_repeatable(self):
        with tempfile.TemporaryDirectory() as folder:
            coordinator = Coordinator(Path(folder) / "isolated.sqlite")
            try:
                coordinator.seed()
                evidence = PageEvidenceStore(coordinator)
                job = coordinator.lease("A")
                request = SimpleNamespace(
                    request_id="req-one", account_ordinal=1, page=1,
                    lane="A", phase="front", cursor=None,
                    request_spec=SimpleNamespace(after=None))
                capture = SimpleNamespace(
                    capture_id="cap-one", capture_digest="a" * 64,
                    request=request)
                receipt = SimpleNamespace(
                    capture_id="cap-one", capture_digest="a" * 64,
                    raw_receipt_id="raw-one", status="committed")
                capture_port = SimpleNamespace(
                    get_capture_for_request=lambda key: capture)
                raw_port = SimpleNamespace(
                    get_raw_receipt=lambda key: receipt)
                page = {"data": [{"id": "post-1"}],
                        "paging": {"next": "https://graph.facebook.com/next",
                                   "cursors": {"after": "edge1"}}}
                edge = guard.commit_verified_page(
                    job, request, receipt, capture_port, raw_port,
                    evidence, response=page)
                self.assertEqual(edge["next_cursor"], "edge1")
                self.assertEqual(evidence.required_cursor(1, 2), "edge1")
                self.assertEqual(coordinator.db.execute(
                    "SELECT state FROM jobs WHERE id=?", (job["id"],)
                ).fetchone()[0], "done")
                with self.assertRaises(ValueError):
                    guard.commit_verified_page(
                        job, request, receipt, capture_port, raw_port,
                        evidence, response=page)
                self.assertEqual(coordinator.db.execute(
                    "SELECT COUNT(*) FROM page_evidence"
                ).fetchone()[0], 1)
            finally:
                coordinator.db.close()

    def test_expired_lease_cannot_commit_even_with_valid_raw_receipt(self):
        with tempfile.TemporaryDirectory() as folder:
            coordinator = Coordinator(Path(folder) / "isolated.sqlite")
            try:
                coordinator.seed()
                evidence = PageEvidenceStore(coordinator)
                start = datetime(2026, 10, 19, tzinfo=timezone.utc)
                job = coordinator.lease("A", now=start)
                request = SimpleNamespace(
                    request_id="req-one", account_ordinal=1, page=1,
                    lane="A", phase="front", cursor=None,
                    request_spec=SimpleNamespace(after=None))
                capture = SimpleNamespace(
                    capture_id="cap-one", capture_digest="a" * 64,
                    request=request)
                receipt = SimpleNamespace(
                    capture_id="cap-one", capture_digest="a" * 64,
                    raw_receipt_id="raw-one", status="committed")
                capture_port = SimpleNamespace(
                    get_capture_for_request=lambda key: capture)
                raw_port = SimpleNamespace(
                    get_raw_receipt=lambda key: receipt)
                with self.assertRaises(ValueError):
                    guard.commit_verified_page(
                        job, request, receipt, capture_port, raw_port, evidence,
                        response={"data": [{"id": "post-1"}]},
                        now=start + timedelta(minutes=16))
                self.assertEqual(coordinator.db.execute(
                    "SELECT COUNT(*) FROM page_evidence"
                ).fetchone()[0], 0)
                self.assertEqual(coordinator.db.execute(
                    "SELECT state FROM jobs WHERE id=?", (job["id"],)
                ).fetchone()[0], "leased")
            finally:
                coordinator.db.close()

    def test_full_294_account_three_page_lane_plan(self):
        with tempfile.TemporaryDirectory() as folder:
            coordinator = Coordinator(Path(folder) / "isolated.sqlite")
            try:
                coordinator.seed()
                coordinator.seed()
                rows = coordinator.db.execute(
                    "SELECT account_ordinal,page,lane,phase FROM jobs "
                    "ORDER BY account_ordinal,page").fetchall()
                self.assertEqual(len(rows), 294 * 3)
                for account in range(1, 295):
                    chunk = rows[(account - 1) * 3:account * 3]
                    expected = ("A", "C", "B") if account <= 150 else ("B", "A", "C")
                    self.assertEqual([row["lane"] for row in chunk], list(expected))
                    self.assertEqual([row["page"] for row in chunk], [1, 2, 3])
                    self.assertEqual([row["phase"] for row in chunk],
                                     ["front", "catchup", "catchup"])
                self.assertEqual(coordinator.db.execute(
                    "SELECT COUNT(DISTINCT account_ordinal) FROM jobs"
                ).fetchone()[0], 294)
                self.assertEqual(coordinator.db.execute(
                    "SELECT COUNT(*) FROM jobs WHERE state='done'"
                ).fetchone()[0], 0)
            finally:
                coordinator.db.close()

    def test_all_882_pages_complete_with_real_sqlite_evidence(self):
        with tempfile.TemporaryDirectory() as folder:
            coordinator = Coordinator(Path(folder) / "isolated.sqlite")
            try:
                coordinator.seed()
                evidence = PageEvidenceStore(coordinator)
                total = 0
                # Round one completes all first pages before catch-up.
                # Every page carries its predecessor's durable cursor proof.
                for page in (1, 2, 3):
                    for lane in ("A", "B", "C"):
                        while True:
                            job = coordinator.lease(lane)
                            if job is None:
                                break
                            if job["page"] != page:
                                # A later-page job is not eligible until its
                                # predecessor; all earlier pages are complete.
                                self.fail("Unexpected page order")
                            ordinal = job["account_ordinal"]
                            previous = None if page == 1 else f"cursor-{ordinal}-{page-1}"
                            payload = {
                                "data": [{"id": f"post-{ordinal}-{page}"}],
                                "paging": {
                                    "next": "https://graph.facebook.com/next",
                                    "cursors": {"after": f"cursor-{ordinal}-{page}"},
                                },
                            }
                            evidence.commit_page(
                                job["id"], job["lease_id"],
                                f"raw-{ordinal}-{page}", payload,
                                requested_cursor=previous)
                            total += 1
                self.assertEqual(total, 882)
                self.assertEqual(coordinator.db.execute(
                    "SELECT COUNT(*) FROM jobs WHERE state='done'"
                ).fetchone()[0], 882)
                self.assertEqual(coordinator.db.execute(
                    "SELECT COUNT(*) FROM page_evidence"
                ).fetchone()[0], 882)
                self.assertEqual(coordinator.db.execute(
                    "SELECT COUNT(DISTINCT account_ordinal) FROM page_evidence"
                ).fetchone()[0], 294)
                self.assertTrue(all(coordinator.lease(lane) is None
                                    for lane in ("A", "B", "C")))
            finally:
                coordinator.db.close()

    def test_rejects_unleased_job(self):
        job = {"id": 1, "state": "pending", "lease_id": None,
               "page": 1, "account_ordinal": 1, "lane": "A", "phase": "front"}
        request = SimpleNamespace(
            account_ordinal=1, page=1, lane="A", phase="front",
            cursor=None, request_spec=SimpleNamespace(after=None))
        evidence = SimpleNamespace(required_cursor=lambda account, page: None)
        with self.assertRaises(guard.AdapterGuardError):
            guard.validate_job_request(job, request, evidence)


if __name__ == "__main__":
    unittest.main()
