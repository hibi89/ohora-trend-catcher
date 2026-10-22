"""Read-only P50 readiness and RAW progress report; no tokens, no Meta HTTP."""
import argparse
import json
import sqlite3
from pathlib import Path


def _count(db, query):
    return db.execute(query).fetchone()[0]


def inspect_p50(workspace):
    root = Path(workspace).expanduser()
    if root.name.lower() != "p50" or root.is_symlink():
        raise ValueError("Dedicated non-symlink p50 workspace required")
    raw_path = root / "p50_raw.sqlite"
    if not raw_path.is_file() or raw_path.is_symlink():
        return {"status": "not_initialized", "raw_exists": False,
                "live_meta_called": False}
    raw = sqlite3.connect(raw_path.resolve().as_uri() + "?mode=ro", uri=True)
    try:
        raw.row_factory = sqlite3.Row
        report = {
            "status": "initialized",
            "raw_exists": True,
            "accounts": _count(raw, "SELECT COUNT(*) FROM accounts"),
            "unique_posts": _count(raw, "SELECT COUNT(*) FROM posts"),
            "post_observations": _count(raw, "SELECT COUNT(*) FROM post_observations"),
            "contract_receipts": _count(
                raw, "SELECT COUNT(*) FROM sqlite_master "
                "WHERE type='table' AND name='three_api_contract_receipts'"),
            "live_meta_called": None,  # Cannot infer network activity from persisted counts.
        }
        if report["contract_receipts"]:
            report["contract_receipts"] = _count(
                raw, "SELECT COUNT(*) FROM three_api_contract_receipts")
        else:
            report["contract_receipts"] = 0
        if report["accounts"] != 50:
            raise ValueError("Unexpected RAW account count; refuse P50 status")
        days = []
        for path in sorted(root.glob("????-??-??/coordinator.sqlite")):
            if path.is_symlink() or path.parent.is_symlink():
                raise ValueError("Symlink coordinator forbidden")
            db = sqlite3.connect(path.resolve().as_uri() + "?mode=ro", uri=True)
            try:
                jobs = dict(db.execute(
                    "SELECT state, COUNT(*) FROM jobs GROUP BY state").fetchall())
                lanes = dict(db.execute(
                    "SELECT lane, COUNT(*) FROM jobs WHERE state='done' GROUP BY lane"
                ).fetchall())
                days.append({"date": path.parent.name, "jobs": jobs,
                             "committed_jobs_by_lane": lanes})
            finally:
                db.close()
        report["daily_jobs"] = days
        return report
    finally:
        raw.close()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--workspace", type=Path, required=True)
    args = parser.parse_args()
    print(json.dumps(inspect_p50(args.workspace), ensure_ascii=False, sort_keys=True))


if __name__ == "__main__":
    main()
