"""Offline P6 KST schedule policy tests: no Meta requests, credentials, or DB."""
from datetime import date, datetime, timedelta, timezone
import contextlib
import io
import json
import unittest

from src.collection_schedule import decide, main


class DailyStartTests(unittest.TestCase):
    def local(self, day, hour, minute, second=0):
        return datetime.fromisoformat(
            f"{day}T{hour:02d}:{minute:02d}:{second:02d}+09:00"
        )

    def plan(self, at, **kwargs):
        return decide(
            now=at, live_runner_verified=True,
            lane_dispatch_allowed=True, **kwargs
        )

    def test_before_midnight_bootstrap_immediate(self):
        d = self.plan(self.local("2026-10-21", 23, 57))
        self.assertEqual(d.action, "BOOTSTRAP_DUE")
        self.assertEqual(d.day_kst, "2026-10-21")
        self.assertEqual(d.scheduled_start_kst, "00:10")
        self.assertEqual(d.daily_page_goal, 882)

    def test_after_midnight_without_success_bootstraps_new_date(self):
        self.assertEqual(
            self.plan(self.local("2026-10-22", 0, 1)).action, "BOOTSTRAP_DUE"
        )

    def test_verified_success_becomes_next_day_anniversary(self):
        first = self.local("2026-10-21", 23, 47, 39)
        self.assertEqual(self.plan(
            self.local("2026-10-22", 23, 46),
            first_verified_success_at=first,
        ).action, "WAIT_START_WINDOW")
        d = self.plan(
            self.local("2026-10-22", 23, 47),
            first_verified_success_at=first,
        )
        self.assertEqual(d.action, "START_DAILY")
        self.assertEqual(d.scheduled_start_kst, "23:47")
        self.assertEqual(d.start_time_source, "verified_first_success")

    def test_first_success_same_day_does_not_seed_twice(self):
        first = self.local("2026-10-21", 23, 47)
        self.assertEqual(self.plan(
            self.local("2026-10-21", 23, 59),
            first_verified_success_at=first,
        ).action, "WAIT_NEXT_DAY")

    def test_started_today_resumes_without_reseeding(self):
        d = self.plan(
            self.local("2026-10-21", 19, 43),
            first_verified_success_at=self.local("2026-10-20", 18, 2),
            daily_day_kst=date(2026, 10, 21),
            daily_state="running",
        )
        self.assertEqual(d.action, "CONTINUE_DAILY")

    def test_completed_today_does_not_repeat(self):
        d = self.plan(
            self.local("2026-10-21", 20, 21),
            first_verified_success_at=self.local("2026-10-20", 9, 2),
            daily_day_kst=date(2026, 10, 21),
            daily_state="complete",
        )
        self.assertEqual(d.action, "DAILY_COMPLETE")

    def test_previous_day_pending_new_day_due(self):
        d = self.plan(
            self.local("2026-10-21", 12, 31),
            first_verified_success_at=self.local("2026-10-20", 12, 30),
            daily_day_kst=date(2026, 10, 20),
            daily_state="pending",
        )
        self.assertEqual(d.action, "START_DAILY")

    def test_cooldown_precedes_resume(self):
        now = self.local("2026-10-21", 21, 0)
        d = self.plan(
            now, daily_day_kst=date(2026, 10, 21),
            daily_state="running",
            ready_at=now + timedelta(minutes=65)
        )
        self.assertEqual(d.action, "COOLDOWN")

    def test_past_ready_time_cannot_auto_reset_held_lane(self):
        now = self.local("2026-10-21", 21, 0)
        d = decide(
            now=now, live_runner_verified=True,
            lane_dispatch_allowed=False,
            ready_at=now - timedelta(minutes=65)
        )
        self.assertEqual(d.action, "WAIT_VERIFIED_QUOTA_RESET")

    def test_manual_review_not_bypassed_next_day(self):
        self.assertEqual(self.plan(
            self.local("2026-10-22", 0, 20),
            daily_day_kst=date(2026, 10, 21),
            daily_state="manual_review",
        ).action, "MANUAL_REVIEW")

    def test_unverified_live_bridge_blocked(self):
        d = decide(
            now=self.local("2026-10-21", 23, 50),
            live_runner_verified=False, lane_dispatch_allowed=True
        )
        self.assertEqual(d.action, "WAIT_LIVE_RUNNER")

    def test_five_minute_tick_at_midnight(self):
        self.assertEqual(
            self.plan(self.local("2026-10-21", 23, 57, 2)).next_check_kst,
            "2026-10-22T00:00:00+09:00"
        )
        self.assertEqual(
            self.plan(self.local("2026-10-21", 23, 55)).next_check_kst,
            "2026-10-22T00:00:00+09:00"
        )

    def test_utc_input_converts_to_kst(self):
        d = self.plan(datetime(
            2026, 10, 21, 14, 58, tzinfo=timezone.utc
        ))
        self.assertEqual(d.day_kst, "2026-10-21")
        self.assertEqual(d.next_check_kst, "2026-10-22T00:00:00+09:00")

    def test_invalid_dates_and_bools(self):
        now = self.local("2026-10-21", 23, 0)
        for bad in (
            dict(now=datetime(2026, 10, 21, 23, 0)),
            dict(live_runner_verified=1),
            dict(lane_dispatch_allowed=1),
            dict(daily_state="unknown"),
            dict(daily_state="running"),
            dict(daily_day_kst=date(2026, 10, 22)),
            dict(first_verified_success_at=now+timedelta(minutes=1)),
        ):
            args = dict(
                now=now, live_runner_verified=True,
                lane_dispatch_allowed=True
            )
            args.update(bad)
            with self.subTest(bad=bad), self.assertRaises(ValueError):
                decide(**args)

    def test_cli_plan_only(self):
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            rc = main([
                "--now", "2026-10-21T23:50:00+09:00",
                "--live-runner-verified", "--lane-dispatch-allowed"
            ])
        self.assertEqual(rc, 0)
        payload = json.loads(out.getvalue())
        self.assertEqual(payload["action"], "BOOTSTRAP_DUE")
        self.assertIn("No HTTP authorization", payload["note"])


if __name__ == "__main__":
    unittest.main()
