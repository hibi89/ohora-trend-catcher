"""Offline preflight guard tests."""
import importlib.util
from pathlib import Path
from types import SimpleNamespace
import unittest

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
