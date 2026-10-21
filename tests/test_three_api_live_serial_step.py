"""Offline safety checks for the opt-in serial runner."""
import unittest
from datetime import datetime, timezone

from three_api_live_serial_step import LiveSerialStep, LiveSerialStepError


class LiveSerialStepGateTests(unittest.TestCase):
    def setUp(self):
        # Bypass construction solely to prove input gates reject before any
        # coordinator, transport, journal, or database access.
        self.runner = object.__new__(LiveSerialStep)
        self.kwargs = dict(
            run_id="offline", roster_digest="x", day_kst=None,
            requested_at=datetime.now(timezone.utc), api_version="v26.0",
            caller_ids={"A": "1", "B": "2", "C": "3"}, approved=True,
        )

    def test_no_approval_does_not_touch_coordinator(self):
        with self.assertRaisesRegex(LiveSerialStepError, "Explicit live approval"):
            self.runner.step([], **{**self.kwargs, "approved": False})

    def test_non_boolean_approval_denied(self):
        with self.assertRaisesRegex(LiveSerialStepError, "Explicit live approval"):
            self.runner.step([], **{**self.kwargs, "approved": 1})

    def test_naive_clock_denied(self):
        with self.assertRaisesRegex(LiveSerialStepError, "Timezone-aware"):
            self.runner.step([], **{
                **self.kwargs, "requested_at": datetime(2026, 10, 21)})

    def test_partial_roster_denied(self):
        with self.assertRaisesRegex(LiveSerialStepError, "294-account"):
            self.runner.step([{}], **self.kwargs)

    def test_partial_lane_identities_denied(self):
        with self.assertRaisesRegex(LiveSerialStepError, "A/B/C"):
            self.runner.step([{} for _ in range(294)], **{
                **self.kwargs, "caller_ids": {"A": "1"}})


if __name__ == "__main__":
    unittest.main()
