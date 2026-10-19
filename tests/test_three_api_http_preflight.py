"""Network-free preflight and fixture transport checks."""
import unittest
from urllib.parse import parse_qs

from pathlib import Path
import sys
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "raw_v2"))

from three_api_credentials import make_references, CredentialReference
from three_api_request_spec import build_request_spec, RequestSpec
from three_api_http_preflight import prepare_graph_request, OfflineFixtureTransport


def references():
    return make_references({lane: {
        "token_env_name": f"OHORA_META_{lane}_TOKEN",
        "ig_user_id_env_name": f"OHORA_META_{lane}_IG_USER_ID"}
        for lane in "ABC"})


class HTTPPreflightTests(unittest.TestCase):
    def test_graph_v26_unsigned_shape(self):
        req = prepare_graph_request(build_request_spec("A", "nail.artist"),
                                    references()["A"], "17840000000000000")
        parts = req.unsigned_parts()
        self.assertEqual(parts["method"], "GET")
        self.assertEqual(parts["url"], "https://graph.facebook.com/v26.0/17840000000000000")
        self.assertEqual(parts["headers"], {})
        self.assertIn("media.limit(4)", parse_qs(parts["query_string"])["fields"][0])

    def test_preflight_does_not_surface_id_or_cursor_in_repr(self):
        req = prepare_graph_request(build_request_spec("C", "artist_nails", after="opaqueCURSOR987"),
                                    references()["C"], "17840000000000000")
        self.assertNotIn("opaqueCURSOR987", repr(req))
        self.assertNotIn("17840000000000000", repr(req))
        self.assertNotIn("OHORA_META_C_TOKEN", repr(req))
        self.assertTrue(req.safe_summary()["after_present"])

    def test_no_token_in_unsigned_query_or_headers(self):
        req = prepare_graph_request(build_request_spec("B", "nails"),
                                    references()["B"], "123")
        parts = req.unsigned_parts()
        self.assertNotIn("access_token", parts["query_string"])
        self.assertNotIn("OHORA_META_B_TOKEN", str(parts))

    def test_lane_mismatch_rejected(self):
        with self.assertRaises(ValueError):
            prepare_graph_request(build_request_spec("A", "nails"), references()["B"], "123")

    def test_forged_credential_reference_rejected(self):
        fake = CredentialReference("A", "TOKEN VALUE", "IG_USER_ID")
        with self.assertRaises(ValueError):
            prepare_graph_request(build_request_spec("A", "nails"), fake, "123")

    def test_identity_and_version_rejected(self):
        for caller in ("", "abc", "1/2", "-3"):
            with self.subTest(caller=caller), self.assertRaises(ValueError):
                prepare_graph_request(build_request_spec("A", "nails"), references()["A"], caller)
        for version in ("http://other", "v26.0/anything", "v-1", "26.0"):
            with self.subTest(version=version), self.assertRaises(ValueError):
                prepare_graph_request(build_request_spec("A", "nails"), references()["A"], "123", version=version)

    def test_fixture_transport_returns_copy(self):
        key = ("A", "nails", None)
        fixtures = {key: {"status": 200, "body": {"data": [{"id": "post1"}]}}}
        transport = OfflineFixtureTransport(fixtures)
        req = prepare_graph_request(build_request_spec("A", "nails"), references()["A"], "123")
        result = transport.fetch(req)
        result["body"]["data"][0]["id"] = "mutated"
        self.assertEqual(transport.fetch(req)["body"]["data"][0]["id"], "post1")

    def test_transport_rejects_missing_fixture(self):
        transport = OfflineFixtureTransport({})
        req = prepare_graph_request(build_request_spec("A", "nails"), references()["A"], "123")
        with self.assertRaisesRegex(KeyError, "Missing offline fixture"):
            transport.fetch(req)

    def test_directly_forged_spec_rejected(self):
        good = build_request_spec("A", "nails")
        forged = RequestSpec(lane="A", username="nails}{access_token=evil",
                             limit=4, media_fields=good.media_fields, after=None)
        with self.assertRaises(ValueError):
            prepare_graph_request(forged, references()["A"], "123")

    def test_cursor_fixture_key_bound_to_prepared_request(self):
        req = prepare_graph_request(build_request_spec("A", "nails", after="cursor2"),
                                    references()["A"], "123")
        transport = OfflineFixtureTransport({("A", "nails", "cursor2"): {"status": 200}})
        self.assertEqual(transport.fetch(req)["status"], 200)

    def test_cursor_field_encoding(self):
        req = prepare_graph_request(build_request_spec("A", "nails", after="abc_123="),
                                    references()["A"], "123")
        query = parse_qs(req.unsigned_parts()["query_string"])
        self.assertIn("after(abc_123=)", query["fields"][0])
        self.assertNotIn("&access_token", req.unsigned_parts()["query_string"])


if __name__ == "__main__":
    unittest.main()
