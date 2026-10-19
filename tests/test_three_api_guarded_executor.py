"""End-to-end guarded three-API executor tests. Network access is forbidden.

All credentials are synthetic and all senders are in-memory callables.
This suite never touches the production RAW v2 collection database.
"""
from datetime import datetime, timedelta, timezone
from io import BytesIO
from pathlib import Path
import sys
import tempfile
import unittest
from unittest import mock
from urllib.error import HTTPError
from email.message import Message

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "raw_v2"))
from three_api_credentials import make_references
from three_api_graph_http import GraphHTTPClient
from three_api_guarded_executor import (
    GuardedExecutionError,
    GuardedThreeApiExecutor,
)

TOKEN_A = "FAKE_A_not_a_real_access_token"
TOKEN_B = "FAKE_B_not_a_real_access_token"
TOKEN_C = "FAKE_C_not_a_real_access_token"
NOW = datetime(2026, 10, 19, tzinfo=timezone.utc)


def references():
    return make_references({lane: {
        "token_env_name": f"TEST_{lane}_TOKEN",
        "ig_user_id_env_name": f"TEST_{lane}_ID",
    } for lane in ("A", "B", "C")})


def environment():
    result = {}
    for lane, token in zip("ABC", (TOKEN_A, TOKEN_B, TOKEN_C)):
        result[f"TEST_{lane}_TOKEN"] = token
        result[f"TEST_{lane}_ID"] = str(17840000000000000 + "ABC".index(lane))
    return result


def accounts():
    return [{"ordinal": i, "key": "KR_%03d" % i,
             "username": "nails_%d" % i} for i in range(1, 295)]


def payload(post="post_1", *, cursor=None):
    media = {"data": [{"id": post, "caption": "synthetic design"}]}
    if cursor:
        media["paging"] = {
            "next": "https://graph.facebook.com/next?access_token=FAKE",
            "cursors": {"after": cursor},
        }
    import json
    return json.dumps({"business_discovery": {"media": media}}).encode()


class FakeResponse:
    def __init__(self, status=200, body=None, usage=20, headers=None):
        self.status = status
        self.headers = {"X-App-Usage": '{"call_count":%d}' % usage} if headers is None else headers
        self._data = BytesIO(payload() if body is None else body)

    def read(self, size=-1):
        return self._data.read(size)

    def __enter__(self):
        return self

    def __exit__(self, *_):
        self._data.close()


class GuardedExecutorTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.db = Path(self.tmp.name) / "isolated.sqlite"
        self.tokens = environment()
        self.calls = []

    def executor(self, sender=None, approved=False, accounts_override=None):
        client = GraphHTTPClient(sender=sender, approved=approved)
        obj = GuardedThreeApiExecutor(
            self.db, accounts() if accounts_override is None else accounts_override,
            references(), self.tokens, http_client=client,
        )
        self.addCleanup(obj.close)
        obj.seed()
        return obj

    def saved_posts(self, executor):
        return executor._runner.coordinator.db.execute(
            "SELECT COUNT(*) FROM staging_posts").fetchone()[0]

    def test_disabled_by_default_blocks_lane_without_sender(self):
        executor = GuardedThreeApiExecutor(self.db, accounts(), references(), self.tokens)
        self.addCleanup(executor.close)
        executor.seed()
        result = executor.run_one("A", now=NOW)
        self.assertEqual(result, {
            "status": "blocked", "lane": "A", "reason": "transport_unavailable"})
        self.assertEqual(self.saved_posts(executor), 0)
        self.assertEqual(executor.summary()["jobs"]["leased"], 1)
        self.assertIsNone(executor._runner.coordinator.lease("A", now=NOW))

    def test_http_success_stages_single_page(self):
        def sender(request, *, timeout):
            self.calls.append(request)
            return FakeResponse()
        executor = self.executor(sender=sender, approved=True)
        with mock.patch("socket.create_connection",
                        side_effect=AssertionError("Forbidden network")):
            result = executor.run_one("A", now=NOW)
        self.assertEqual(result["status"], "staged")
        self.assertEqual(self.saved_posts(executor), 1)
        self.assertEqual(len(self.calls), 1)
        self.assertEqual(self.calls[0].get_header("Authorization"), "Bearer " + TOKEN_A)

    def test_transport_exception_halts_only_lane_a(self):
        def sender(req, *, timeout):
            self.calls.append(req)
            if req.get_header("Authorization") == "Bearer " + TOKEN_A:
                raise RuntimeError("secret " + TOKEN_A)
            return FakeResponse()
        executor = self.executor(sender=sender, approved=True)
        result = executor.run_one("A", now=NOW)
        self.assertEqual(result["status"], "blocked")
        self.assertEqual(result["reason"], "transport_unavailable")
        self.assertNotIn(TOKEN_A, str(result))
        self.assertEqual(executor.run_one("B", now=NOW)["status"], "staged")
        self.assertEqual(self.saved_posts(executor), 1)
        self.assertEqual(len(self.calls), 2)
        self.assertEqual(executor.summary()["jobs"]["leased"], 1)

    def test_never_retries_after_sender_exception(self):
        executor = self.executor(
            sender=lambda *_a, **_kw: self.calls.append(1) or
            (_ for _ in ()).throw(TimeoutError("synthetic timeout")),
            approved=True,
        )
        self.assertEqual(executor.run_one("A", now=NOW)["status"], "blocked")
        self.assertEqual(executor.run_one("A", now=NOW)["status"], "no_eligible_job")
        self.assertEqual(len(self.calls), 1)

    def test_manual_cooldown_reset_does_not_retry_unknown_job(self):
        failures = [True]
        def sender(*_a, **_kw):
            self.calls.append(1)
            if failures[0]:
                raise TimeoutError("synthetic transport down")
            return FakeResponse()
        executor = self.executor(sender=sender, approved=True)
        self.assertEqual(executor.run_one("A", now=NOW)["status"], "blocked")
        self.assertFalse(executor.reset_after_verified_cooldown(
            "A", now=NOW + timedelta(minutes=64)))
        self.assertEqual(executor.run_one(
            "A", now=NOW + timedelta(minutes=70))["status"], "no_eligible_job")
        self.assertEqual(len(self.calls), 1)
        self.assertTrue(executor.reset_after_verified_cooldown(
            "A", now=NOW + timedelta(minutes=70)))
        failures[0] = False
        retried = executor.run_one("A", now=NOW + timedelta(minutes=70))
        self.assertEqual(retried["status"], "staged")
        self.assertEqual(self.saved_posts(executor), 1)
        self.assertEqual(len(self.calls), 2)
        # The timed-out lease stays unresolved; success is a different job.
        self.assertEqual(executor.summary()["jobs"].get("leased", 0), 1)
        self.assertNotEqual(retried["account_ordinal"], 1)

    def test_http_429_halts_without_post_evidence(self):
        executor = self.executor(sender=lambda *_a, **_kw:
                                 FakeResponse(status=429, body=b'{"error":{"code":613}}'),
                                 approved=True)
        result = executor.run_one("A", now=NOW)
        self.assertEqual(result["reason"], "cooldown")
        self.assertEqual(self.saved_posts(executor), 0)
        self.assertEqual(executor._runner.coordinator.db.execute(
            "SELECT COUNT(*) FROM page_evidence").fetchone()[0], 0)

    def test_http_403_halts_without_post_evidence(self):
        executor = self.executor(sender=lambda *_a, **_kw:
                                 FakeResponse(status=403, body=b'{"error":{"code":190}}'),
                                 approved=True)
        result = executor.run_one("A", now=NOW)
        self.assertEqual(result["reason"], "credentials_or_permissions")
        self.assertEqual(self.saved_posts(executor), 0)

    def test_http_200_with_missing_usage_halts(self):
        executor = self.executor(sender=lambda *_a, **_kw: FakeResponse(headers={}),
                                 approved=True)
        result = executor.run_one("A", now=NOW)
        self.assertEqual(result["reason"], "usage_limit_or_missing")
        self.assertEqual(self.saved_posts(executor), 0)

    def test_85_percent_stops_before_commit(self):
        executor = self.executor(sender=lambda *_a, **_kw: FakeResponse(usage=85),
                                 approved=True)
        self.assertEqual(executor.run_one("A", now=NOW)["status"], "blocked")
        self.assertEqual(self.saved_posts(executor), 0)

    def test_invalid_media_response_causes_fail_closed_lane(self):
        executor = self.executor(sender=lambda *_a, **_kw:
                                 FakeResponse(body=b'{"business_discovery":{"media":{"data":[{}]}}}'),
                                 approved=True)
        result = executor.run_one("A", now=NOW)
        self.assertEqual(result["reason"], "execution_failure")
        self.assertEqual(self.saved_posts(executor), 0)
        self.assertIsNone(executor._runner.coordinator.lease("A", now=NOW))

    def test_oversized_body_halts_without_staging(self):
        executor = self.executor(sender=lambda *_a, **_kw:
                                 FakeResponse(body=b"x" * (512 * 1024 + 1)),
                                 approved=True)
        self.assertEqual(executor.run_one("A", now=NOW)["reason"], "transport_unavailable")
        self.assertEqual(self.saved_posts(executor), 0)

    def test_a_to_c_cursor_chain_uses_distinct_tokens(self):
        auth = []
        def sender(req, *, timeout):
            auth.append(req.get_header("Authorization"))
            if len(auth) == 1:
                return FakeResponse(body=payload("p1", cursor="cursor_after_1"))
            return FakeResponse(body=payload("p2"))
        executor = self.executor(sender=sender, approved=True)
        self.assertEqual(executor.run_one("A", now=NOW)["status"], "staged")
        self.assertEqual(executor.run_one("C", now=NOW)["status"], "staged")
        self.assertEqual(auth, ["Bearer " + TOKEN_A, "Bearer " + TOKEN_C])
        self.assertEqual(self.saved_posts(executor), 2)
        cursor = executor._runner.coordinator.db.execute(
            "SELECT requested_cursor FROM page_evidence WHERE page=2").fetchone()[0]
        self.assertEqual(cursor, "cursor_after_1")

    def test_database_and_status_never_contain_tokens(self):
        executor = self.executor(sender=lambda *_a, **_kw: FakeResponse(),
                                 approved=True)
        result = executor.run_one("A", now=NOW)
        dumped = "\n".join(executor._runner.coordinator.db.iterdump())
        for value in (TOKEN_A, TOKEN_B, TOKEN_C):
            self.assertNotIn(value, dumped)
            self.assertNotIn(value, str(result))
            self.assertNotIn(value, repr(executor))

    def test_invalid_caller_identity_fails_without_send(self):
        env = dict(self.tokens)
        env["TEST_A_ID"] = "invalid/id"
        self.tokens = env
        executor = self.executor(
            sender=lambda *_a, **_kw: self.calls.append(1) or FakeResponse(),
            approved=True,
        )
        result = executor.run_one("A", now=NOW)
        self.assertEqual(result["reason"], "execution_failure")
        self.assertEqual(self.calls, [])
        self.assertEqual(self.saved_posts(executor), 0)

    def test_refuses_production_database_basename(self):
        with self.assertRaises(ValueError):
            GuardedThreeApiExecutor(
                Path(self.tmp.name) / "collection.sqlite",
                accounts(), references(), self.tokens,
            )

    def test_invalid_lane_is_rejected(self):
        executor = self.executor()
        with self.assertRaises(GuardedExecutionError):
            executor.run_one("X", now=NOW)


if __name__ == "__main__":
    unittest.main()
