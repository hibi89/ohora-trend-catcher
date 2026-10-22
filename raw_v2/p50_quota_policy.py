"""P50 quota policy: switch A to B to C at 85%, pause 63 minutes.

This module is pure policy. After cooldown a fresh verified usage reading is
required before another Meta request; elapsed time alone is not quota proof.
"""
from datetime import datetime, timedelta, timezone

THRESHOLD = 85
COOLDOWN = timedelta(minutes=63)
LANES = ("A", "B", "C")


def choose_lane(usage, *, now=None, cooldown_started=None):
    """Return (lane, resume_at). None lane means no HTTP is authorized."""
    now = now or datetime.now(timezone.utc)
    if now.tzinfo is None:
        raise ValueError("Timezone-aware time required")
    if set(usage) != set(LANES):
        raise ValueError("Exactly A/B/C quota readings required")
    for lane in LANES:
        value = usage[lane]
        if value is not None and (type(value) not in (int, float) or not 0 <= value <= 100):
            raise ValueError("Invalid quota reading")
        if value is not None and value < THRESHOLD:
            return lane, None
    if cooldown_started is None:
        cooldown_started = now
    if cooldown_started.tzinfo is None:
        raise ValueError("Timezone-aware cooldown required")
    return None, cooldown_started + COOLDOWN
