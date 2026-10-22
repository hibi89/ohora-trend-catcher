"""Read-only cooldown eligibility for the isolated P6 pilot.

This utility never clears a lane, retries an intent, reads tokens, or sends HTTP.
The operator must separately approve any manual cooldown reset.
"""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import json
from pathlib import Path
import sqlite3


def inspect(path: Path):
    if not path.is_file() or path.is_symlink():
        raise ValueError("Existing coordinator SQLite file required")
    now = datetime.now(timezone.utc)
    uri = path.resolve().as_uri() + "?mode=ro"
    with sqlite3.connect(uri, uri=True) as db:
        rows = db.execute(
            "SELECT lane,halted,reason,ready_at FROM lanes ORDER BY lane"
        ).fetchall()
    result = []
    for lane, halted, reason, ready_at in rows:
        ready = datetime.fromisoformat(ready_at.replace("Z", "+00:00"))
        if ready.utcoffset() is None:
            raise ValueError("Timezone-aware cooldown required")
        result.append({
            "lane": lane,
            "halted": bool(halted),
            "reason": reason,
            "cooldown_elapsed": now >= ready,
            "eligible_for_manual_reset": bool(halted) and now >= ready,
            "ready_at_kst": ready.astimezone(
                __import__("datetime").timezone(__import__("datetime").timedelta(hours=9))
            ).isoformat(),
        })
    return {"mode": "READ_ONLY_COOLDOWN_DIAGNOSTIC",
            "live_meta_called": False, "db_writes": 0, "lanes": result}


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--coordinator", type=Path, required=True)
    args = parser.parse_args()
    try:
        print(json.dumps(inspect(args.coordinator), ensure_ascii=False))
    except (ValueError, sqlite3.Error):
        parser.exit(2, "Cooldown status unavailable; no changes made.\n")


if __name__ == "__main__":
    main()
