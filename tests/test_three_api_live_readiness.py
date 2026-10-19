"""No-network tests for G0-G6 evidence-only readiness evaluation."""
from pathlib import Path
import sys
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "raw_v2"))
from three_api_live_readiness import GATES, evaluate_live_readiness


class LiveReadinessTests(unittest.TestCase):
    def test_missing_input_fails_closed(self):
        for value in (None, {}, [], "", False, 1):
            with self.subTest(value=value):
                result = evaluate_live_readiness(value)
                self.assertFalse(result["runtime_authorized"])
                self.assertFalse(result["all_evidence_present"])
                self.assertEqual(result["blocked_gates"], list(GATES))

    def test_each_missing_gate_blocks(self):
        for missing in GATES:
            with self.subTest(missing=missing):
                evidence = {gate: gate != missing for gate in GATES}
                result = evaluate_live_readiness(evidence)
                self.assertTrue(result["valid_input"])
                self.assertEqual(result["blocked_gates"], [missing])
                self.assertFalse(result["runtime_authorized"])

    def test_complete_evidence_is_not_runtime_approval(self):
        result = evaluate_live_readiness({gate: True for gate in GATES})
        self.assertTrue(result["all_evidence_present"])
        self.assertFalse(result["runtime_authorized"])
        self.assertFalse(result["live_meta_verified"])
        self.assertFalse(result["unattended_execution_enabled"])
        self.assertEqual(result["reason"], "EVIDENCE_COMPLETE_APPROVAL_STILL_REQUIRED")

    def test_truthy_and_extra_keys_rejected(self):
        for bad in ("true", 1, None, [], {}):
            with self.subTest(bad=bad):
                evidence = {gate: True for gate in GATES}
                evidence["G2"] = bad
                result = evaluate_live_readiness(evidence)
                self.assertFalse(result["valid_input"])
                self.assertEqual(result["blocked_gates"], list(GATES))
        evidence = {gate: True for gate in GATES}
        evidence["approved"] = True
        self.assertFalse(evaluate_live_readiness(evidence)["valid_input"])

    def test_never_echoes_secret_or_arbitrary_payload(self):
        secret = "DO_NOT_ECHO_FAKE_SECRET"
        result = evaluate_live_readiness({"G0": secret})
        self.assertNotIn(secret, repr(result))
        self.assertFalse(result["runtime_authorized"])


if __name__ == "__main__":
    unittest.main()
