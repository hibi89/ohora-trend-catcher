"""Offline P50 checks for Windows-interruptible waits and KST log deadlines."""
import sys
from pathlib import Path
from datetime import datetime, timedelta, timezone
import threading
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "raw_v2"))
from p50_v2_collector import next_check_kst, wait_until_or_stopped


class InterruptibleWaitTests(unittest.TestCase):
    def test_utc_deadline_displays_as_kst(self):
        utc = datetime(2026, 10, 23, 1, 0, tzinfo=timezone.utc)
        self.assertEqual(next_check_kst(utc), "2026-10-23T10:00:00+09:00")

    def test_cancellation_checks_in_one_second_chunks(self):
        class StopOnFirstWait:
            called = []
            stopped = False

            def is_set(self):
                return self.stopped

            def wait(self, timeout):
                self.called.append(timeout)
                self.stopped = True
                return True

        stop = StopOnFirstWait()
        deadline = datetime.now(timezone.utc) + timedelta(hours=1)
        self.assertTrue(wait_until_or_stopped(deadline, stop))
        self.assertEqual(len(stop.called), 1)
        self.assertGreater(stop.called[0], 0)
        self.assertLessEqual(stop.called[0], 1.0)

    def test_stop_already_requested(self):
        stop = threading.Event()
        stop.set()
        self.assertTrue(wait_until_or_stopped(datetime.now(timezone.utc) + timedelta(hours=1), stop))

    def test_expired_deadline_does_not_wait(self):
        stop = threading.Event()
        self.assertFalse(wait_until_or_stopped(datetime.now(timezone.utc) - timedelta(seconds=1), stop))

    def test_naive_deadline_rejected(self):
        with self.assertRaises(ValueError):
            next_check_kst(datetime(2026, 10, 23, 1, 0))
        with self.assertRaises(ValueError):
            wait_until_or_stopped(datetime(2026, 10, 23, 1, 0), threading.Event())


if __name__ == "__main__":
    unittest.main()
