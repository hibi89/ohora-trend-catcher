"""Offline tests for Meta cursor edge validation."""
import importlib.util
from pathlib import Path
import unittest

p = Path(__file__).resolve().parents[1] / "raw_v2" / "three_api_paging.py"
spec = importlib.util.spec_from_file_location("three_api_paging", p)
mod = importlib.util.module_from_spec(spec)
spec.loader.exec_module(mod)


class PagingTests(unittest.TestCase):
    def test_valid_edge(self):
        result = mod.validate_page_edge(
            {"data": [{"id": "1"}], "paging": {
                "next": "https://graph.facebook.com/page",
                "cursors": {"after": "abc123"}}})
        self.assertEqual(result["next_cursor"], "abc123")
        self.assertFalse(result["terminal"])

    def test_terminal_page(self):
        result = mod.validate_page_edge({"data": [{"id": "2"}]})
        self.assertTrue(result["terminal"])
        self.assertIsNone(result["next_cursor"])

    def test_full_page_without_paging_is_ambiguous(self):
        result = mod.validate_page_edge({"data": [{"id": str(i)} for i in range(4)]})
        self.assertFalse(result["terminal"])
        self.assertIsNone(result["next_cursor"])
        self.assertEqual(result["pagination_status"], "pagination_ambiguous")
        self.assertEqual(result["records"], 4)

    def test_after_cursor_without_next_is_preserved_as_candidate(self):
        result = mod.validate_page_edge({
            "data": [{"id": str(i)} for i in range(4)],
            "paging": {"cursors": {"after": "abc123"}},
        })
        self.assertFalse(result["terminal"])
        self.assertEqual(result["next_cursor"], "abc123")
        self.assertEqual(result["pagination_status"], "cursor_candidate")

    def test_short_page_without_cursor_is_terminal_verified(self):
        result = mod.validate_page_edge({"data": [{"id": str(i)} for i in range(3)]})
        self.assertTrue(result["terminal"])
        self.assertEqual(result["pagination_status"], "terminal_verified")

    def test_empty_page_with_after_fails_closed(self):
        with self.assertRaisesRegex(ValueError, "Empty page"):
            mod.validate_page_edge({"data": [], "paging": {"cursors": {"after": "new_cursor"}}},
                                   requested_cursor="old_cursor")

    def test_full_page_next_without_after_fails_closed(self):
        with self.assertRaises(ValueError):
            mod.validate_page_edge({
                "data": [{"id": str(i)} for i in range(4)],
                "paging": {"next": "https://graph.facebook.com/page"},
            })

    def test_next_without_cursor_fails(self):
        with self.assertRaises(ValueError):
            mod.validate_page_edge({"data": [], "paging": {"next": "https://example.org"}})

    def test_cycle_fails(self):
        with self.assertRaises(ValueError):
            mod.validate_page_edge(
                {"data": [], "paging": {"next": "https://example.org",
                 "cursors": {"after": "abc"}}},
                requested_cursor="abc")

    def test_prior_cursor_reuse_fails(self):
        with self.assertRaises(ValueError):
            mod.validate_page_edge(
                {"data": [], "paging": {"next": "https://example.org",
                 "cursors": {"after": "abc"}}}, prior_cursors=("abc",))

    def test_rejects_cursor_cycles_across_all_previous_pages(self):
        for previous in ("first_cursor", "second_cursor", "third_cursor"):
            with self.subTest(previous=previous), self.assertRaisesRegex(
                    ValueError, "Cursor cycle detected"):
                mod.validate_page_edge({
                    "data": [{"id": "1"}],
                    "paging": {"next": "https://example.org/next",
                               "cursors": {"after": previous}}},
                    requested_cursor="current_cursor",
                    prior_cursors=("first_cursor", "second_cursor",
                                   "third_cursor"))

    def test_rejects_duplicate_consumed_cursors(self):
        with self.assertRaisesRegex(ValueError, "Repeated prior cursor"):
            mod.validate_page_edge({"data": []},
                                   prior_cursors=("cursor_a", "cursor_a"))

    def test_rejects_malformed_response_cursor_without_next(self):
        for invalid in ("", "bad cursor", "https://example.org", 17):
            with self.subTest(invalid=invalid), self.assertRaises(ValueError):
                mod.validate_page_edge({
                    "data": [],
                    "paging": {"cursors": {"after": invalid}}})

    def test_rejects_malformed_prior_cursor_even_for_terminal_page(self):
        for invalid in ("", "bad cursor", 17, None):
            with self.subTest(invalid=invalid), self.assertRaises(ValueError):
                mod.validate_page_edge({"data": []},
                                       prior_cursors=(invalid,))

    def test_empty_page_with_stale_after_is_rejected(self):
        with self.assertRaises(ValueError):
            mod.validate_page_edge({
            "data": [],
            "paging": {"cursors": {"after": "stale_cursor"}}},
            requested_cursor="current_cursor")

    def test_invalid_response_fails(self):
        with self.assertRaises(ValueError):
            mod.validate_page_edge({"data": "bad"})


if __name__ == "__main__":
    unittest.main()
