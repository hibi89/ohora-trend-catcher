"""Offline-only bearer header / runtime env boundary regression tests.

All identifiers and token strings are synthetic and are never passed to Meta.
"""
from datetime import datetime, timezone
from pathlib import Path
from unittest import mock
import sqlite3
import sys
import tempfile
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "raw_v2"))
from three_api_credentials import CredentialReference, make_references
from three_api_offline_runner import OfflineThreeApiRunner
from three_api_runtime_boundary import OfflineCredentialedFetcher, OfflineRuntimeError


def references():
    return make_references({
        lane: {
            "token_env_name": f"OHORA_TEST_{lane}_TOKEN",
            "ig_user_id_env_name": f"OHORA_TEST_{lane}_IG_ID",
        }
        for lane in "ABC"
    })


def synthetic_environment():
    env = {}
    for lane in "ABC":
        env[f"OHORA_TEST_{lane}_TOKEN"] = "synthetic_" + lane + "_not_a_real_token"
        env[f"OHORA_TEST_{lane}_IG_ID"] = "1784000000000000" + str("ABC".index(lane))
    return env


def accounts():
    return [
        {"ordinal": i, "key": f"KR_{i:03}", "username": f"nails_{i}"}
        for i in range(1, 295)
    ]


def envelope(post_id, usage=30, next_cursor=None, status=200):
    media = {"data": [{"id": post_id, "caption": "synthetic nail design"}]}
    if next_cursor:
        media["paging"] = {
            "next": "https://graph.facebook.com/next?access_token=FAKE",
            "cursors": {"after": next_cursor},
        }
    return {
        "status": status,
        "headers": {"X-App-Usage": '{"call_count":%d}' % usage},
        "body": {"business_discovery": {"media": media}},
    }


class RuntimeBoundaryTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.env = synthetic_environment()
        self.refs = references()
        self.now = datetime(2026, 10, 19, tzinfo=timezone.utc)

    def make_runner(self, fixtures, *, environment=None, version="v26.0"):
        fetcher = OfflineCredentialedFetcher(
            self.refs, self.env if environment is None else environment,
            fixtures, version=version,
        )
        runner = OfflineThreeApiRunner(
            Path(self.tmp.name) / "staging.sqlite",
            accounts(), fetcher, envelope_fixture=True,
        )
        self.addCleanup(runner.close)
        runner.seed()
        return runner, fetcher

    def test_full_a_to_c_cursor_chain_with_no_network(self):
        fixture = {
            ("A", "nails_1", None): envelope("p1", next_cursor="next_cursor_1"),
            ("C", "nails_1", "next_cursor_1"): envelope("p2"),
        }
        runner, _ = self.make_runner(fixture)
        with mock.patch("socket.create_connection", side_effect=AssertionError("Network prohibited")):
            first = runner.run_one("A", now=self.now)
            second = runner.run_one("C", now=self.now)
        self.assertEqual((first["status"], second["status"]), ("staged", "staged"))
        self.assertEqual(runner.coordinator.db.execute(
            "SELECT COUNT(*) FROM staging_posts").fetchone()[0], 2)
        self.assertEqual(runner.coordinator.db.execute(
            "SELECT requested_cursor FROM page_evidence WHERE page=2").fetchone()[0],
            "next_cursor_1")

    def test_token_absent_from_database_result_and_repr(self):
        runner, fetcher = self.make_runner({
            ("A", "nails_1", None): envelope("p1"),
        })
        result = runner.run_one("A", now=self.now)
        dump = "\n".join(runner.coordinator.db.iterdump())
        for value in self.env.values():
            self.assertNotIn(value, dump)
            self.assertNotIn(value, repr(fetcher))
            self.assertNotIn(value, str(result))
        self.assertNotIn("Authorization", dump)

    def test_lane_b_uses_its_own_runtime_identity(self):
        runner, _ = self.make_runner({
            ("B", "nails_151", None): envelope("p151"),
        })
        result = runner.run_one("B", now=self.now)
        self.assertEqual(result["status"], "staged")
        self.assertEqual(runner.coordinator.db.execute(
            "SELECT account_ordinal FROM staging_posts").fetchone()[0], 151)

    def test_missing_token_fails_closed(self):
        env = dict(self.env)
        del env["OHORA_TEST_A_TOKEN"]
        runner, _ = self.make_runner({
            ("A", "nails_1", None): envelope("p1"),
        }, environment=env)
        with self.assertRaisesRegex(OfflineRuntimeError, "credentials unavailable"):
            runner.run_one("A", now=self.now)
        self.assertEqual(runner.coordinator.db.execute(
            "SELECT COUNT(*) FROM staging_posts").fetchone()[0], 0)

    def test_token_with_newline_rejected_without_leak(self):
        env = dict(self.env)
        env["OHORA_TEST_A_TOKEN"] = "synthetic_SECRET\nEXTRA"
        runner, _ = self.make_runner({}, environment=env)
        with self.assertRaises(OfflineRuntimeError) as error:
            runner.run_one("A", now=self.now)
        self.assertNotIn("synthetic_SECRET", str(error.exception))

    def test_invalid_runtime_identity_fails_closed(self):
        env = dict(self.env)
        env["OHORA_TEST_A_IG_ID"] = "id/attack"
        runner, _ = self.make_runner({}, environment=env)
        with self.assertRaises(OfflineRuntimeError):
            runner.run_one("A", now=self.now)

    def test_unsupported_graph_version_fails_closed(self):
        runner, _ = self.make_runner({}, version="https://attacker.test/")
        with self.assertRaisesRegex(OfflineRuntimeError, "runtime request configuration"):
            runner.run_one("A", now=self.now)

    def test_missing_fixture_has_safe_error(self):
        runner, _ = self.make_runner({})
        with self.assertRaises(OfflineRuntimeError) as error:
            runner.run_one("A", now=self.now)
        self.assertEqual(str(error.exception), "Synthetic transport failed")
        self.assertEqual(runner.coordinator.db.execute(
            "SELECT COUNT(*) FROM staging_posts").fetchone()[0], 0)

    def test_quota_threshold_blocks_post_commit(self):
        runner, _ = self.make_runner({
            ("A", "nails_1", None): envelope("p1", usage=85),
        })
        result = runner.run_one("A", now=self.now)
        self.assertEqual(result["status"], "blocked")
        self.assertIsNone(runner.coordinator.lease("A", now=self.now))
        self.assertEqual(runner.coordinator.db.execute(
            "SELECT COUNT(*) FROM staging_posts").fetchone()[0], 0)

    def test_malformed_usage_blocks(self):
        response = envelope("p1")
        response["headers"] = {}
        runner, _ = self.make_runner({("A", "nails_1", None): response})
        result = runner.run_one("A", now=self.now)
        self.assertEqual(result["status"], "blocked")

    def test_no_ambient_credentials_loaded_at_constructor(self):
        env = {}
        runner, _ = self.make_runner({
            ("A", "nails_1", None): envelope("p1"),
        }, environment=env)
        env.update(self.env)
        result = runner.run_one("A", now=self.now)
        self.assertEqual(result["status"], "staged")

    def test_rejects_ref_with_wrong_lane(self):
        refs = dict(self.refs)
        refs["A"] = CredentialReference(
            "B", "OHORA_TEST_A_TOKEN", "OHORA_TEST_A_IG_ID")
        with self.assertRaises(OfflineRuntimeError):
            OfflineCredentialedFetcher(refs, self.env, {})

    def test_rejects_reused_env_names(self):
        refs = dict(self.refs)
        refs["B"] = CredentialReference(
            "B", "OHORA_TEST_A_TOKEN", "OHORA_TEST_B_IG_ID")
        with self.assertRaises(OfflineRuntimeError):
            OfflineCredentialedFetcher(refs, self.env, {})

    def test_rejects_unapproved_live_intent(self):
        fetcher = OfflineCredentialedFetcher(self.refs, self.env, {})
        unsafe = {
            "lane": "A", "username": "nails_1", "post_limit": 4,
            "logical_page": 1, "meta_request_enabled": True,
            "mode": "OFFLINE_INTENT_NO_API_CALLS",
        }
        with self.assertRaisesRegex(OfflineRuntimeError, "Unsafe offline"):
            fetcher(unsafe, None)

    def test_rejects_fake_cursor_and_username(self):
        fetcher = OfflineCredentialedFetcher(self.refs, self.env, {})
        safe = {
            "lane": "A", "username": "nails_1", "post_limit": 4,
            "logical_page": 2, "meta_request_enabled": False,
            "mode": "OFFLINE_INTENT_NO_API_CALLS",
        }
        with self.assertRaises(OfflineRuntimeError):
            fetcher(safe, "abc&access_token=secret")
        safe["username"] = "nails{injected}"
        with self.assertRaises(OfflineRuntimeError):
            fetcher(safe, "cursor2")

    def test_head_page_rejects_cursor(self):
        fetcher = OfflineCredentialedFetcher(self.refs, self.env, {})
        intent = {
            "lane": "A", "username": "nails_1", "post_limit": 4,
            "logical_page": 1, "meta_request_enabled": False,
            "mode": "OFFLINE_INTENT_NO_API_CALLS",
        }
        with self.assertRaises(OfflineRuntimeError):
            fetcher(intent, "unapproved")


if __name__ == "__main__":
    unittest.main()
