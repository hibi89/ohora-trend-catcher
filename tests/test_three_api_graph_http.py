"""Graph HTTP client contract tests: all senders are local functions.

No real HTTP or Meta calls. No real tokens. All IDs are synthetic.
"""
from datetime import datetime, timezone
from email.message import Message
from io import BytesIO
import json
from pathlib import Path
import sys
import tempfile
import unittest
from urllib.error import HTTPError
from urllib.parse import parse_qs, urlsplit
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "raw_v2"))

from three_api_coordinator import Coordinator
from three_api_credentials import make_references
from three_api_evidence import PageEvidenceStore
from three_api_graph_http import (
    GraphHTTPBlocked, GraphHTTPClient, GraphTransportError,
    make_strict_sender,
)
from three_api_http_fixture import stage_fixture_envelope
from three_api_http_preflight import PreparedGraphRequest, prepare_graph_request
from three_api_request_spec import build_request_spec

TOKEN = "SYNTHETIC_not_a_real_meta_token"
CALLER = "17840000000000000"


def refs():
    return make_references({lane: {
        "token_env_name": "TEST_%s_TOKEN" % lane,
        "ig_user_id_env_name": "TEST_%s_IG_USER_ID" % lane,
    } for lane in "ABC"})


def prepared(lane="A", username="nails_1", after=None):
    return prepare_graph_request(build_request_spec(lane, username, after=after),
                                 refs()[lane], CALLER)


class MemoryResponse:
    def __init__(self, status=200, body=b'{"business_discovery":{"media":{"data":[{"id":"post1"}]}}}',
                 headers=None):
        self.status = status
        self.content = BytesIO(body)
        self.headers = headers if headers is not None else {
            "X-App-Usage": '{"call_count":20}', "Set-Cookie": "SECRET_COOKIE",
            "Authorization": "SECRET_AUTHORIZATION",
        }

    def read(self, amount=-1):
        return self.content.read(amount)

    def __enter__(self):
        return self

    def __exit__(self, *_):
        self.content.close()


class GraphHTTPTests(unittest.TestCase):
    def test_default_disabled_without_sending(self):
        invoked = []
        client = GraphHTTPClient(sender=lambda *_args, **_kwargs: invoked.append(1))
        with self.assertRaisesRegex(GraphHTTPBlocked, "disabled"):
            client.fetch(prepared(), TOKEN)
        self.assertEqual(invoked, [])

    def test_approved_without_sender_still_blocked(self):
        with self.assertRaises(GraphHTTPBlocked):
            GraphHTTPClient(approved=True).fetch(prepared(), TOKEN)

    def test_successful_mock_wire_request(self):
        calls = []
        def sender(request, *, timeout):
            calls.append((request, timeout))
            return MemoryResponse()
        client = GraphHTTPClient(sender=sender, approved=True, timeout=7)
        with patch("socket.create_connection", side_effect=AssertionError("no network")):
            response = client.fetch(prepared(), TOKEN)
        self.assertEqual(len(calls), 1)
        request, timeout = calls[0]
        self.assertEqual(timeout, 7)
        self.assertEqual(request.get_method(), "GET")
        url = urlsplit(request.full_url)
        self.assertEqual((url.scheme, url.netloc), ("https", "graph.facebook.com"))
        self.assertEqual(url.path, "/v26.0/" + CALLER)
        self.assertIn("media.limit(4)", parse_qs(url.query)["fields"][0])
        self.assertNotIn(TOKEN, request.full_url)
        self.assertEqual(request.get_header("Authorization"), "Bearer " + TOKEN)
        self.assertEqual(response["status"], 200)
        self.assertEqual(response["headers"], {"X-App-Usage": '{"call_count":20}'})
        self.assertNotIn("SECRET_COOKIE", str(response))
        self.assertNotIn("SECRET_AUTHORIZATION", str(response))

    def test_error_payload_removes_message_and_token(self):
        body = ('{"error":{"code":190,"message":"bad token ' + TOKEN +
                '","error_subcode":123,"debug":"private"}}').encode()
        client = GraphHTTPClient(sender=lambda *_a, **_k: MemoryResponse(
            400, body=body), approved=True)
        envelope = client.fetch(prepared(), TOKEN)
        self.assertEqual(envelope["body"], {"error": {"code": 190, "error_subcode": 123}})
        self.assertNotIn(TOKEN, str(envelope))

    def test_only_explicit_code_100_invalid_cursor_message_becomes_boolean(self):
        body = json.dumps({"error": {
            "code": 100, "error_subcode": 33,
            "message": "(#100) The supplied after cursor is invalid: " + TOKEN,
        }}).encode()
        client = GraphHTTPClient(sender=lambda *_a, **_k: MemoryResponse(
            400, body=body), approved=True)
        envelope = client.fetch(prepared(after="opaque-old-cursor"), TOKEN)
        self.assertEqual(envelope["body"], {"error": {
            "code": 100, "error_subcode": 33, "cursor_invalid": True,
        }})
        self.assertNotIn(TOKEN, str(envelope))
        self.assertNotIn("supplied after cursor", str(envelope))

    def test_generic_parameter_or_auth_400_is_not_cursor_invalid(self):
        for status, code, message in (
            (400, 100, "Invalid parameter: field selection"),
            (400, 190, "Invalid cursor because token expired"),
            (403, 100, "Invalid cursor"),
        ):
            body = json.dumps({"error": {"code": code, "message": message}}).encode()
            client = GraphHTTPClient(sender=lambda *_a, **_k: MemoryResponse(
                status, body=body), approved=True)
            with self.subTest(status=status, code=code):
                envelope = client.fetch(prepared(after="opaque-old-cursor"), TOKEN)
                self.assertNotIn("cursor_invalid", envelope["body"]["error"])
                self.assertNotIn("Invalid parameter", str(envelope))

    def test_http_200_error_body_is_redacted(self):
        body = ('{"error":{"code":613,"message":"Authorization: ' + TOKEN + '"}}').encode()
        client = GraphHTTPClient(sender=lambda *_a, **_k: MemoryResponse(
            200, body=body), approved=True)
        result = client.fetch(prepared(), TOKEN)
        self.assertEqual(result["body"], {"error": {"code": 613}})

    def test_http_429_mock_error_response(self):
        message = Message()
        message["X-App-Usage"] = '{"call_count":98}'
        message["Set-Cookie"] = "LEAK"
        def sender(request, *, timeout):
            raise HTTPError(request.full_url, 429, "Synthetic rate limit",
                            message, BytesIO(b'{"error":{"code":613,"message":"SECRET"}}'))
        result = GraphHTTPClient(sender=sender, approved=True).fetch(prepared(), TOKEN)
        self.assertEqual(result["status"], 429)
        self.assertEqual(result["body"], {"error": {"code": 613}})
        self.assertEqual(result["headers"], {"X-App-Usage": '{"call_count":98}'})
        self.assertNotIn("SECRET", str(result))

    def test_non_json_429_preserves_status(self):
        def sender(request, *, timeout):
            raise HTTPError(request.full_url, 429, "limit", Message(),
                            BytesIO(b"<html>rate limit</html>"))
        result = GraphHTTPClient(sender=sender, approved=True).fetch(prepared(), TOKEN)
        self.assertEqual(result["status"], 429)
        self.assertEqual(result["body"], {"error": {}})

    def test_oversized_response_fails_closed(self):
        client = GraphHTTPClient(sender=lambda *_a, **_k: MemoryResponse(
            body=b"a" * (512 * 1024 + 1)), approved=True)
        with self.assertRaisesRegex(GraphTransportError, "large"):
            client.fetch(prepared(), TOKEN)

    def test_bad_success_json_fails_closed(self):
        for data in (b"not-json", b'["not an object"]', b""):
            client = GraphHTTPClient(
                sender=lambda *_a, **_k: MemoryResponse(body=data), approved=True)
            with self.subTest(data=data), self.assertRaises(GraphTransportError):
                client.fetch(prepared(), TOKEN)

    def test_transport_exception_does_not_leak(self):
        def sender(*_a, **_kw):
            raise RuntimeError("Failure includes " + TOKEN)
        client = GraphHTTPClient(sender=sender, approved=True)
        with self.assertRaises(GraphTransportError) as ctx:
            client.fetch(prepared(), TOKEN)
        self.assertEqual(str(ctx.exception), "HTTP transport failed")
        self.assertIsNone(ctx.exception.__cause__)

    def test_invalid_bearer_token_fails_before_sender(self):
        calls = []
        client = GraphHTTPClient(sender=lambda *_a, **_kw: calls.append(True),
                                 approved=True)
        for bad in ("a\nb", "", "a b", None):
            with self.subTest(bad=bad), self.assertRaises(GraphHTTPBlocked):
                client.fetch(prepared(), bad)
        self.assertEqual(calls, [])

    def test_forged_host_and_method_rejected(self):
        good = prepared()
        forged = PreparedGraphRequest(
            good.lane, good.username, "v26.0/../../../host", good.ig_user_id,
            good.fields, good.after_present, good.after, good.token_env_name)
        with self.assertRaises(GraphHTTPBlocked):
            GraphHTTPClient(sender=lambda *_a, **_kw: 1, approved=True).fetch(forged, TOKEN)
        forged = PreparedGraphRequest(
            good.lane, good.username, good.version, good.ig_user_id, good.fields,
            good.after_present, good.after, good.token_env_name, method="POST")
        with self.assertRaises(GraphHTTPBlocked):
            GraphHTTPClient(sender=lambda *_a, **_kw: 1, approved=True).fetch(forged, TOKEN)

    def test_forged_query_is_rejected(self):
        good = prepared()
        forged = PreparedGraphRequest(
            good.lane, good.username, good.version, good.ig_user_id,
            good.fields + ",access_token", good.after_present, good.after,
            good.token_env_name)
        with self.assertRaises(GraphHTTPBlocked):
            GraphHTTPClient(sender=lambda *_a, **_kw: 1, approved=True).fetch(forged, TOKEN)

    def test_timeout_and_approval_input_validation(self):
        for timeout in (0, 61, "15", True):
            with self.subTest(timeout=timeout), self.assertRaises(ValueError):
                GraphHTTPClient(timeout=timeout)
        with self.assertRaises(ValueError):
            GraphHTTPClient(approved="yes")
        with self.assertRaises(TypeError):
            GraphHTTPClient(sender="untrusted")

    def test_strict_sender_blocks_redirects(self):
        sender = make_strict_sender()
        # Constructing the sender cannot perform any network I/O.
        with self.assertRaises(GraphHTTPBlocked):
            GraphHTTPClient(sender=sender).fetch(prepared(), TOKEN)

    def test_graph_success_envelope_integrates_with_staging_and_quota(self):
        with tempfile.TemporaryDirectory() as directory:
            c = Coordinator(Path(directory) / "isolated.sqlite")
            try:
                c.seed()
                e = PageEvidenceStore(c)
                now = datetime(2026, 10, 19, tzinfo=timezone.utc)
                job = c.lease("A", now=now)
                client = GraphHTTPClient(
                    sender=lambda *_a, **_kw: MemoryResponse(
                        headers={"X-App-Usage": '{"call_count":84}'}),
                    approved=True)
                env = client.fetch(prepared(), TOKEN)
                result = stage_fixture_envelope(c, e, job, env, now=now)
                self.assertEqual(result["status"], "staged")
                self.assertEqual(c.db.execute(
                    "SELECT COUNT(*) FROM staging_posts").fetchone()[0], 1)
                dump = "\n".join(c.db.iterdump())
                self.assertNotIn(TOKEN, dump)
                self.assertNotIn("Authorization", dump)
            finally:
                c.db.close()

    def test_graph_429_envelope_halts_and_does_not_store(self):
        with tempfile.TemporaryDirectory() as directory:
            c = Coordinator(Path(directory) / "isolated.sqlite")
            try:
                c.seed()
                e = PageEvidenceStore(c)
                now = datetime(2026, 10, 19, tzinfo=timezone.utc)
                job = c.lease("A", now=now)
                client = GraphHTTPClient(sender=lambda *_a, **_kw: MemoryResponse(
                    429, body=b'{"error":{"code":613}}'), approved=True)
                env = client.fetch(prepared(), TOKEN)
                result = stage_fixture_envelope(c, e, job, env, now=now)
                self.assertEqual(result["status"], "blocked")
                self.assertEqual(result["reason"], "cooldown")
                self.assertEqual(c.db.execute(
                    "SELECT COUNT(*) FROM staging_posts").fetchone()[0], 0)
                self.assertIsNone(c.lease("A", now=now))
            finally:
                c.db.close()


if __name__ == "__main__":
    unittest.main()
