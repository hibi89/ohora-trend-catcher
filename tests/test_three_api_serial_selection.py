"""Offline A/B/C job selection, durability and usage-gate integration.

Entirely isolated coordinator/Capture/RAW SQLite and in-memory response pairs.
No credentials, Meta HTTP transport, operating collection.sqlite or scheduler.
"""
from dataclasses import replace
from datetime import timedelta
from pathlib import Path
import sqlite3
import sys
import tempfile
import unittest
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "raw_v2"))
sys.path.insert(0, str(ROOT / "tests"))

from three_api_capture import CaptureJournal
from three_api_contract_executor import ContractExecutionError, ThreeApiContractExecutor
from three_api_coordinator import Coordinator
from three_api_evidence import PageEvidenceStore
from three_api_job_binding import JobRequestBindingStore
from three_api_raw_writer import ContractRawWriter
from three_api_request_spec import build_request_spec
from three_api_runtime_contract import digest_sha256
from three_api_serial_contract_bridge import (
    OfflineFixtureTransport, OfflineSerialContractBridge, SerialContractBridgeError,
)
from three_api_serial_lock import serial_tick_lock
from test_three_api_contract_executor import (
    OBSERVED_AT, SCHEMA, make_request, make_response,
)


class SerialFixtureSelectionTests(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.root = Path(tmp.name)
        self.coord = Coordinator(self.root / "fixture-control.sqlite")
        self.coord.seed()
        self.evidence = PageEvidenceStore(self.coord)
        self.bindings = JobRequestBindingStore(self.coord)
        self.capture = CaptureJournal(str(self.root / "fixture-capture.sqlite"))
        self.raw_db = sqlite3.connect(
            self.root / "fixture-raw.sqlite", isolation_level=None)
        self.raw_db.executescript(SCHEMA)
        self.raw_db.executemany(
            "INSERT INTO accounts VALUES(?)",
            [("KR_001",), ("KR_002",), ("KR_151",)])
        self.raw = ContractRawWriter(self.raw_db)
        self.transport = OfflineFixtureTransport(None)
        self.executor = ThreeApiContractExecutor(
            self.transport, self.capture, self.raw, clock=lambda: OBSERVED_AT)
        self.bridge = OfflineSerialContractBridge(
            self.coord, self.bindings, self.executor)
        self.addCleanup(lambda: self.raw_db.close())
        self.addCleanup(lambda: self.capture.close())
        self.addCleanup(lambda: self.coord.db.close())
        self.base_request = make_request()

    def fixture(self, lane="A", ordinal=1, page=1, *,
                after=None, usage=20, next_cursor=None):
        username = f"nails_{ordinal}"
        account_key = f"KR_{ordinal:03d}"
        spec = build_request_spec(lane, username, after=after)
        request = replace(
            self.base_request, lane=lane, phase="front" if page == 1 else "catchup",
            account_ordinal=ordinal, account_key=account_key,
            username=username, page=page, cursor=after,
            request_id="req-" + f"{ordinal:03x}{page:x}{lane.lower()}".encode().hex().ljust(64, "0"),
            request_spec=spec, request_spec_digest=digest_sha256({
                "api_version": self.base_request.api_version,
                "target_ig_user_id": self.base_request.target_ig_user_id,
                "spec": spec,
            }),
        )
        # Request IDs must be lowercase SHA-formatted and stable, not a real username.
        reqid = "req-" + digest_sha256({
            "lane": lane, "ordinal": ordinal, "page": page,
            "after": after,
        })
        request = replace(request, request_id=reqid)
        head = make_response(
            request, usage=(
                {} if usage is None else
                {"x-app-usage": '{"call_count":' + str(usage) + '}'}
            ))
        root = dict(head.payload["business_discovery"])
        root["username"] = username
        media = dict(root["media"])
        post = dict(media["data"][0])
        post["id"] = str(100000000000 + ordinal * 10 + page)
        post["username"] = username
        media["data"] = [post]
        if next_cursor is not None:
            media["paging"] = {
                "next": "https://graph.facebook.com/fixture-only",
                "cursors": {"after": next_cursor},
            }
        root["media"] = media
        response = replace(
            head, payload={"business_discovery": root}, next_cursor=next_cursor)
        return request, response

    def tick(self, fixtures, *, now=OBSERVED_AT, lane_order=("A", "B", "C")):
        return self.bridge.step_fixture_serial(
            fixtures, self.evidence, now=now, lane_order=lane_order)

    def lane(self, lane):
        return dict(self.coord.db.execute(
            "SELECT * FROM lanes WHERE lane=?", (lane,)).fetchone())

    def counts(self):
        return (
            self.coord.db.execute("SELECT COUNT(*) FROM page_evidence").fetchone()[0],
            self.raw_db.execute("SELECT COUNT(*) FROM post_observations").fetchone()[0],
            self.coord.db.execute("SELECT COUNT(*) FROM job_request_bindings").fetchone()[0],
        )

    def test_selects_a_then_b_round_robin_after_durable_success(self):
        fixtures = {
            ("A", 1, 1): self.fixture("A", 1),
            ("B", 151, 1): self.fixture("B", 151),
        }
        first = self.tick(fixtures)
        second = self.tick(fixtures)
        self.assertEqual((first["selected_lane"], first["account_ordinal"]), ("A", 1))
        self.assertEqual((second["selected_lane"], second["account_ordinal"]), ("B", 151))
        self.assertEqual(self.transport.calls, 2)
        self.assertEqual(self.counts(), (2, 2, 2))
        self.assertEqual(self.coord.db.execute(
            "SELECT last_lane FROM offline_contract_serial_turn").fetchone()[0], "B")

    def test_usage_85_halts_only_fixture_lane_and_b_is_available(self):
        fixtures = {
            ("A", 1, 1): self.fixture("A", 1, usage=85),
            ("B", 151, 1): self.fixture("B", 151),
        }
        self.assertEqual(self.tick(fixtures)["selected_lane"], "A")
        self.assertEqual(self.lane("A")["reason"], "usage_limit")
        self.assertEqual(self.lane("A")["halted"], 1)
        self.assertEqual(self.tick(fixtures)["selected_lane"], "B")
        self.assertEqual(self.lane("B")["halted"], 0)
        self.assertEqual(self.transport.calls, 2)
        self.assertEqual(self.counts(), (2, 2, 2))
        self.assertFalse(self.coord.reset_after_verified_cooldown(
            "A", now=OBSERVED_AT + timedelta(minutes=64)))
        self.assertTrue(self.coord.reset_after_verified_cooldown(
            "A", now=OBSERVED_AT + timedelta(minutes=65)))

    def test_usage_missing_halts_and_bound_request_never_resends(self):
        fixtures = {
            ("A", 1, 1): self.fixture("A", 1, usage=None),
            ("B", 151, 1): self.fixture("B", 151),
        }
        with self.assertRaises(ContractExecutionError):
            self.tick(fixtures)
        self.assertEqual(self.transport.calls, 1)
        self.assertEqual(self.lane("A")["reason"], "missing_usage")
        self.assertEqual(self.lane("A")["halted"], 1)
        self.assertEqual(self.counts(), (0, 0, 1))
        self.assertEqual(self.tick(fixtures)["selected_lane"], "B")
        self.assertEqual(self.transport.calls, 2)

    def test_missing_fixture_returns_lease_to_pending_and_sends_nothing(self):
        with self.assertRaisesRegex(
                SerialContractBridgeError, "Fixture pair unavailable"):
            self.tick({})
        self.assertEqual(self.transport.calls, 0)
        self.assertEqual(self.counts(), (0, 0, 0))
        job = self.coord.db.execute(
            "SELECT state,lease_id FROM jobs WHERE account_ordinal=1 AND page=1"
        ).fetchone()
        self.assertEqual(tuple(job), ("pending", None))
        self.assertEqual(self.tick({("A", 1, 1): self.fixture("A", 1)})[
            "selected_lane"], "A")

    def test_invalid_fixture_identity_does_not_reserve_or_send(self):
        req, response = self.fixture("A", 1)
        bad = replace(response, request_id="req-" + "f" * 64)
        with self.assertRaises(SerialContractBridgeError):
            self.tick({("A", 1, 1): (req, bad)})
        self.assertEqual(self.counts(), (0, 0, 0))
        self.assertEqual(self.transport.calls, 0)

    def test_bound_capture_recovers_after_lease_expiry_without_resend(self):
        fixture = self.fixture("A", 1)
        fixtures = {("A", 1, 1): fixture}
        with mock.patch.object(
                self.evidence, "commit_page",
                side_effect=OSError("synthetic interruption")):
            with self.assertRaises(OSError):
                self.tick(fixtures)
        self.assertEqual(self.counts(), (0, 1, 1))
        self.assertEqual(self.transport.calls, 1)
        recovered = self.tick(fixtures, now=OBSERVED_AT + timedelta(minutes=16))
        self.assertEqual((recovered["status"], recovered["recovery_only"]),
                         ("page_committed", True))
        self.assertEqual(recovered["selected_lane"], "A")
        self.assertEqual(self.counts(), (1, 1, 1))
        self.assertEqual(self.transport.calls, 1)

    def test_bound_without_capture_never_replays_http(self):
        job = self.coord.lease_for_dispatch("A", now=OBSERVED_AT)
        self.bindings.reserve(
            job, self.fixture("A", 1)[0], self.evidence, now=OBSERVED_AT)
        later = OBSERVED_AT + timedelta(minutes=16)
        with self.assertRaisesRegex(
                SerialContractBridgeError, "manual reconciliation"):
            self.tick({("A", 1, 1): self.fixture("A", 1)}, now=later)
        self.assertEqual(self.transport.calls, 0)
        self.assertEqual(self.counts(), (0, 0, 1))

    def test_serial_busy_does_not_lease_a_job(self):
        with serial_tick_lock(self.coord.path):
            result = self.tick({("A", 1, 1): self.fixture("A", 1)})
        self.assertEqual(result, {"status": "serial_busy", "selected_lane": None})
        self.assertEqual(self.counts(), (0, 0, 0))
        self.assertEqual(self.transport.calls, 0)

    def test_lane_order_and_port_restrictions_fail_before_reservation(self):
        fixture = {("A", 1, 1): self.fixture("A", 1)}
        with self.assertRaises(SerialContractBridgeError):
            self.tick(fixture, lane_order=("A", "A", "C"))
        self.executor.transport = object()
        with self.assertRaises(SerialContractBridgeError):
            self.tick(fixture)
        self.assertEqual(self.transport.calls, 0)
        self.assertEqual(self.counts(), (0, 0, 0))

    def test_a_then_b_then_c_with_frozen_page2_cursor_assignment(self):
        fixtures = {
            ("A", 1, 1): self.fixture("A", 1, next_cursor="fixturecursor2"),
            ("B", 151, 1): self.fixture("B", 151),
            ("C", 1, 2): self.fixture(
                "C", 1, 2, after="fixturecursor2"),
        }
        steps = [self.tick(fixtures) for _ in range(3)]
        self.assertEqual([s["selected_lane"] for s in steps], ["A", "B", "C"])
        self.assertEqual([s["page"] for s in steps], [1, 1, 2])
        self.assertEqual(self.counts(), (3, 3, 3))
        self.assertEqual(self.transport.calls, 3)
        self.assertEqual(self.evidence.required_cursor(1, 2), "fixturecursor2")
        self.assertEqual(self.coord.db.execute(
            "SELECT last_lane FROM offline_contract_serial_turn").fetchone()[0], "C")


if __name__ == "__main__":
    unittest.main()
