import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from raw_v2.three_api_credential_diagnostic import diagnose_credentials


class CredentialDiagnosticTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.directory = Path(self.temp.name)

    def tearDown(self):
        self.temp.cleanup()

    def write_raw(self, lane, token, caller, version=None):
        values = [
            f"META_ACCESS_TOKEN={token}",
            f"META_IG_USER_ID={caller}",
        ]
        if version is not None:
            values.append(f"META_GRAPH_VERSION={version}")
        (self.directory / f".env.api_{lane.lower()}").write_text(
            "\n".join(values), encoding="utf-8",
        )

    def environment(self, token_b="token-B-fixture", caller_b="101",
                    token_c="token-C-fixture", caller_c="202",
                    version=None):
        env = {
            "P6_META_TOKEN_B": token_b,
            "P6_META_IG_ID_B": caller_b,
            "P6_META_TOKEN_C": token_c,
            "P6_META_IG_ID_C": caller_c,
        }
        if version is not None:
            env["P6_META_GRAPH_VERSION"] = version
        return env

    def test_matching_b_and_c_config_is_reported_without_values(self):
        self.write_raw("B", "token-B-fixture", "101")
        self.write_raw("C", "token-C-fixture", "202")
        report = diagnose_credentials(
            environment=self.environment(), raw_env_dir=self.directory,
        )
        self.assertEqual("match", report["lanes"]["B"]["configuration_comparison"])
        self.assertEqual("match", report["lanes"]["C"]["configuration_comparison"])
        self.assertFalse(report["live_meta_called"])
        self.assertFalse(report["secret_values_emitted"])
        self.assertEqual("not_checked_offline",
                         report["lanes"]["B"]["active_token_or_permissions"])
        rendered = json.dumps(report)
        for secret in ("token-B-fixture", "token-C-fixture", "101", "202"):
            self.assertNotIn(secret, rendered)

    def test_token_or_caller_mismatch_is_detected_but_values_stay_private(self):
        self.write_raw("B", "other-token-fixture", "999")
        self.write_raw("C", "token-C-fixture", "202")
        report = diagnose_credentials(
            environment=self.environment(), raw_env_dir=self.directory,
        )
        b = report["lanes"]["B"]
        self.assertEqual("mismatch", b["configuration_comparison"])
        self.assertFalse(b["token_exact_match"])
        self.assertFalse(b["caller_id_exact_match"])
        rendered = json.dumps(report)
        for secret in ("token-B-fixture", "other-token-fixture", "101", "999"):
            self.assertNotIn(secret, rendered)

    def test_missing_file_or_p6_environment_fails_closed_as_unavailable(self):
        report = diagnose_credentials(environment={}, raw_env_dir=self.directory)
        self.assertEqual("unavailable",
                         report["lanes"]["B"]["configuration_comparison"])
        self.assertEqual("missing", report["lanes"]["B"]["raw_file_status"])
        self.assertIsNone(report["lanes"]["B"]["token_exact_match"])

    def test_invalid_token_and_caller_shapes_are_not_reported_as_a_match(self):
        self.write_raw("B", "raw token with spaces", "caller-x")
        report = diagnose_credentials(
            environment=self.environment(
                token_b="p6 token with spaces", caller_b="caller-y",
            ),
            raw_env_dir=self.directory,
        )
        b = report["lanes"]["B"]
        self.assertEqual("invalid_format", b["configuration_comparison"])
        self.assertFalse(b["p6_token_shape_ok"])
        self.assertFalse(b["p6_caller_id_shape_ok"])
        self.assertFalse(b["raw_token_shape_ok"])
        self.assertFalse(b["raw_caller_id_shape_ok"])

    def test_graph_version_difference_is_reported_separately(self):
        self.write_raw("B", "token-B-fixture", "101", version="v25.0")
        self.write_raw("C", "token-C-fixture", "202")
        report = diagnose_credentials(
            environment=self.environment(version="v26.0"),
            raw_env_dir=self.directory,
        )
        b = report["lanes"]["B"]
        self.assertEqual("mismatch", b["configuration_comparison"])
        self.assertTrue(b["token_exact_match"])
        self.assertTrue(b["caller_id_exact_match"])
        self.assertFalse(b["graph_version_match"])

    def test_p6_b_and_c_credentials_must_be_distinct(self):
        report = diagnose_credentials(
            environment=self.environment(
                token_c="token-B-fixture", caller_c="101",
            ),
            raw_env_dir=self.directory,
        )
        self.assertFalse(report["p6_b_c_token_values_distinct"])
        self.assertFalse(report["p6_b_c_caller_ids_distinct"])
        rendered = json.dumps(report)
        self.assertNotIn("token-B-fixture", rendered)
        self.assertNotIn("101", rendered)

    def test_only_the_two_named_raw_files_are_read(self):
        self.write_raw("B", "token-B-fixture", "101")
        self.write_raw("C", "token-C-fixture", "202")
        (self.directory / ".env.local").write_text(
            "SHOULD_NOT_BE_READ=local-only", encoding="utf-8",
        )
        original_read_text = Path.read_text
        read_names = []

        def record_read(path, *args, **kwargs):
            read_names.append(path.name)
            return original_read_text(path, *args, **kwargs)

        with patch.object(Path, "read_text", new=record_read):
            report = diagnose_credentials(
                environment=self.environment(), raw_env_dir=self.directory,
            )
        self.assertEqual({".env.api_b", ".env.api_c"}, set(read_names))
        self.assertEqual("readable", report["lanes"]["B"]["raw_file_status"])
        self.assertEqual("readable", report["lanes"]["C"]["raw_file_status"])
        self.assertNotIn("SHOULD_NOT_BE_READ", json.dumps(report))

    def test_symlinked_credential_file_is_refused(self):
        with patch(
            "raw_v2.three_api_credential_diagnostic.Path.is_symlink",
            return_value=True,
        ):
            report = diagnose_credentials(
                environment=self.environment(), raw_env_dir=self.directory,
            )
        self.assertEqual("symlink_refused",
                         report["lanes"]["B"]["raw_file_status"])
        self.assertEqual("unavailable",
                         report["lanes"]["B"]["configuration_comparison"])


if __name__ == "__main__":
    unittest.main()
