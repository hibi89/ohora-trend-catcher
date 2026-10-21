"""Offline safety checks for the opt-in serial runner."""
import unittest
from three_api_live_serial_step import LiveSerialStep, LiveSerialStepError


class LiveSerialStepGateTests(unittest.TestCase):
    def test_no_approval_does_not_touch_coordinator(self):
        runner = object.__new__(LiveSerialStep)
        with self.assertRaisesRegex(LiveSerialStepError, "Explicit live approval"):
            runner.step([], run_id="offline", roster_digest="x",
                        day_kst=None, requested_at=None,
                        api_version="v26.0", caller_ids={}, approved=False)

    def test_non_boolean_approval_denied(self):
        runner = object.__new__(LiveSerialStep)
        with self.assertRaises(LiveSerialStepError):
            runner.step([], run_id="offline", roster_digest="x",
                        day_kst=None, requested_at=None,
                        api_version="v26.0", caller_ids={}, approved=1)


if __name__ == "__main__":
    unittest.main()
