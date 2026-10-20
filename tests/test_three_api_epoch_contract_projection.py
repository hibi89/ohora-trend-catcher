"""P4 P3 <-> real CaptureJournal / RAW v2 receipt projection, offline only.

All requests use an in-memory synthetic transport, a temp capture journal,
an isolated in-memory RAW v2 schema, and an in-memory P3 epoch database.
Never send Meta HTTP or open the operating collection database.
"""
from dataclasses import replace
from datetime import date, timedelta
from pathlib import Path
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

    def test_durable_capture_and_raw_receipt_advance_p3_only_once(self):
        transport, receipt = self.execute()
        self.assertEqual(transport.calls, 1)
        self.assertEqual(self.state(), "bound")
        self.assertEqual(
            self.projector.project_front_page(self.job)["status"], "committed"
        )
        self.assertEqual(self.state(), "done")
        self.assertEqual(self.p3db.execute(
            "SELECT state FROM jobs_v2 WHERE epoch_id=? AND account_ordinal=1 "
            "ORDER BY page_seq", (self.day,)
        ).fetchall(), [
            ("done",), ("not_required",), ("not_required",)
        ])
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
