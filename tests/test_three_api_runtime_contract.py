"""Offline contract checks; no sockets, tokens, or database."""
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
import sys
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'raw_v2'))

from three_api_request_spec import build_request_spec
from three_api_runtime_contract import (
    CONTRACT_VERSION, CaptureRecord, CursorState, RawWriteReceipt,
    RequestEnvelope, ResponseEnvelope, ContractError, FailureEvidence,
    capture_from_response, sanitize_meta_response, requested_fields,
    validate_capture_receipt, validate_raw_receipt, CaptureReceipt,
    TransportFailure, persist_capture_verified, commit_raw_and_confirm,
    canonical_bytes, digest_sha256, utc,
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

    def make_record(self, timestamp=None):
        request = self.make_request()
        observed = timestamp or datetime(2026, 10, 19, tzinfo=timezone.utc)
        response = ResponseEnvelope(CONTRACT_VERSION, request.request_id, 200,
            {"business_discovery": {"followers_count": None, "media_count": 1,
              "media": {"data": [{"id": "123", "like_count": 0}]}}},
            {"x-app-usage-call-count": "3"}, observed, None)
        return capture_from_response(
            request, response, capture_id="capture-1",
            normalized_posts=[{"id": "123", "media_type": "IMAGE", "like_count": 0}],
            account_observation={"followers_count": None, "media_count": 1},
            account_fields_tested=frozenset(("followers_count", "media_count")),
            carousel_completeness={"123": "not_applicable"}, source_digest="source-sha")

    def test_capture_digest_includes_identity_and_observation(self):
        now = datetime(2026, 10, 19, tzinfo=timezone.utc)
        self.assertNotEqual(self.make_record(now).capture_digest,
                            self.make_record(now + timedelta(seconds=1)).capture_digest)

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
        from dataclasses import replace
        record = self.make_record()
        with self.assertRaises(ValueError):
            replace(record, observed_at=datetime(2026, 10, 19))
        with self.assertRaises(ValueError):
            replace(record, observed_at=record.observed_at + timedelta(seconds=1))
        with self.assertRaises(ValueError):
            replace(record, field_status={"followers_count": "absent", "media_count": "returned"})
        with self.assertRaises(ValueError):
            replace(record, carousel_completeness={"123": "complete"})

    def test_raw_receipt_utc_and_nonnegative_counts(self):
        now = datetime(2026, 10, 19, 9, tzinfo=timezone(timedelta(hours=9)))
        receipt = RawWriteReceipt(CONTRACT_VERSION, "capture", "digest", "raw",
                                  "already_committed", now, {"posts": 0})
        self.assertEqual(receipt.committed_at.utcoffset(), timedelta(0))
        with self.assertRaises(ValueError):
            RawWriteReceipt(CONTRACT_VERSION, "capture", "digest", "raw",
                            "committed", now, {"posts": -1})

    def test_error_scopes_and_retry_not_approval(self):
        with self.assertRaises(ValueError):
            TransportFailure(CONTRACT_VERSION, "r", "raw_conflict", "account",
                             True, None, "conflict")
        failure = TransportFailure(CONTRACT_VERSION, "r", "transport",
                                   "account", True, None, "safe")
        self.assertTrue(failure.retryable)
        self.assertNotIn("retry_approved", failure.__dict__)

    def test_sanitizer_drops_paging_url_and_secrets(self):
        raw = {"business_discovery": {
            "username": "abc", "access_token": "secret",
            "media": {"data": [{"id": "123", "like_count": None,
                  "token": "BAD"}],
                  "paging": {"next": "https://graph.example/?access_token=BAD"}}}}
        clean = sanitize_meta_response(raw)
        content = str(clean)
        self.assertNotIn("BAD", content)
        self.assertNotIn("paging", content)
        self.assertNotIn("access_token", content)
        from dataclasses import replace
        record = self.make_record()
        with self.assertRaises(ValueError):
            replace(record, sanitized_response={"account": {},
                "media": {"data": []}, "Authorization": "secret"})
        with self.assertRaises(ValueError):
            replace(record, normalized_posts=[{"id": "123", "access_token": "secret"}])

    def test_failure_not_success_capture(self):
        record = self.make_record()
        request = record.request
        result = ResponseEnvelope(CONTRACT_VERSION, request.request_id, 401,
                                  {"error": {"message": "bad"}}, {},
                                  record.observed_at, None)
        with self.assertRaises(ContractError):
            capture_from_response(request, result, capture_id="fail",
                normalized_posts=[], account_observation={},
                account_fields_tested=frozenset(), carousel_completeness={},
                source_digest="s")
        failure = FailureEvidence(CONTRACT_VERSION, request.request_id, request.run_id,
            "auth", "global", "failed", record.observed_at, "authentication rejected", 401)
        self.assertEqual(failure.scope, "global")

    def test_usage_missing_blocks_success(self):
        record = self.make_record()
        response = ResponseEnvelope(CONTRACT_VERSION, record.request.request_id,
                                    200, {"media": {"data": []}}, {},
                                    record.observed_at, None)
        with self.assertRaises(ContractError) as ctx:
            capture_from_response(record.request, response, capture_id="c",
                normalized_posts=[], account_observation={},
                account_fields_tested=frozenset(), carousel_completeness={},
                source_digest="s")
        self.assertEqual(ctx.exception.category, "usage_missing")

    def test_capture_and_raw_receipt_identity(self):
        from dataclasses import replace
        record = self.make_record()
        receipt = CaptureReceipt(CONTRACT_VERSION, record.capture_id,
            record.request.request_id, record.request.account_key,
            record.request.lane, record.observed_at, record.capture_digest,
            "capture-key", True)
        validate_capture_receipt(record, receipt)
        with self.assertRaises(ValueError):
            validate_capture_receipt(record, replace(receipt, request_id="wrong"))
        raw = RawWriteReceipt(CONTRACT_VERSION, record.capture_id,
            record.capture_digest, "raw-1", "committed", record.observed_at,
            {"posts": 1})
        validate_raw_receipt(record, raw)
        with self.assertRaises(ValueError):
            validate_raw_receipt(record, replace(raw, capture_digest="0" * 64))

    def test_post_like_comment_states(self):
        from dataclasses import replace
        record = self.make_record()
        self.assertEqual(record.post_field_status["123"]["like_count"], "returned")
        self.assertEqual(record.post_field_status["123"]["comments_count"], "absent")
        with self.assertRaises(ValueError):
            replace(record, post_field_status={"123": {
                "like_count": "absent", "comments_count": "absent"}})

    def test_recovery_reuses_raw_receipt_without_new_write(self):
        record = self.make_record()
        receipt = CaptureReceipt(CONTRACT_VERSION, record.capture_id,
            record.request.request_id, record.request.account_key,
            record.request.lane, record.observed_at, record.capture_digest,
            "storage", True)
        raw_receipt = RawWriteReceipt(CONTRACT_VERSION, record.capture_id,
            record.capture_digest, "raw1", "already_committed",
            record.observed_at, {"posts": 1})
        class CaptureFake:
            def __init__(self):
                self.confirmations = []
            def save_capture(self, value):
                return receipt
            def get_capture(self, key):
                return record
            def confirm_processed(self, capture_id, raw_id):
                self.confirmations.append((capture_id, raw_id))
        class RawFake:
            def __init__(self):
                self.writes = 0
            def get_raw_receipt(self, key):
                return raw_receipt
            def write_capture(self, value):
                self.writes += 1
                raise AssertionError("RAW should never be written a second time")
        capture, raw = CaptureFake(), RawFake()
        persist_capture_verified(capture, record)
        self.assertEqual(commit_raw_and_confirm(capture, raw, record), raw_receipt)
        self.assertEqual(raw.writes, 0)
        self.assertEqual(capture.confirmations, [("capture-1", "raw1")])

    def test_capture_reread_digest_mismatch_blocks(self):
        from dataclasses import replace
        record = self.make_record()
        receipt = CaptureReceipt(CONTRACT_VERSION, record.capture_id,
            record.request.request_id, record.request.account_key,
            record.request.lane, record.observed_at, record.capture_digest,
            "storage", True)
        class CaptureFake:
            def save_capture(self, value):
                return receipt
            def get_capture(self, key):
                return replace(record, capture_id="different")
        with self.assertRaises(ContractError):
            persist_capture_verified(CaptureFake(), record)


    def test_unrequested_engagement_is_not_tested(self):
        from dataclasses import replace
        request = self.make_request()
        spec = replace(request.request_spec,
                       media_fields="id,caption,media_type,comments_count")
        request = replace(request, request_spec=spec,
            request_spec_digest=digest_sha256({
                "api_version": request.api_version,
                "target_ig_user_id": request.target_ig_user_id, "spec": spec}))
        now = datetime(2026, 10, 19, tzinfo=timezone.utc)
        response = ResponseEnvelope(CONTRACT_VERSION, request.request_id, 200,
            {"media": {"data": [{"id": "123", "comments_count": 0}]}},
            {"x-app-usage-call-count": "2"}, now, None)
        record = capture_from_response(request, response, capture_id="c",
            normalized_posts=[{"id": "123", "comments_count": 0}],
            account_observation={}, account_fields_tested=frozenset(),
            carousel_completeness={"123": "not_applicable"}, source_digest="s")
        self.assertEqual(record.post_field_status["123"]["like_count"], "not_tested")
        self.assertEqual(record.post_field_status["123"]["comments_count"], "returned")
        with self.assertRaises(ContractError):
            capture_from_response(request, response, capture_id="c",
                normalized_posts=[{"id": "123", "like_count": 2}],
                account_observation={}, account_fields_tested=frozenset(),
                carousel_completeness={"123": "not_applicable"}, source_digest="s")

    def test_normalized_post_allowlist_types_and_bounds(self):
        from dataclasses import replace
        record = self.make_record()
        for bad in (
            [{"id": "123", "media_type": "IMAGE", "private_key": "x"}],
            [{"id": "123", "media_type": "IMAGE", "like_count": True}],
            [{"id": "123", "media_type": "IMAGE", "comments_count": -1}],
            [{"id": "123", "media_type": "IMAGE", "caption": "x" * 9000}],
            [{"id": "123", "media_type": "IMAGE", "access_token": "s"}],
            [{"id": "123", "media_type": "IMAGE", "children": []}],
            [{"id": str(i), "media_type": "IMAGE"} for i in range(5)],
        ):
            with self.subTest(bad=str(bad)[:50]), self.assertRaises(ValueError):
                replace(record, normalized_posts=bad)

    def test_carousel_child_schema_and_maximum(self):
        from dataclasses import replace
        record = self.make_record()
        base = {"id": "123", "media_type": "CAROUSEL_ALBUM"}
        for children in (
            [{"id": "124", "authorization": "secret"}],
            [{"id": "124", "media_type": "UNSAFE"}],
            [{"id": "124", "media_url": "https://graph.example/paging?access_token=x"}],
            [{"id": "124", "media_url": 123}],
            [{"id": "124"}, {"id": "124"}],
            [{"id": str(i)} for i in range(200, 221)],
        ):
            with self.subTest(children=str(children)[:50]), self.assertRaises(ValueError):
                replace(record, normalized_posts=[{**base, "children": children}],
                    carousel_completeness={"123": "complete"},
                    post_field_status={"123": {"like_count": "absent",
                                                "comments_count": "absent"}})



if __name__ == "__main__":
    unittest.main()
