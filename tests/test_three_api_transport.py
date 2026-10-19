"""Offline-only quota parser tests."""
import unittest
from pathlib import Path
import sys
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "raw_v2"))
from three_api_transport import parse_meta_usage, extract_next_cursor

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

if __name__ == "__main__":
    unittest.main()
