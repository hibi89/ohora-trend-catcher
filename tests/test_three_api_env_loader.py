import contextlib
import io
import os
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "raw_v2"))

from three_api_env_loader import (
    CredentialConfigurationError,
    load_three_api_environment,
    main as loader_main,
)
import three_api_manual_pilot


class ThreeApiEnvironmentLoaderTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.directory = Path(self.temp.name)
        self.values = {
            "A": ("synthetic-token-A", "10001"),
            "B": ("synthetic-token-B", "10002"),
            "C": ("synthetic-token-C", "10003"),
        }
        self.write_files()

    def tearDown(self):
        self.temp.cleanup()

    def write_files(self, *, version_by_lane=None):
        for lane, (token, caller) in self.values.items():
            version = (version_by_lane or {}).get(lane, "v26.0")
            (self.directory / f".env.api_{lane.lower()}").write_text(
                "META_ACCESS_TOKEN=" + token + "\n"
                "META_IG_USER_ID=" + caller + "\n"
                "META_GRAPH_VERSION=" + version + "\n",
                encoding="utf-8",
            )

    def test_maps_raw_keys_to_p6_environment_without_mutating_base(self):
        base = {"KEEP_ME": "present"}
        result = load_three_api_environment(
            self.directory, base_environment=base,
        )
        self.assertEqual(result["P6_META_TOKEN_A"], "synthetic-token-A")
        self.assertEqual(result["P6_META_IG_ID_B"], "10002")
        self.assertEqual(result["P6_META_TOKEN_C"], "synthetic-token-C")
        self.assertEqual(result["P6_META_GRAPH_VERSION"], "v26.0")
        self.assertEqual(base, {"KEEP_ME": "present"})

    def test_duplicate_tokens_are_rejected_without_echoing_values(self):
        token, _ = self.values["C"]
        self.values["C"] = (self.values["A"][0], "10003")
        self.write_files()
        with self.assertRaises(CredentialConfigurationError) as caught:
            load_three_api_environment(self.directory)
        self.assertNotIn(token, str(caught.exception))
        self.assertIn("unique", str(caught.exception))

    def test_duplicate_caller_ids_are_rejected(self):
        self.values["C"] = ("synthetic-token-C", self.values["A"][1])
        self.write_files()
        with self.assertRaisesRegex(CredentialConfigurationError, "caller IDs"):
            load_three_api_environment(self.directory)

    def test_blank_required_value_is_rejected(self):
        path = self.directory / ".env.api_b"
        path.write_text(
            "META_ACCESS_TOKEN=\nMETA_IG_USER_ID=10002\n"
            "META_GRAPH_VERSION=v26.0\n",
            encoding="utf-8",
        )
        with self.assertRaisesRegex(CredentialConfigurationError, "blank"):
            load_three_api_environment(self.directory)

    def test_version_mismatch_and_non_v26_version_are_rejected(self):
        self.write_files(version_by_lane={"C": "v25.0"})
        with self.assertRaisesRegex(CredentialConfigurationError, "v26.0"):
            load_three_api_environment(self.directory)

    def test_unknown_duplicate_and_malformed_keys_are_rejected(self):
        path = self.directory / ".env.api_a"
        path.write_text(
            "META_ACCESS_TOKEN=synthetic-token-A\n"
            "META_ACCESS_TOKEN=shadow\n"
            "META_IG_USER_ID=10001\nMETA_GRAPH_VERSION=v26.0\n",
            encoding="utf-8",
        )
        with self.assertRaisesRegex(CredentialConfigurationError, "duplicate key"):
            load_three_api_environment(self.directory)
        path.write_text(
            "META_ACCESS_TOKEN=synthetic-token-A\nMETA_IG_USER_ID=10001\n"
            "META_GRAPH_VERSION=v26.0\nOTHER=value\n",
            encoding="utf-8",
        )
        with self.assertRaisesRegex(CredentialConfigurationError, "unsupported key"):
            load_three_api_environment(self.directory)

    def test_symlink_credential_file_is_rejected(self):
        path = self.directory / ".env.api_a"
        original = self.directory / "original.env"
        original.write_text(
            "META_ACCESS_TOKEN=synthetic-token-A\nMETA_IG_USER_ID=10001\n"
            "META_GRAPH_VERSION=v26.0\n",
            encoding="utf-8",
        )
        path.unlink()
        try:
            os.symlink(original, path)
        except (OSError, NotImplementedError):
            self.skipTest("symbolic link creation is unavailable")
        with self.assertRaisesRegex(CredentialConfigurationError, "regular file"):
            load_three_api_environment(self.directory)

    def test_check_cli_only_reports_redacted_summary(self):
        output = io.StringIO()
        with contextlib.redirect_stdout(output):
            self.assertEqual(loader_main(["--directory", str(self.directory)]), 0)
        text = output.getvalue()
        self.assertIn("Meta calls=0", text)
        for token, caller in self.values.values():
            self.assertNotIn(token, text)
            self.assertNotIn(caller, text)

    def test_pilot_live_entrypoint_maps_files_without_dispatching_http(self):
        source = self.directory / "unused-account-list.csv"
        source.touch()
        workspace = self.directory / "unused-workspace"
        captured = {}
        def fake_run(*args, **kwargs):
            captured.update(kwargs)
            return {"mode": "MOCKED_ONLY", "live_meta_called": False}
        output = io.StringIO()
        with patch.object(three_api_manual_pilot, "run_manual_pilot", side_effect=fake_run):
            with contextlib.redirect_stdout(output):
                code = three_api_manual_pilot.main([
                    "--source", str(source),
                    "--workspace", str(workspace),
                    "--credentials-dir", str(self.directory),
                    "--live", "--confirm-owner-approval",
                ])
        self.assertEqual(code, 0)
        mapped = captured["environment"]
        self.assertEqual(mapped["P6_META_TOKEN_A"], "synthetic-token-A")
        self.assertEqual(mapped["P6_META_IG_ID_C"], "10003")
        self.assertNotIn("synthetic-token-A", output.getvalue())
        self.assertNotIn("10003", output.getvalue())


if __name__ == "__main__":
    unittest.main()