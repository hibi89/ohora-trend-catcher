"""P1 synthetic regression: receive-first evidence, quota/NULL/partial recovery.

No Meta call, no real credentials, no production SQLite paths.
"""
from dataclasses import replace
from datetime import datetime, timezone
from pathlib import Path
import copy
import sqlite3
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "raw_v2"))
sys.path.insert(0, str(ROOT / "tests"))

from test_three_api_contract_executor import (
    FakeTransport, make_request, make_response, SCHEMA,
)
from three_api_capture import CaptureJournal
from three_api_contract_executor import ContractExecutionError, ThreeApiContractExecutor
from three_api_raw_writer import ContractRawWriter
from three_api_response_evidence import prepare_safe_response
from three_api_runtime_contract import digest_sha256

class P1ResponseEvidenceTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.request = make_request()
        self.journal = CaptureJournal(str(Path(self.tmp.name) / "capture.sqlite"))
        self.raw = sqlite3.connect(":memory:", isolation_level=None)
        self.raw.executescript(SCHEMA)
        self.raw.execute("INSERT INTO accounts VALUES(?)", ("KR_001",))

    def tearDown(self):
        self.journal.close()
        self.raw.close()
        self.tmp.cleanup()

    def run_request(self, response):
        transport = FakeTransport(response)
        executor = ThreeApiContractExecutor(transport, self.journal, ContractRawWriter(self.raw))
        return executor, transport

    def test_sanitizer_keeps_safe_unknown_fields_but_not_secrets_or_next_url(self):
        safe, paths = prepare_safe_response({
            "business_discovery": {
                "id": "123", "extra_future_field": {"debug_safe": True},
                "access_token": "NEVER_STORE_THIS",
                "media": {"data": [{"id": "55", "caption": "nail",
                                    "client_secret": "NEVER_STORE_ME",
                                    "media_url": "https://cdn.test/clip.mp4?sig=safe"}],
                          "paging": {"next": "https://graph.test/?access_token=BAD",
                                     "cursors": {"after": "opaque_abc"}}}
            }
        })
        self.assertTrue(safe["business_discovery"]["extra_future_field"]["debug_safe"])
        self.assertEqual(safe["business_discovery"]["media"]["paging"]["next"],
                         "sanitized-next-present")
        self.assertEqual(safe["business_discovery"]["media"]["paging"]["cursors"]["after"],
                         "opaque_abc")
        self.assertIn("business_discovery.access_token", paths)
        self.assertNotIn("NEVER_STORE", str(safe))
        self.assertNotIn("BAD", str(safe))
        self.assertIn("sig=safe", str(safe))

    def test_missing_usage_saves_response_before_rejection(self):
        response = make_response(self.request, usage={})
        exe, transport = self.run_request(response)
        with self.assertRaises(ContractExecutionError):
            exe.execute(self.request)
        e = self.journal.get_response_evidence(self.request.request_id)
        self.assertEqual(e["classification"], "safe")
        self.assertEqual(e["safe_usage"], {})
        self.assertEqual(e["safe_body"]["business_discovery"]["media"]["data"][0]["id"],
                         "1234567890")
        self.assertEqual(transport.calls, 1)
        self.assertEqual(self.raw.execute("SELECT COUNT(*) FROM posts").fetchone()[0], 0)

    def test_threshold_response_saved_and_projected_without_retry(self):
        response = make_response(self.request, usage={
            "x-app-usage": '{"call_count":85}'})
        exe, transport = self.run_request(response)
        result = exe.execute(self.request)
        self.assertEqual(result.status, "committed")
        self.assertEqual(transport.calls, 1)
        e = self.journal.get_response_evidence(self.request.request_id)
        self.assertEqual(e["safe_usage"]["max_usage_percent"], 85)
        self.assertEqual(self.raw.execute("SELECT COUNT(*) FROM post_observations").fetchone()[0], 1)
        exe.execute(self.request)
        self.assertEqual(transport.calls, 1)

    def test_explicit_null_and_zero_are_distinct(self):
        response = make_response(self.request)
        body = copy.deepcopy(response.payload)
        body["business_discovery"]["media"]["data"][0]["like_count"] = None
        body["business_discovery"]["media"]["data"][0]["comments_count"] = 0
        response = replace(response, payload=body)
        exe, transport = self.run_request(response)
        exe.execute(self.request)
        capture = self.journal.get_capture_for_request(self.request.request_id)
        self.assertEqual(capture.post_field_status["1234567890"]["like_count"], "null")
        self.assertEqual(capture.post_field_status["1234567890"]["comments_count"], "returned")
        row = self.raw.execute(
            "SELECT like_count,comments_count,like_present,comments_present FROM post_observations"
        ).fetchone()
        self.assertEqual(row, (None, 0, 1, 1))

    def test_partial_carousel_keeps_parent_and_video_url_without_completion_claim(self):
        response = make_response(self.request)
        body = copy.deepcopy(response.payload)
        body["business_discovery"]["media"]["data"] = [{
            "id": "1234567890", "media_type": "CAROUSEL_ALBUM",
            "children": {
                "data": [{"id": "55555", "media_type": "VIDEO",
                          "media_url": "https://cdn.test/video.mp4?sig=ok"}],
                "paging": {"next": "https://graph.test/?access_token=DO_NOT_STORE",
                           "cursors": {"after": "nextchild"}},
            },
        }]
        response = replace(response, payload=body)
        exe, _ = self.run_request(response)
        exe.execute(self.request)
        record = self.journal.get_capture_for_request(self.request.request_id)
        self.assertEqual(record.carousel_completeness["1234567890"], "partial")
        self.assertEqual(record.normalized_posts[0]["children"][0]["id"], "55555")
        self.assertEqual(self.raw.execute("SELECT coverage FROM carousel_status").fetchone()[0],
                         "partial")
        self.assertEqual(self.raw.execute("SELECT media_url FROM post_media").fetchone()[0],
                         "https://cdn.test/video.mp4?sig=ok")
        safe = self.journal.get_response_evidence(self.request.request_id)
        self.assertNotIn("DO_NOT_STORE", str(safe))

    def test_malformed_paging_still_preserves_safe_response_without_raw_commit(self):
        response = make_response(self.request)
        body = copy.deepcopy(response.payload)
        body["business_discovery"]["media"]["paging"] = {
            "next": "https://graph.test/?access_token=SECRET_NOT_ALLOWED"
        }
        response = replace(response, payload=body)
        exe, _ = self.run_request(response)
        with self.assertRaises(ContractExecutionError):
            exe.execute(self.request)
        e = self.journal.get_response_evidence(self.request.request_id)
        self.assertEqual(e["classification"], "safe")
        self.assertEqual(e["safe_body"]["business_discovery"]["media"]["paging"]["next"],
                         "sanitized-next-present")
        self.assertNotIn("SECRET_NOT_ALLOWED", str(e))
        self.assertEqual(self.raw.execute("SELECT COUNT(*) FROM posts").fetchone()[0], 0)

    def test_unsafe_value_records_metadata_only_without_credential(self):
        response = make_response(self.request)
        body = copy.deepcopy(response.payload)
        body["business_discovery"]["media"]["data"][0]["caption"] = (
            "Authorization: Bearer NEVER_STORE_THIS_VALUE")
        response = replace(response, payload=body)
        exe, _ = self.run_request(response)
        with self.assertRaises(ContractExecutionError):
            exe.execute(self.request)
        e = self.journal.get_response_evidence(self.request.request_id)
        self.assertEqual(e["classification"], "evidence_unavailable")
        self.assertIsNone(e["safe_body"])
        raw_bytes = Path(self.journal.path).read_bytes()
        self.assertNotIn(b"NEVER_STORE_THIS_VALUE", raw_bytes)
        self.assertEqual(self.raw.execute("SELECT COUNT(*) FROM posts").fetchone()[0], 0)

    def test_opt_in_account_fields_are_observed_and_survive_restart(self):
        from three_api_request_spec import build_request_spec
        spec = build_request_spec("A", "nails_1", include_account_metrics=True)
        request = replace(self.request, request_spec=spec,
            request_spec_digest=digest_sha256({
                "api_version": "v26.0", "target_ig_user_id": self.request.target_ig_user_id,
                "spec": spec,
            }))
        response = make_response(request)
        body = copy.deepcopy(response.payload)
        body["business_discovery"]["followers_count"] = 1500
        body["business_discovery"]["media_count"] = 123
        response = replace(response, payload=body)
        executor, _ = self.run_request(response)
        executor.execute(request)
        record = self.journal.get_capture_for_request(request.request_id)
        self.assertEqual(record.field_status["followers_count"], "returned")
        self.assertEqual(record.field_status["media_count"], "returned")
        self.assertEqual(record.account_observation["followers_count"], 1500)
        self.journal.close()
        self.journal = CaptureJournal(str(Path(self.tmp.name) / "capture.sqlite"))
        restored = self.journal.get_capture_for_request(request.request_id)
        self.assertEqual(restored.request.request_spec.account_fields,
                         ("id", "username", "followers_count", "media_count"))
        self.assertEqual(restored.account_observation["followers_count"], 1500)

    def test_unrequested_account_metrics_are_evidence_only(self):
        response = make_response(self.request)
        body = copy.deepcopy(response.payload)
        body["business_discovery"]["followers_count"] = 900
        response = replace(response, payload=body)
        executor, _ = self.run_request(response)
        executor.execute(self.request)
        record = self.journal.get_capture_for_request(self.request.request_id)
        self.assertEqual(record.field_status["followers_count"], "not_tested")
        self.assertNotIn("followers_count", record.account_observation)
        evidence = self.journal.get_response_evidence(self.request.request_id)
        self.assertEqual(evidence["safe_body"]["business_discovery"]["followers_count"], 900)

    def test_new_journal_after_restart_retains_observation_without_http(self):
        response = make_response(self.request, usage={})
        exe, transport = self.run_request(response)
        with self.assertRaises(ContractExecutionError):
            exe.execute(self.request)
        self.journal.close()
        self.journal = CaptureJournal(str(Path(self.tmp.name) / "capture.sqlite"))
        e = self.journal.get_response_evidence(self.request.request_id)
        self.assertEqual(e["classification"], "safe")
        self.assertEqual(e["http_status"], 200)
        self.assertEqual(transport.calls, 1)

if __name__ == "__main__":
    unittest.main()
