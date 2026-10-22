"""Owner-required Reels view count: end-to-end OFFLINE contract/RAW history.

Never calls Meta, opens operating collection.sqlite, or uses a real token.
"""
from dataclasses import replace
from datetime import timedelta
from pathlib import Path
import copy
import json
import sqlite3
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "raw_v2"))
sys.path.insert(0, str(ROOT / "tests"))

from test_three_api_contract_executor import (
    FakeTransport, OBSERVED_AT, SCHEMA, make_request, make_response,
)
from three_api_capture import CaptureJournal
from three_api_contract_executor import ThreeApiContractExecutor, ContractExecutionError
from three_api_request_spec import RequestSpec
from three_api_raw_writer import ContractRawWriter
from three_api_runtime_contract import digest_sha256


class ReelMetricTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.capture = CaptureJournal(str(Path(self.temp.name) / "capture.sqlite"))
        self.db = sqlite3.connect(":memory:", isolation_level=None)
        self.db.executescript(SCHEMA)
        self.db.execute("INSERT INTO accounts VALUES(?)", ("KR_001",))
        self.writer = ContractRawWriter(self.db)
        self.base = make_request()

    def tearDown(self):
        self.capture.close()
        self.db.close()
        self.temp.cleanup()

    def collect(self, number, *, views=18711, media_type="VIDEO",
                product_type="REELS", include_view=True, hour=0):
        request = replace(self.base, request_id="req-reels-" + str(number))
        response = make_response(request)
        body = copy.deepcopy(response.payload)
        post = body["business_discovery"]["media"]["data"][0]
        post["media_type"] = media_type
        post["media_product_type"] = product_type
        if include_view:
            post["view_count"] = views
        response = replace(response, payload=body,
                           received_at=OBSERVED_AT + timedelta(hours=hour))
        t = FakeTransport(response)
        receipt = ThreeApiContractExecutor(t, self.capture, self.writer).execute(request)
        self.assertEqual(t.calls, 1)
        self.assertIn(receipt.status, ("committed", "already_committed"))
        return receipt, request

    def test_mandatory_reel_views_persist_as_separate_immutable_checks(self):
        for number, count, hour in ((1, 18711, 0), (2, 18715, 1), (3, 18715, 2)):
            self.collect(number, views=count, hour=hour)
        vals = self.db.execute(
            "SELECT s.full_values_json,s.field_states_json FROM post_checks c "
            "JOIN post_states s ON s.state_id=c.metrics_state_id "
            "ORDER BY c.observed_at"
        ).fetchall()
        self.assertEqual([json.loads(row[0])["view_count"] for row in vals],
                         [18711, 18715, 18715])
        self.assertTrue(all(json.loads(row[1])["view_count"]=="returned"
                            for row in vals))
        self.assertEqual(self.db.execute(
            "SELECT COUNT(*) FROM current_change_events "
            "WHERE field_path='metrics.view_count'").fetchone()[0], 1)
        self.assertEqual(self.db.execute(
            "SELECT COUNT(*) FROM post_observations").fetchone()[0], 3)
        self.assertEqual(self.db.execute(
            "SELECT COUNT(*) FROM field_observations "
            "WHERE field_name='view_count' AND status='returned'").fetchone()[0], 3)
        self.assertEqual(self.db.execute(
            "SELECT media_product_type FROM posts").fetchone()[0], "REELS")

    def test_image_absent_view_is_not_zero_or_not_tested(self):
        self.collect(1, media_type="IMAGE", product_type="FEED",
                     include_view=False)
        self.assertEqual(self.db.execute(
            "SELECT status FROM field_observations WHERE field_name='view_count'"
        ).fetchone()[0], "absent")
        state = self.db.execute(
            "SELECT s.full_values_json,s.field_states_json FROM post_checks c "
            "JOIN post_states s ON s.state_id=c.metrics_state_id"
        ).fetchone()
        self.assertNotIn("view_count", json.loads(state[0]))
        self.assertEqual(json.loads(state[1])["view_count"], "absent")

    def test_reels_explicit_zero_is_saved_and_null_is_distinct(self):
        self.collect(1, views=0)
        self.collect(2, views=None, hour=1)
        self.assertEqual(self.db.execute(
            "SELECT status FROM field_observations "
            "WHERE field_name='view_count' ORDER BY rowid"
        ).fetchall(), [("returned",), ("null",)])

    def test_reels_views_support_large_counts_without_truncation(self):
        self.collect(1, views=2**31-1)
        self.collect(2, views=2**31, hour=1)
        rows = self.db.execute(
            "SELECT s.full_values_json FROM post_checks c "
            "JOIN post_states s ON s.state_id=c.metrics_state_id "
            "ORDER BY c.observed_at"
        ).fetchall()
        self.assertEqual(
            [json.loads(row[0])["view_count"] for row in rows],
            [2**31-1, 2**31],
        )
        self.assertEqual(self.db.execute(
            "SELECT COUNT(*) FROM current_change_events "
            "WHERE field_path='metrics.view_count'"
        ).fetchone()[0], 1)

    def test_invalid_views_fail_closed_without_raw_receipt(self):
        for value in (True, -1, "100"):
            with self.subTest(value=value):
                request = replace(self.base, request_id="req-invalid-" + str(value))
                response = make_response(request)
                body = copy.deepcopy(response.payload)
                body["business_discovery"]["media"]["data"][0]["view_count"] = value
                t = FakeTransport(replace(response, payload=body))
                with self.assertRaises(ContractExecutionError):
                    ThreeApiContractExecutor(t, self.capture, self.writer).execute(request)
                # A rejected response must not even create the receipt table.
                self.assertEqual(self.db.execute(
                    "SELECT COUNT(*) FROM sqlite_master "
                    "WHERE type='table' AND name='three_api_contract_receipts'"
                ).fetchone()[0], 0)

    def test_legacy_request_keeps_old_metric_field_evidence(self):
        spec = self.base.request_spec
        legacy_fields = ("id,caption,media_type,media_url,permalink,"
                         "timestamp,like_count,comments_count,thumbnail_url")
        old_spec = replace(spec, media_fields=legacy_fields)
        old_req = replace(self.base, request_spec=old_spec,
                          request_spec_digest=digest_sha256({
                              "api_version":self.base.api_version,
                              "target_ig_user_id":self.base.target_ig_user_id,
                              "spec":old_spec,
                          }), request_id="req-legacy-view-1")
        resp = make_response(old_req)
        t = FakeTransport(resp)
        ThreeApiContractExecutor(t, self.capture, self.writer).execute(old_req)
        state = self.db.execute(
            "SELECT s.full_values_json,s.field_states_json FROM post_checks c "
            "JOIN post_states s ON s.state_id=c.metrics_state_id"
        ).fetchone()
        self.assertNotIn("view_count", json.loads(state[0]))
        self.assertNotIn("view_count", json.loads(state[1]))
        self.assertEqual(self.db.execute(
            "SELECT status FROM field_observations WHERE field_name='view_count'"
        ).fetchone()[0], "not_tested")


if __name__ == "__main__":
    unittest.main()
