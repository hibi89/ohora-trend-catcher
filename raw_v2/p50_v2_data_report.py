"""Read-only report of actual P50 v2 SQLite data, without Meta API calls.

Usage: python raw_v2/p50_v2_data_report.py --workspace <existing p50_v2 path>
Optional: --details (50-account progress), --samples 3 (redacted examples).
No tokens, cursor contents, media URLs, or full API response JSON are printed.
"""
import argparse
from contextlib import closing
from collections import Counter, defaultdict
from datetime import datetime, timedelta, timezone
import json
from pathlib import Path
import sqlite3

KST = timezone(timedelta(hours=9))
POST_FIELDS = (
    "id", "caption", "media_type", "media_product_type", "media_url",
    "permalink", "timestamp", "like_count", "comments_count",
    "thumbnail_url", "view_count",
)


def read_only_database(path):
    if not path.is_file():
        raise FileNotFoundError(f"Existing SQLite database not found: {path}")
    db = sqlite3.connect(path.resolve().as_uri() + "?mode=ro", uri=True)
    db.row_factory = sqlite3.Row
    db.execute("PRAGMA query_only=ON")
    return db


def columns(db, table):
    return [row["name"] for row in db.execute(f"PRAGMA table_info({table})")]


def kst(value):
    if not value:
        return None
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        return None
    return parsed.astimezone(KST).isoformat()


def report(workspace, include_details=False, sample_count=0):
    root = Path(workspace)
    if root.name.lower() != "p50_v2":
        raise ValueError("Expected existing dedicated p50_v2 workspace")
    with closing(read_only_database(root / "raw.sqlite")) as raw, closing(read_only_database(
        root / "planner.sqlite"
    )) as planner:
        raw.execute("BEGIN")
        planner.execute("BEGIN")
        totals = Counter()
        present = Counter()
        field_names = set()
        by_lane = defaultdict(Counter)
        by_account = defaultdict(Counter)
        account_profile = {}
        samples = []
        raw_times = []
        unique_ids = set()
        for page in raw.execute(
            "SELECT cycle,lane,ordinal,response_json,created_at FROM pages ORDER BY rowid"
        ):
            response = json.loads(page["response_json"])
            media = response.get("media") or {}
            posts = media.get("data") or []
            ordinal = page["ordinal"]
            totals["pages"] += 1
            totals["posts_in_raw_pages"] += len(posts)
            by_lane[page["lane"]]["pages"] += 1
            by_lane[page["lane"]]["posts"] += len(posts)
            by_account[ordinal]["pages"] += 1
            by_account[ordinal]["posts"] += len(posts)
            if isinstance(response, dict):
                account_profile[ordinal] = {
                    "username": response.get("username"),
                    "followers_count_latest_raw": response.get("followers_count"),
                    "media_count_latest_raw": response.get("media_count"),
                }
            if page["created_at"]:
                raw_times.append(kst(page["created_at"]))
            for post in posts:
                if not isinstance(post, dict):
                    continue
                field_names.update(post)
                if post.get("id") is not None:
                    unique_ids.add(post["id"])
                for field in POST_FIELDS:
                    if field in post and post[field] is not None:
                        present[field] += 1
                if sample_count and len(samples) < sample_count:
                    samples.append({
                        "account_ordinal": ordinal,
                        "post_id": post.get("id"),
                        "published_at": post.get("timestamp"),
                        "media_type": post.get("media_type"),
                        "media_product_type": post.get("media_product_type"),
                        "like_count": post.get("like_count"),
                        "comments_count": post.get("comments_count"),
                        "view_count": post.get("view_count"),
                        "caption_excerpt": (post.get("caption") or "")[:100],
                    })
        accounts = planner.execute(
            "SELECT ordinal,finished,finished_reason,cursor FROM accounts ORDER BY ordinal"
        ).fetchall()
        progress = planner.execute(
            "SELECT round_no,lane_index,ordinal,slot_count,ready_at "
            "FROM schedule_state WHERE id=1"
        ).fetchone()
        lanes = planner.execute(
            "SELECT lane,usage,halted,ready_at FROM lanes ORDER BY lane"
        ).fetchall()
        snapshot = raw.execute("""SELECT COUNT(*) AS total,
            SUM(CASE WHEN view_count IS NULL THEN 1 ELSE 0 END) AS view_missing,
            SUM(CASE WHEN view_count=0 THEN 1 ELSE 0 END) AS view_zero,
            SUM(CASE WHEN like_count IS NULL THEN 1 ELSE 0 END) AS likes_missing,
            SUM(CASE WHEN comments_count IS NULL THEN 1 ELSE 0 END) AS comments_missing,
            MIN(observed_at) AS first_at,
            MAX(observed_at) AS last_at FROM post_observations""").fetchone()
        total_post_rows = totals["posts_in_raw_pages"]
        detail = {
            "source": str(root),
            "raw": {
                "page_receipts": totals["pages"],
                "post_occurrences": total_post_rows,
                "distinct_post_ids": len(unique_ids),
                "post_ids_table": raw.execute("SELECT COUNT(*) FROM post_ids").fetchone()[0],
                "observations_table": snapshot["total"],
                "first_raw_saved_at_kst": min(raw_times) if raw_times else None,
                "last_raw_saved_at_kst": max(raw_times) if raw_times else None,
                "by_api": {lane: dict(by_lane[lane]) for lane in ("A", "B", "C")},
                "raw_account_fields": ["id", "username", "followers_count", "media_count"],
                "raw_post_field_coverage": {
                    field: {
                        "non_null": present[field],
                        "total": total_post_rows,
                        "percent": round(100 * present[field] / total_post_rows, 1)
                        if total_post_rows else 0,
                    } for field in POST_FIELDS
                },
                "additional_raw_post_keys": sorted(field_names - set(POST_FIELDS)),
                "tables": {name: columns(raw, name)
                           for name in ("pages", "post_ids", "post_observations")},
            },
            "observations": {
                "count": snapshot["total"],
                "view_count_missing_null": snapshot["view_missing"] or 0,
                "view_count_real_zero": snapshot["view_zero"] or 0,
                "like_count_missing_null": snapshot["likes_missing"] or 0,
                "comments_count_missing_null": snapshot["comments_missing"] or 0,
                "first_observed_at": snapshot["first_at"],
                "last_observed_at": snapshot["last_at"],
            },
            "planner": {
                "total_accounts": len(accounts),
                "exhausted_accounts": sum(bool(row["finished"]) for row in accounts),
                "accounts_with_saved_cursor": sum(bool(row["cursor"]) for row in accounts),
                "saved_requests": planner.execute(
                    "SELECT COUNT(*) FROM jobs WHERE state='done'").fetchone()[0],
                "reserved_requests": planner.execute(
                    "SELECT COUNT(*) FROM jobs WHERE state='reserved'").fetchone()[0],
                "schedule": ({
                    "round": progress["round_no"],
                    "current_lane": ("A", "B", "C")[progress["lane_index"]],
                    "next_account_ordinal": progress["ordinal"],
                    "slot_collected_posts": progress["slot_count"],
                    "ready_at_kst": kst(progress["ready_at"]),
                } if progress else None),
                "lanes": [
                    {"lane": row["lane"], "usage_percent": row["usage"],
                     "halted": bool(row["halted"]),
                     "ready_at_kst": kst(row["ready_at"])}
                    for row in lanes
                ],
                "tables": {name: columns(planner, name)
                           for name in ("accounts", "jobs", "lanes", "schedule_state")},
            },
        }
        if include_details:
            detail["accounts"] = [
                {
                    "ordinal": a["ordinal"],
                    **account_profile.get(a["ordinal"], {}),
                    "raw_pages": by_account[a["ordinal"]]["pages"],
                    "raw_post_occurrences": by_account[a["ordinal"]]["posts"],
                    "finished": bool(a["finished"]),
                    "cursor_saved": bool(a["cursor"]),
                } for a in accounts
            ]
        if sample_count:
            detail["samples"] = samples
        planner.execute("COMMIT")
        raw.execute("COMMIT")
        return detail


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--workspace", required=True)
    parser.add_argument("--details", action="store_true")
    parser.add_argument("--samples", type=int, default=0)
    args = parser.parse_args()
    if not 0 <= args.samples <= 10:
        parser.error("--samples must be between 0 and 10")
    print(json.dumps(report(args.workspace, args.details, args.samples),
                     ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
