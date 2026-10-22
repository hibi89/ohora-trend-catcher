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


def initialize_isolated_workspace(source, workspace):
    """Create a dedicated P50 RAW/control workspace without HTTP or credentials.

    Existing stores are never silently reused for a different roster.
    The caller supplies the P50 roster; the original 294-account loader is
    intentionally not involved.
    """
    from collector import connect
    from three_api_p50_coordinator import P50Coordinator

    accounts, digest = load_p50_roster(source)
    validate_p50_roster(accounts)
    root = Path(workspace).expanduser()
    if root.is_symlink() or root.name.lower() != "p50":
        raise ValueError("Workspace must be a dedicated directory named p50")
    root = root.resolve()
    raw_path = root / "p50_raw.sqlite"
    coordinator_path = root / "control" / "coordinator.sqlite"
    if raw_path.is_symlink() or coordinator_path.is_symlink():
        raise ValueError("Symlink database paths are forbidden")
    if raw_path.exists() or coordinator_path.exists():
        raise ValueError("P50 bootstrap is create-only; existing stores need explicit inspection")
    raw = connect(raw_path, accounts, digest)
    try:
        raw.close()
        coordinator = P50Coordinator(coordinator_path)
        try:
            coordinator.seed()
        finally:
            coordinator.db.close()
    except BaseException:
        # Do not delete a possibly valid RAW file after a partial failure.
        raise
    return {
        "mode": "P50_OFFLINE_WORKSPACE_CREATED",
        "accounts": len(accounts),
        "planned_pages": 150,
        "live_meta_called": False,
        "workspace": str(root),
    }


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", required=True, type=Path)
    parser.add_argument("--initialize-workspace", type=Path, default=None,
                        help="Create an isolated p50 RAW and control store; no Meta HTTP")
    args = parser.parse_args(argv)
    result = (initialize_isolated_workspace(args.source, args.initialize_workspace)
              if args.initialize_workspace is not None else preview(args.source))
    print(json.dumps(result, ensure_ascii=False, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
