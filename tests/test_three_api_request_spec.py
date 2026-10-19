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

    def test_fixed_page_size_and_lane(self):
        for limit in (0, 5, True):
            with self.subTest(limit=limit), self.assertRaises(ValueError):
                build_request_spec("A", "nails", limit=limit)
        with self.assertRaises(ValueError):
            build_request_spec("D", "nails")


if __name__ == "__main__":
    unittest.main()
