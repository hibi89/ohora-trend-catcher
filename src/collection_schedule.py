"""P6 KST collection start policy. OFFLINE decisions only: no Meta, credentials or DB.

A one-off initial collection can begin immediately when an audited live runner is
ready, even before midnight on the setup day. The first VERIFIED persistent RAW
success clock becomes the daily start time. Until then 00:10 KST is provisional.
n8n may evaluate every five minutes, but this module cannot authorize HTTP.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import date, datetime, time, timedelta, timezone
import argparse
import json

KST = timezone(timedelta(hours=9))
PROVISIONAL_START = time(0, 10)
TICK_MINUTES = 5
VALID_STATES = frozenset(("absent", "pending", "running", "complete", "manual_review"))


@dataclass(frozen=True)
class ScheduleDecision:
    action: str
    day_kst: str
    scheduled_start_kst: str
    start_time_source: str
    next_check_kst: str
    daily_page_goal: int = 882
    post_limit: int = 4
    api_lanes: int = 3
    note: str = "No HTTP authorization; execution requires separately verified live runner"

    def to_dict(self) -> dict:
        return asdict(self)


def _aware(value: datetime | None, label: str) -> None:
    if value is not None and (
        not isinstance(value, datetime)
        or value.tzinfo is None or value.utcoffset() is None
    ):
        raise ValueError(f"{label} must be a timezone-aware datetime")


def _next_tick(local: datetime) -> str:
    boundary = local.replace(second=0, microsecond=0)
    boundary += timedelta(minutes=TICK_MINUTES - boundary.minute % TICK_MINUTES)
    return boundary.isoformat(timespec="seconds")


def decide(*, now: datetime, live_runner_verified: bool,
           first_verified_success_at: datetime | None = None,
           daily_day_kst: date | None = None,
           daily_state: str = "absent",
           lane_dispatch_allowed: bool = False,
           ready_at: datetime | None = None) -> ScheduleDecision:
    """Read-only decision; never alters the P6 API, jobs, quotas or RAW.

    First launch may be attempted as soon as the verified collector is ready.
    Same-day jobs resume instead of restarting at each tick.
    New KST daily epochs supersede ordinary prior-day work, not unknown outcomes.
    A past ready_at NEVER overrides the coordinator's explicit quota reset gate.
    """
    _aware(now, "now")
    _aware(first_verified_success_at, "first_verified_success_at")
    _aware(ready_at, "ready_at")
    if type(live_runner_verified) is not bool or type(lane_dispatch_allowed) is not bool:
        raise ValueError("Live readiness and dispatch approval must be booleans")
    if daily_state not in VALID_STATES:
        raise ValueError("Unknown daily job state")
    if daily_day_kst is not None and (
        not isinstance(daily_day_kst, date) or isinstance(daily_day_kst, datetime)
    ):
        raise ValueError("daily_day_kst must be a date")
    if daily_state != "absent" and daily_day_kst is None:
        raise ValueError("Existing daily state requires its KST day")
    if first_verified_success_at is not None and first_verified_success_at > now:
        raise ValueError("First successful receipt cannot be in the future")

    local = now.astimezone(KST)
    today = local.date()
    if daily_day_kst and daily_day_kst > today:
        raise ValueError("Daily epoch cannot be in the future")
    first_local = (
        first_verified_success_at.astimezone(KST)
        if first_verified_success_at else None
    )
    start = (
        first_local.timetz().replace(tzinfo=None, second=0, microsecond=0)
        if first_local else PROVISIONAL_START
    )
    start_hhmm = start.strftime("%H:%M")
    source = "verified_first_success" if first_local else "provisional_bootstrap"

    def result(action: str) -> ScheduleDecision:
        return ScheduleDecision(
            action, today.isoformat(), start_hhmm, source, _next_tick(local)
        )

    if not live_runner_verified:
        return result("WAIT_LIVE_RUNNER")
    if daily_state == "manual_review":
        return result("MANUAL_REVIEW")
    if daily_day_kst == today and daily_state == "complete":
        return result("DAILY_COMPLETE")
    if ready_at is not None and now < ready_at:
        return result("COOLDOWN")
    if not lane_dispatch_allowed:
        return result("WAIT_VERIFIED_QUOTA_RESET")

    if daily_day_kst == today and daily_state in ("pending", "running"):
        return result("CONTINUE_DAILY")
    if first_local is None:
        return result("BOOTSTRAP_DUE")
    if first_local.date() == today:
        return result("WAIT_NEXT_DAY")
    if local.time().replace(tzinfo=None) >= start:
        return result("START_DAILY")
    return result("WAIT_START_WINDOW")


def _parse(value: str | None) -> datetime | None:
    if not value:
        return None
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    _aware(parsed, "ISO timestamp")
    return parsed


def main(argv=None) -> int:
    """Read-only, JSON-only planning preview; never touches Meta."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--now", required=True, help="ISO with timezone offset")
    parser.add_argument("--live-runner-verified", action="store_true")
    parser.add_argument("--lane-dispatch-allowed", action="store_true")
    parser.add_argument("--first-verified-success-at")
    parser.add_argument("--daily-day-kst")
    parser.add_argument("--daily-state", choices=sorted(VALID_STATES), default="absent")
    parser.add_argument("--ready-at")
    args = parser.parse_args(argv)
    try:
        result = decide(
            now=_parse(args.now),
            live_runner_verified=args.live_runner_verified,
            first_verified_success_at=_parse(args.first_verified_success_at),
            daily_day_kst=date.fromisoformat(args.daily_day_kst)
                if args.daily_day_kst else None,
            daily_state=args.daily_state,
            lane_dispatch_allowed=args.lane_dispatch_allowed,
            ready_at=_parse(args.ready_at),
        )
    except (TypeError, ValueError):
        print(json.dumps({"action": "INVALID_INPUT", "no_http": True}))
        return 2
    print(json.dumps(result.to_dict(), sort_keys=True, ensure_ascii=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
