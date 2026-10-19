"""Offline contract checks; no sockets, tokens, or database."""
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
import sys
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'raw_v2'))

from three_api_request_spec import build_request_spec
from three_api_runtime_contract import (
    CONTRACT_VERSION, CaptureRecord, CursorState, RawWriteReceipt,
    RequestEnvelope, canonical_bytes, digest_sha256, utc,
)


class ContractTests(unittest.TestCase):
    def make_request(self):
        spec = build_request_spec("A", "example.nails", limit=4)
        return RequestEnvelope(
            contract_version=CONTRACT_VERSION, request_id="req-1",
            run_id="run-1", roster_digest="roster-sha", day_kst=date(2026, 10, 19),
            account_ordinal=1, account_key="KR_001", username="example.nails",
            lane="A", phase="front", page=1, cursor=None,
            requested_at=datetime(2026, 10, 19, 9, tzinfo=timezone(timedelta(hours=9))),
            request_spec=spec, api_version="v24.0", target_ig_user_id="17841400000000000",
            request_spec_digest=digest_sha256({"api_version": "v24.0", "target_ig_user_id": "17841400000000000", "spec": spec}),
        )

    def test_request_time_normalized_and_spec_bound(self):
        request = self.make_request()
        self.assertEqual(request.requested_at.isoformat(), "2026-10-19T00:00:00+00:00")
        self.assertEqual(request.request_spec.limit, 4)

    def test_naive_time_rejected(self):
        with self.assertRaises(ValueError):
            utc(datetime(2026, 10, 19))

    def test_canonical_digest_stable_across_mapping_order(self):
        self.assertEqual(digest_sha256({"a": 1, "b": 2}),
                         digest_sha256({"b": 2, "a": 1}))
        self.assertIn(b'"a":1', canonical_bytes({"a": 1}))

    def test_capture_digest_includes_identity_and_observation(self):
        request = self.make_request()
        cursor = CursorState(CONTRACT_VERSION, request.request_id, "src-sha", None, None, 1)
        def record(observed):
            return CaptureRecord(
                CONTRACT_VERSION, "capture-1", request,
                {"api_version": "v1", "fields": ["id"], "limit": 4},
                {"data": [{"id": "123"}]}, [{"id": "123"}],
                {"followers_count": None}, {"followers_count": "null"},
                {"123": "not_tested"}, cursor, observed,
            )
        now = datetime(2026, 10, 19, tzinfo=timezone.utc)
        self.assertNotEqual(record(now).capture_digest,
                            record(now + timedelta(seconds=1)).capture_digest)

    def test_raw_receipt_requires_final_status(self):
        now = datetime(2026, 10, 19, tzinfo=timezone.utc)
        with self.assertRaises(ValueError):
            RawWriteReceipt(CONTRACT_VERSION, "c", "digest", "raw-1", "pending", now, {})
        self.assertEqual(RawWriteReceipt(
            CONTRACT_VERSION, "c", "digest", "raw-1", "committed", now,
            {"posts": 2}).row_counts["posts"], 2)


    def test_request_digest_rejects_target_or_api_change(self):
        from dataclasses import replace
        request = self.make_request()
        with self.assertRaises(ValueError):
            replace(request, target_ig_user_id="another-id")
        with self.assertRaises(ValueError):
            replace(request, api_version="v99.0")

    def test_history_hold_and_cursor_mismatch(self):
        from dataclasses import replace
        request = self.make_request()
        with self.assertRaises(ValueError):
            replace(request, phase="history")
        with self.assertRaises(ValueError):
            replace(request, cursor="unrelated")

    def test_capture_rejects_unaware_observation_and_field_state(self):
        request = self.make_request()
        cursor = CursorState(CONTRACT_VERSION, request.request_id, "src", None, None, 1)
        args = (CONTRACT_VERSION, "capture-1", request,
                {"api_version": "v24.0"}, {"data": []}, [],
                {}, {"likes": "absent"}, {}, cursor)
        with self.assertRaises(ValueError):
            CaptureRecord(*args, datetime(2026, 10, 19))
        with self.assertRaises(ValueError):
            CaptureRecord(*args[:7], {"likes": "unknown"}, *args[8:],
                          datetime(2026, 10, 19, tzinfo=timezone.utc))

    def test_raw_receipt_utc_and_nonnegative_counts(self):
        now = datetime(2026, 10, 19, 9, tzinfo=timezone(timedelta(hours=9)))
        receipt = RawWriteReceipt(CONTRACT_VERSION, "capture", "digest", "raw",
                                  "already_committed", now, {"posts": 0})
        self.assertEqual(receipt.committed_at.utcoffset(), timedelta(0))
        with self.assertRaises(ValueError):
            RawWriteReceipt(CONTRACT_VERSION, "capture", "digest", "raw",
                            "committed", now, {"posts": -1})


if __name__ == "__main__":
    unittest.main()
