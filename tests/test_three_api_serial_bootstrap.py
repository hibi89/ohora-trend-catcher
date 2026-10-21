"""No-network integration of the complete 3-lane serial capture path.

The injected Graph sender is an in-memory fixture and never opens a socket.
Explicit approval in this test is synthetic only and grants no real Meta use.
"""
from datetime import date, datetime, timezone
import json
from pathlib import Path
import sqlite3
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "raw_v2"))
sys.path.insert(0, str(ROOT / "tests"))

from three_api_capture import CaptureJournal
from three_api_coordinator import Coordinator
from three_api_credentials import CredentialReference
from three_api_graph_http import GraphHTTPClient
from three_api_live_lane_router import ThreeLaneMetaTransport
from three_api_live_serial_step import LiveSerialStepError
from three_api_raw_writer import ContractRawWriter
from three_api_serial_bootstrap import build_verified_serial_runner, SerialBootstrapError
from three_api_transport import MetaTransport
from test_three_api_contract_executor import SCHEMA


def synthetic_roster():
    return [
        {"ordinal": n, "key": f"KR_{n:03}", "username": f"nail_{n}"}
        for n in range(1, 295)
    ]


class MockResponse:
    status = 200
    headers = {"x-app-usage": '{"call_count":20}'}

    def __init__(self):
        self.payload = {
            "business_discovery": {
                "id": "17840000000000099",
                "username": "nail_1",
                "media": {"data": [{
                    "id": "1234567890", "username": "nail_1",
                    "caption": "synthetic design",
                    "media_type": "IMAGE", "like_count": 3,
                    "comments_count": 1,
                    "timestamp": "2026-10-21T10:00:00+0000",
                    "permalink": "https://www.instagram.com/p/SYNTHETIC/",
                }]},
            },
        }

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return False

    def read(self, size):
        return json.dumps(self.payload).encode("utf-8")


class VerifiedSerialBootstrapTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.when = datetime(2026, 10, 21, 10, 0, tzinfo=timezone.utc)
        self.coordinator = Coordinator(self.root / "coordinator.sqlite")
        self.addCleanup(self.coordinator.db.close)
        self.coordinator.seed()
        self.capture = CaptureJournal(str(self.root / "capture.sqlite"))
        self.addCleanup(self.capture.close)
        self.db = sqlite3.connect(":memory:", isolation_level=None)
        self.addCleanup(self.db.close)
        self.db.executescript(SCHEMA)
        self.db.execute("INSERT INTO accounts VALUES(?)", ("KR_001",))
        self.raw = ContractRawWriter(self.db)
        self.calls = []
        lanes = {}
        for lane, caller in (("A", "1001"), ("B", "1002"), ("C", "1003")):
            reference = CredentialReference(lane, f"TOKEN_{lane}", f"CALLER_{lane}")
            def sender(req, timeout, *, lane=lane):
                self.calls.append(lane)
                return MockResponse()
            client = GraphHTTPClient(sender=sender, approved=True)
            lanes[lane] = MetaTransport(
                client=client, credentials=reference, caller_ig_id=caller,
                token=f"synthetic-token-{lane}", clock=lambda: self.when,
            )
        self.transports = ThreeLaneMetaTransport(lanes)
        self.runner = build_verified_serial_runner(
            self.coordinator, self.capture, self.raw, self.transports,
        )
        self.kwargs = {
            "run_id": "offline-synthetic-run",
            "roster_digest": "b" * 64,
            "day_kst": date(2026, 10, 21),
            "requested_at": self.when,
            "api_version": "v26.0",
            "caller_ids": {"A": "1001", "B": "1002", "C": "1003"},
        }

    def test_injected_sender_is_not_called_without_step_approval(self):
        with self.assertRaisesRegex(LiveSerialStepError, "approval"):
            self.runner.step(synthetic_roster(), approved=False, **self.kwargs)
        self.assertEqual(self.calls, [])
        self.assertEqual(self.coordinator.db.execute(
            "SELECT COUNT(*) FROM job_request_bindings"
        ).fetchone()[0], 0)

    def test_exact_one_synthetic_page_reaches_raw_and_cursor(self):
        result = self.runner.step(
            synthetic_roster(), approved=True, **self.kwargs,
        )
        self.assertEqual(result["status"], "page_committed")
        self.assertEqual(result["lane"], "A")
        self.assertEqual(result["account_ordinal"], 1)
        self.assertEqual(self.calls, ["A"])
        self.assertEqual(self.db.execute(
            "SELECT COUNT(*) FROM post_observations"
        ).fetchone()[0], 1)
        self.assertEqual(self.coordinator.db.execute(
            "SELECT COUNT(*) FROM page_evidence"
        ).fetchone()[0], 1)
        self.assertEqual(self.coordinator.db.execute(
            "SELECT state FROM jobs WHERE account_ordinal=1 AND page=1"
        ).fetchone()[0], "done")
        self.assertFalse(self.coordinator.db.execute(
            "SELECT halted FROM lanes WHERE lane='A'"
        ).fetchone()[0])

    def test_wrong_raw_port_cannot_be_substituted(self):
        db2 = sqlite3.connect(":memory:", isolation_level=None)
        self.addCleanup(db2.close)
        separate = ContractRawWriter(db2)
        with self.assertRaisesRegex(LiveSerialStepError, "durable ports"):
            from three_api_live_serial_step import LiveSerialStep
            LiveSerialStep(
                self.coordinator, self.runner.bindings,
                self.runner.evidence,
                type(self.runner.executor)(
                    self.transports, self.capture, separate,
                ),
            )

    def test_rejects_invalid_ports_before_any_collection(self):
        with self.assertRaises(SerialBootstrapError):
            build_verified_serial_runner(
                self.coordinator, self.capture, self.raw, object(),
            )
        self.assertEqual(self.calls, [])


if __name__ == "__main__":
    unittest.main()
