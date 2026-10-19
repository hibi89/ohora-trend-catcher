"""Offline end-to-end checks for P6 A/B/C ports.

Uses a synthetic transport and isolated in-memory RAW schema. It never opens
the operational database or creates a network sender.
"""
from dataclasses import replace
from datetime import date, datetime, timezone
from pathlib import Path
import sqlite3
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "raw_v2"))
sys.path.insert(0, str(ROOT / "tests"))

from test_three_api_raw_writer import SCHEMA
from three_api_capture import CaptureJournal
from three_api_contract_executor import ContractExecutionError, ThreeApiContractExecutor
from three_api_raw_writer import ContractRawWriter
from three_api_request_spec import build_request_spec
from three_api_runtime_contract import (
    CONTRACT_VERSION, RequestEnvelope, ResponseEnvelope, TransportFailure,
    digest_sha256,
)


TARGET_ID = "17840000000000000"
USERNAME = "nails_1"
OBSERVED_AT = datetime(2026, 10, 19, 12, tzinfo=timezone.utc)


def make_request():
    spec = build_request_spec("A", USERNAME)
    return RequestEnvelope(
        contract_version=CONTRACT_VERSION,
        request_id="req-" + "a" * 64,
        run_id="offline-run-1",
        roster_digest="b" * 64,
        day_kst=date(2026, 10, 19),
        account_ordinal=1,
        account_key="KR_001",
        username=USERNAME,
        lane="A",
        phase="front",
        page=1,
        cursor=None,
        requested_at=OBSERVED_AT,
        request_spec=spec,
        api_version="v26.0",
        target_ig_user_id=TARGET_ID,
        request_spec_digest=digest_sha256({
            "api_version": "v26.0", "target_ig_user_id": TARGET_ID,
            "spec": spec,
        }),
    )


def make_response(request, *, usage=None, account_id=TARGET_ID):
    return ResponseEnvelope(
        CONTRACT_VERSION,
        request.request_id,
        200,
        {"business_discovery": {
            "id": account_id,
            "username": USERNAME,
            "media": {"data": [{
                "id": "1234567890",
                "username": USERNAME,
                "caption": "synthetic nail art",
                "media_type": "IMAGE",
                "like_count": 12,
                "comments_count": 2,
                "timestamp": "2026-10-19T10:00:00+0000",
                "permalink": "https://www.instagram.com/p/SYNTHETIC/",
            }]},
        }},
        {"x-app-usage": '{"call_count":20}'} if usage is None else usage,
        OBSERVED_AT,
        None,
    )


class FakeTransport:
    def __init__(self, response=None, error=None):
        self.response = response
        self.error = error
        self.calls = 0

    def send(self, request):
        self.calls += 1
        if self.error is not None:
            raise self.error
        return self.response


class ContractExecutorTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.journal = CaptureJournal(str(Path(self.temp.name) / "capture.sqlite"))
        self.raw_db = sqlite3.connect(":memory:", isolation_level=None)
        self.raw_db.executescript(SCHEMA)
        self.raw_db.execute("INSERT INTO accounts VALUES(?)", ("KR_001",))
        self.request = make_request()

    def tearDown(self):
        self.journal.close()
        self.raw_db.close()
        self.temp.cleanup()

    def executor(self, transport):
        return ThreeApiContractExecutor(
            transport, self.journal, ContractRawWriter(self.raw_db),
            clock=lambda: OBSERVED_AT,
        )

    def count(self, table):
        if not self.raw_db.execute(
                "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (table,)
        ).fetchone():
            return 0
        return self.raw_db.execute("SELECT COUNT(*) FROM " + table).fetchone()[0]

    def test_capture_journal_refuses_operational_database_filename(self):
        with self.assertRaisesRegex(ValueError, "Operating RAW database"):
            CaptureJournal(str(Path(self.temp.name) / "collection.sqlite"))

    def test_success_persists_capture_then_raw_and_acknowledges(self):
        transport = FakeTransport(make_response(self.request))
        receipt = self.executor(transport).execute(self.request)

        self.assertEqual(transport.calls, 1)
        self.assertEqual(receipt.status, "committed")
        self.assertEqual(receipt.capture_id, "cap-" + self.request.request_id)
        self.assertEqual(self.count("post_observations"), 1)
        self.assertEqual(self.count("three_api_contract_receipts"), 1)
        self.assertEqual(self.journal.get_intent_outcome(self.request.request_id), "captured")
        self.assertEqual(
            self.journal._db.execute(
                "SELECT processed_receipt FROM captures WHERE capture_id=?",
                (receipt.capture_id,),
            ).fetchone()[0],
            receipt.raw_receipt_id,
        )

    def test_multiple_account_requests_share_one_raw_run(self):
        transport = FakeTransport(make_response(self.request))
        executor = self.executor(transport)
        executor.execute(self.request)

        next_request = replace(self.request, request_id="req-" + "c" * 64)
        next_transport = FakeTransport(make_response(next_request))
        executor.transport = next_transport
        second_receipt = executor.execute(next_request)

        self.assertEqual(second_receipt.status, "committed")
        self.assertEqual(transport.calls + next_transport.calls, 2)
        self.assertEqual(self.count("runs"), 1)
        self.assertEqual(
            self.raw_db.execute("SELECT status,finished_at FROM runs").fetchone(),
            ("started", None),
        )
        self.assertEqual(self.count("post_observations"), 2)
        self.assertEqual(self.count("three_api_contract_receipts"), 2)

    def test_unknown_outcome_is_not_retried(self):
        failure = TransportFailure(
            CONTRACT_VERSION, self.request.request_id, "outcome_unknown",
            "account", False, None, "Synthetic ambiguous result", True,
        )
        transport = FakeTransport(error=failure)
        executor = self.executor(transport)

        with self.assertRaises(ContractExecutionError):
            executor.execute(self.request)
        with self.assertRaises(ContractExecutionError):
            executor.execute(self.request)

        self.assertEqual(transport.calls, 1)
        self.assertEqual(self.journal.get_intent_outcome(self.request.request_id), "unknown")
        self.assertEqual(self.count("three_api_raw_receipts"), 0)

    def test_recovery_after_raw_commit_before_ack_never_resends_http(self):
        transport = FakeTransport(make_response(self.request))
        executor = self.executor(transport)
        original = self.journal.confirm_processed
        interrupted = {"value": False}

        def fail_once(capture_id, receipt_id):
            if not interrupted["value"]:
                interrupted["value"] = True
                raise OSError("synthetic ack interruption")
            return original(capture_id, receipt_id)

        self.journal.confirm_processed = fail_once
        with self.assertRaises(OSError):
            executor.execute(self.request)
        self.assertEqual(self.count("three_api_contract_receipts"), 1)

        self.journal.confirm_processed = original
        result = executor.recover(self.request.run_id)

        self.assertEqual(result["raw_receipts_recovered"], 1)
        self.assertEqual(transport.calls, 1)
        self.assertEqual(self.count("post_observations"), 1)

    def test_new_executor_after_restart_recovers_without_http(self):
        first_transport = FakeTransport(make_response(self.request))
        first = self.executor(first_transport)
        original = self.journal.confirm_processed

        def interrupted_ack(capture_id, receipt_id):
            raise OSError("synthetic process interruption")

        self.journal.confirm_processed = interrupted_ack
        with self.assertRaises(OSError):
            first.execute(self.request)
        self.journal.confirm_processed = original
        self.journal.close()

        self.journal = CaptureJournal(str(Path(self.temp.name) / "capture.sqlite"))
        second_transport = FakeTransport(error=AssertionError("HTTP must not run"))
        restarted = self.executor(second_transport)
        recovered = restarted.execute(self.request)

        self.assertIn(recovered.status, ("committed", "already_committed"))
        self.assertEqual(first_transport.calls, 1)
        self.assertEqual(second_transport.calls, 0)
        self.assertEqual(self.count("post_observations"), 1)
        self.assertEqual(self.count("three_api_contract_receipts"), 1)

    def test_new_executor_after_unknown_outcome_never_resends_http(self):
        failure = TransportFailure(
            CONTRACT_VERSION, self.request.request_id, "outcome_unknown",
            "account", False, None, "Synthetic ambiguous result", True,
        )
        first_transport = FakeTransport(error=failure)
        with self.assertRaises(ContractExecutionError):
            self.executor(first_transport).execute(self.request)
        self.journal.close()

        self.journal = CaptureJournal(str(Path(self.temp.name) / "capture.sqlite"))
        second_transport = FakeTransport(error=AssertionError("HTTP must not run"))
        with self.assertRaises(ContractExecutionError):
            self.executor(second_transport).execute(self.request)
        self.assertEqual(first_transport.calls, 1)
        self.assertEqual(second_transport.calls, 0)
        self.assertEqual(self.count("three_api_contract_receipts"), 0)

    def assert_usage_blocked(self, header):
        transport = FakeTransport(make_response(self.request, usage=header))
        with self.assertRaises(ContractExecutionError):
            self.executor(transport).execute(self.request)
        self.assertEqual(transport.calls, 1)
        self.assertEqual(self.journal.get_intent_outcome(self.request.request_id), "failed_no_retry")
        self.assertEqual(self.count("three_api_contract_receipts"), 0)
        self.assertEqual(self.count("post_observations"), 0)

    def test_missing_usage_blocks_raw_write(self):
        self.assert_usage_blocked({})

    def test_usage_at_threshold_blocks_raw_write(self):
        self.assert_usage_blocked({"x-app-usage": '{"call_count":85}'})

    def test_returned_account_identity_must_match_requested_target(self):
        transport = FakeTransport(make_response(self.request, account_id="17840000000000099"))
        with self.assertRaises(ContractExecutionError):
            self.executor(transport).execute(self.request)
        self.assertEqual(transport.calls, 1)
        self.assertEqual(self.count("three_api_contract_receipts"), 0)
        self.assertEqual(self.count("posts"), 0)


if __name__ == "__main__":
    unittest.main()
