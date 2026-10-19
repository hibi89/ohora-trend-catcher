"""Offline-only quota parser tests."""
from datetime import date, datetime, timezone
from email.message import Message
from io import BytesIO
import json
import unittest
from pathlib import Path
import sys
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "raw_v2"))
from three_api_transport import parse_meta_usage, extract_next_cursor
from three_api_transport import MetaTransport
from three_api_graph_http import GraphHTTPClient
from three_api_credentials import make_references
from three_api_request_spec import build_request_spec
from three_api_runtime_contract import (
    CONTRACT_VERSION, RequestEnvelope, TransportFailure, digest_sha256,
)


TARGET_ID = "17840000000000000"


def request_envelope():
    spec = build_request_spec("A", "nails_1")
    return RequestEnvelope(
        CONTRACT_VERSION, "req-transport-1", "run-transport-1", "a" * 64,
        date(2026, 10, 19), 1, "KR_001", "nails_1", "A", "front", 1,
        None, datetime(2026, 10, 19, tzinfo=timezone.utc), spec,
        digest_sha256({"api_version": "v26.0", "target_ig_user_id": TARGET_ID, "spec": spec}),
        api_version="v26.0", target_ig_user_id=TARGET_ID,
    )


def references():
    return make_references({lane: {
        "token_env_name": "TEST_%s_TOKEN" % lane,
        "ig_user_id_env_name": "TEST_%s_IG_USER_ID" % lane,
    } for lane in "ABC"})


class MemoryResponse:
    def __init__(self, body, status=200, headers=None):
        self.status = status
        self.content = BytesIO(json.dumps(body).encode("utf-8"))
        self.headers = Message()
        for name, value in (headers or {}).items():
            self.headers[name] = value

    def read(self, amount=-1):
        return self.content.read(amount)

    def __enter__(self):
        return self

    def __exit__(self, *_):
        self.content.close()


def payload():
    return {"business_discovery": {
        "id": TARGET_ID, "username": "nails_1",
        "media": {"data": [{"id": "1234567890"}]},
    }}


def transport(*, headers=None, approved=True, sender=None):
    client = GraphHTTPClient(
        sender=sender or (lambda *_args, **_kwargs: MemoryResponse(
            payload(), headers=headers if headers is not None else {
                "X-App-Usage": '{"call_count":20}'
            })),
        approved=approved,
    )
    return MetaTransport(
        client=client, credentials=references()["A"],
        caller_ig_id=TARGET_ID, token="SYNTHETIC_NOT_A_REAL_TOKEN",
        clock=lambda: datetime(2026, 10, 19, 12, tzinfo=timezone.utc),
    )

class QuotaTests(unittest.TestCase):
    def test_cursor_extraction(self):
        payload = {"business_discovery": {"media": {
            "data": [{"id": "123"}],
            "paging": {"next": "https://example.invalid/?access_token=secret",
                       "cursors": {"after": "opaque_ABC"}}}}}
        self.assertEqual(extract_next_cursor(payload), "opaque_ABC")

    def test_full_page_without_cursor_blocks(self):
        payload = {"business_discovery": {"media": {
            "data": [{"id": str(i)} for i in range(4)],
            "paging": {"next": "https://example.invalid/"}}}}
        with self.assertRaises(ValueError):
            extract_next_cursor(payload)

    def test_cursor_cycle_blocks(self):
        payload = {"business_discovery": {"media": {
            "data": [{"id": "123"}],
            "paging": {"next": "https://example.invalid/",
                       "cursors": {"after": "same_cursor"}}}}}
        with self.assertRaises(ValueError):
            extract_next_cursor(payload, requested_cursor="same_cursor")

    def test_valid(self):
        self.assertEqual(parse_meta_usage({"x-app-usage": '{"call_count":84}'}), 84)
    def test_threshold(self):
        self.assertEqual(parse_meta_usage({"x-app-usage": '{"call_count":85}'}), 85)

    def test_business_multiple_entries(self):
        value = '{"one":[{"call_count":21},{"call_count":91}]}'
        self.assertEqual(parse_meta_usage({"X-Business-Use-Case-Usage": value}), 91)

    def test_empty_business_fails_closed(self):
        with self.assertRaises(ValueError):
            parse_meta_usage({"x-business-use-case-usage": "{}"})

    def test_non_numeric_fails_closed(self):
        with self.assertRaises(ValueError):
            parse_meta_usage({"x-app-usage": '{"call_count":"85"}'})

    def test_overflow_fails_closed(self):
        with self.assertRaises(ValueError):
            parse_meta_usage({"x-app-usage": '{"call_count":101}'})

    def test_missing(self):
        with self.assertRaises(ValueError):
            parse_meta_usage({})
    def test_invalid(self):
        with self.assertRaises(ValueError):
            parse_meta_usage({"x-app-usage": "invalid"})

    def test_meta_transport_returns_typed_response_from_local_sender(self):
        calls = []

        def sender(request, *, timeout):
            calls.append(request)
            return MemoryResponse(payload(), headers={
                "X-App-Usage": '{"call_count":20}',
                "Set-Cookie": "must-not-be-retained",
            })

        result = transport(sender=sender).send(request_envelope())
        self.assertEqual(result.request_id, "req-transport-1")
        self.assertEqual(result.http_status, 200)
        self.assertEqual(result.next_cursor, None)
        self.assertEqual(result.usage_headers, {"X-App-Usage": '{"call_count":20}'})
        self.assertEqual(len(calls), 1)
        self.assertNotIn("SYNTHETIC_NOT_A_REAL_TOKEN", calls[0].full_url)

    def test_default_graph_gate_blocks_before_sender(self):
        calls = []
        client = GraphHTTPClient(sender=lambda *_a, **_k: calls.append(1))
        blocked = MetaTransport(
            client=client, credentials=references()["A"],
            caller_ig_id=TARGET_ID, token="SYNTHETIC_NOT_A_REAL_TOKEN",
        )
        with self.assertRaises(TransportFailure) as ctx:
            blocked.send(request_envelope())
        self.assertEqual(ctx.exception.category, "approval_denied")
        self.assertEqual(calls, [])

    def test_missing_or_high_usage_fails_closed(self):
        for headers, category in (
            ({}, "usage_missing"),
            ({"X-App-Usage": '{"call_count":85}'}, "usage_limit"),
        ):
            with self.subTest(category=category):
                with self.assertRaises(TransportFailure) as ctx:
                    transport(headers=headers).send(request_envelope())
                self.assertEqual(ctx.exception.category, category)

    def test_lane_and_target_identity_are_bound_before_send(self):
        calls = []
        client = GraphHTTPClient(sender=lambda *_a, **_k: calls.append(1), approved=True)
        wrong_lane = MetaTransport(
            client=client, credentials=references()["B"],
            caller_ig_id=TARGET_ID, token="SYNTHETIC_NOT_A_REAL_TOKEN",
        )
        with self.assertRaises(TransportFailure) as ctx:
            wrong_lane.send(request_envelope())
        self.assertEqual(ctx.exception.category, "approval_denied")
        self.assertEqual(calls, [])

if __name__ == "__main__":
    unittest.main()
