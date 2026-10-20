"""Synthetic-only regression for the OS-owned three-API serial tick gate."""
from datetime import datetime, timezone
from pathlib import Path
import sys
import tempfile
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "raw_v2"))

from three_api_serial_lock import SerialTickBusy, serial_tick_lock
from three_api_daily_orchestrator import OfflineDailyOrchestrator
from test_three_api_daily_orchestrator import DAY, NOW, accounts, fixture


class SerialTickLockTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.source = self.root / "source.sqlite"

    def test_nested_lock_on_same_source_is_refused(self):
        with serial_tick_lock(self.source):
            with self.assertRaises(SerialTickBusy):
                with serial_tick_lock(self.source):
                    self.fail("Second owner acquired the serial lock")

    def test_lock_recovers_after_exception(self):
        with self.assertRaisesRegex(RuntimeError, "synthetic failure"):
            with serial_tick_lock(self.source):
                raise RuntimeError("synthetic failure")
        with serial_tick_lock(self.source):
            self.assertTrue(True)

    def test_unrelated_offline_source_db_has_independent_gate(self):
        another = self.root / "other.sqlite"
        with serial_tick_lock(self.source):
            with serial_tick_lock(another):
                self.assertTrue(True)

    def test_busy_gate_blocks_serial_and_legacy_direct_step_without_raw(self):
        runner = OfflineDailyOrchestrator(
            self.source, self.root / "gap.sqlite",
            self.root / "three_api_raw_mirror.sqlite",
            accounts(), DAY,
            {(1, 1, None): fixture([115, 114, 113, 112], "p2")},
            {},
        )
        self.addCleanup(runner.close)
        with serial_tick_lock(self.source):
            self.assertEqual(runner.step_serial(now=NOW),
                             {"status": "serial_busy", "selected_lane": None})
            self.assertEqual(runner.step("A", now=NOW),
                             {"status": "serial_busy", "lane": "A"})
            self.assertEqual(runner.mirror.summary()["raw_posts"], 0)
            self.assertEqual(runner.coverage()["front_pages"], 0)
            self.assertIsNone(runner.coordinator.db.execute(
                "SELECT last_lane FROM offline_serial_turn WHERE singleton=1"
            ).fetchone())
        result = runner.step_serial(now=NOW)
        self.assertEqual((result["status"], result["selected_lane"]),
                         ("front_committed", "A"))
        self.assertEqual(runner.mirror.summary()["raw_posts"], 4)
        self.assertEqual(runner.coverage()["front_pages"], 1)


if __name__ == "__main__":
    unittest.main()
