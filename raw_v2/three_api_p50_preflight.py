"""P50 offline entrypoint: validates the exact roster and previews 150 isolated jobs.

No Graph HTTP, tokens, Capture or RAW writes. Never uses a P6/294 DB.
The live P50 integration is deliberately disabled until the strict 294-only
runner, RAW initializer, and approval gates have a separately tested P50 path.
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path
import sqlite3

from three_api_p50_roster import load_p50_roster, validate_p50_roster


def preview(source):
    accounts, digest = load_p50_roster(source)
    validate_p50_roster(accounts)
    # An ephemeral in-memory plan ensures no workspace or operational DB mutation.
    with sqlite3.connect(":memory:") as db:
        db.execute("CREATE TABLE jobs(account_ordinal INTEGER, page INTEGER, lane TEXT, phase TEXT)")
        for account in accounts:
            for page, lane in enumerate(("A", "C", "B"), 1):
                db.execute("INSERT INTO jobs VALUES(?,?,?,?)", (
                    account["ordinal"], page, lane,
                    "front" if page == 1 else "catchup"))
        counts = dict(db.execute(
            "SELECT lane,COUNT(*) FROM jobs GROUP BY lane").fetchall())
    return {
        "mode": "P50_OFFLINE_PREFLIGHT",
        "accounts": len(accounts),
        "country_counts": {
            country: sum(a["country"] == country for a in accounts)
            for country in ("US", "KR", "JP")
        },
        "roster_sha256": digest,
        "planned_pages": sum(counts.values()),
        "lane_pages": counts,
        "page_limit": 4,
        "live_meta_called": False,
        "operational_db_changed": False,
    }


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", required=True, type=Path)
    args = parser.parse_args(argv)
    result = preview(args.source)
    print(json.dumps(result, ensure_ascii=False, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
