"""Offline credential-reference contract tests."""
import unittest
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "raw_v2"))
from three_api_credentials import make_references, validate_identity_value


def refs():
    return {lane: {"token_env_name": f"OHORA_META_{lane}_TOKEN",
                   "ig_user_id_env_name": f"OHORA_META_{lane}_IG_USER_ID"}
            for lane in "ABC"}


class ReferenceTests(unittest.TestCase):
    def test_three_independent_names(self):
        result = make_references(refs())
        self.assertEqual(set(result), {"A", "B", "C"})
        self.assertEqual(result["A"].token_env_name, "OHORA_META_A_TOKEN")

    def test_summary_never_contains_references_or_tokens(self):
        result = make_references(refs())
        self.assertNotIn("OHORA_META_A_TOKEN", str(result["A"].public_summary()))
        self.assertFalse(result["A"].public_summary()["token_configured"])

    def test_refuse_shared_token_reference(self):
        x = refs()
        x["B"]["token_env_name"] = x["A"]["token_env_name"]
        with self.assertRaises(ValueError):
            make_references(x)

    def test_refuse_incomplete_mapping(self):
        with self.assertRaises(ValueError):
            make_references({"A": refs()["A"]})

    def test_refuse_unsafe_env_names(self):
        x = refs()
        x["A"]["token_env_name"] = "OHORA_TOKEN; rm -rf /"
        with self.assertRaises(ValueError):
            make_references(x)

    def test_no_credential_values_allowed_in_mapping(self):
        x = refs()
        x["A"]["token"] = "SECRET"
        with self.assertRaises(ValueError):
            make_references(x)

    def test_id_format(self):
        self.assertTrue(validate_identity_value("123456789"))
        for value in ("", "123/abc", "abc", "1" * 33, None):
            with self.subTest(value=value):
                self.assertFalse(validate_identity_value(value))


if __name__ == "__main__":
    unittest.main()
