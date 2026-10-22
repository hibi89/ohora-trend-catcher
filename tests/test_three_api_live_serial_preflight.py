"""Offline preflight checks for opt-in live serial step."""
from datetime import datetime, timezone
import unittest

from three_api_live_serial_step import LiveSerialStep, LiveSerialStepError


class LiveSerialPreflightTests(unittest.TestCase):
    def setUp(self):
        self.runner = object.__new__(LiveSerialStep)
        self.now = datetime(2026, 10, 21, tzinfo=timezone.utc)
        self.base = dict(
            run_id="offline", roster_digest="0" * 64,
            day_kst=self.now.date(), requested_at=self.now,
            api_version="v26.0", caller_ids={"A": "1", "B": "2", "C": "3"},
            approved=True,
        )

    def test_wrong_roster_length_rejected_before_coordinator_access(self):
        with self.assertRaisesRegex(LiveSerialStepError, "294-account roster"):
            self.runner.step([], **self.base)

    def test_missing_lane_identity_rejected_before_coordinator_access(self):
        options = dict(self.base)
        options["caller_ids"] = {"A": "1", "B": "2"}
        with self.assertRaisesRegex(LiveSerialStepError, "A/B/C caller identities"):
            self.runner.step([{} for _ in range(294)], **options)

    def test_naive_time_rejected_before_coordinator_access(self):
        options = dict(self.base)
        options["requested_at"] = datetime(2026, 10, 21)
        with self.assertRaisesRegex(LiveSerialStepError, "Timezone-aware"):
            self.runner.step([{} for _ in range(294)], **options)


if __name__ == "__main__":
    unittest.main()
