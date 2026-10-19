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
