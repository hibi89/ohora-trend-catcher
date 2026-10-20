"""No-network request-spec contract tests."""
from pathlib import Path
import sys
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "raw_v2"))
from three_api_request_spec import build_request_spec, business_discovery_field


class RequestSpecTests(unittest.TestCase):
    def test_first_page(self):
        spec = build_request_spec("A", "nail.artist")
        field = business_discovery_field(spec)
        self.assertIn("business_discovery.username(nail.artist)", field)
        self.assertIn("media.limit(4)", field)
        self.assertNotIn("access_token", field)
        self.assertIsNone(spec.after)

    def test_owner_required_view_count_in_all_new_collection_pages(self):
        for lane in ("A", "B", "C"):
            spec = build_request_spec(lane, "nail.artist", limit=4)
            fields = business_discovery_field(spec)
            self.assertIn("media.limit(4)", fields)
            self.assertIn("media_product_type", spec.media_fields.split(","))
            self.assertIn("view_count", spec.media_fields.split(","))
            self.assertIn("view_count", fields)
            self.assertNotIn("access_token", fields)

    def test_next_page(self):
        spec = build_request_spec("C", "nails_01", after="abc_123=")
        self.assertIn("media.after(abc_123=).limit(4)", business_discovery_field(spec))
        self.assertTrue(spec.as_safe_dict()["after_present"])
        self.assertNotIn("abc_123=", str(spec.as_safe_dict()))

    def test_invalid_usernames(self):
        for name in ("", "hello;evil", "hello/evil", "a" * 31, "name{inject}"):
            with self.subTest(name=name), self.assertRaises(ValueError):
                build_request_spec("A", name)

    def test_rejects_untrusted_cursors(self):
        for cursor in ("https://example.com", "x&access_token=abc", "a b", ""):
            with self.subTest(cursor=cursor), self.assertRaises(ValueError):
                build_request_spec("A", "nails", after=cursor)

    def test_explicit_account_metrics_opt_in_preserves_default_contract(self):
        from three_api_runtime_contract import canonical_bytes
        from three_api_http_preflight import prepare_graph_request
        from three_api_credentials import make_references
        base = build_request_spec("A", "nails")
        profile = build_request_spec("A", "nails", include_account_metrics=True)
        self.assertEqual(base.limit, profile.limit)
        self.assertEqual(base.limit, 4)
        self.assertNotIn("followers_count", business_discovery_field(base))
        self.assertIn("followers_count,media_count", business_discovery_field(profile))
        self.assertNotIn(b'"account_fields"', canonical_bytes(base))
        self.assertIn(b'"account_fields"', canonical_bytes(profile))
        refs = make_references({lane: {
            "token_env_name": "TEST_%s_TOKEN" % lane,
            "ig_user_id_env_name": "TEST_%s_IG_USER_ID" % lane,
        } for lane in "ABC"})
        prepared = prepare_graph_request(profile, refs["A"], "17840000000000000")
        self.assertTrue(prepared.include_account_metrics)
        self.assertIn("followers_count", prepared.fields)
        with self.assertRaises(ValueError):
            build_request_spec("A", "nails", include_account_metrics=1)

    def test_fixed_page_size_and_lane(self):
        for limit in (0, 5, True):
            with self.subTest(limit=limit), self.assertRaises(ValueError):
                build_request_spec("A", "nails", limit=limit)
        with self.assertRaises(ValueError):
            build_request_spec("D", "nails")


if __name__ == "__main__":
    unittest.main()
