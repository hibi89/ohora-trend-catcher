"""Read-only P50 v2 coverage by media type and account. No API calls or DB writes."""
import argparse
from collections import Counter, defaultdict
from contextlib import closing
import json
from pathlib import Path
import sqlite3


def analyze(workspace):
    root = Path(workspace)
    if root.name.lower() != "p50_v2":
        raise ValueError("Expected dedicated p50_v2 workspace")
    dbpath = root / "raw.sqlite"
    if not dbpath.is_file():
        raise FileNotFoundError(dbpath)
    with closing(sqlite3.connect(dbpath.resolve().as_uri() + "?mode=ro", uri=True)) as db:
        db.row_factory = sqlite3.Row
        db.execute("PRAGMA query_only=ON")
        by_type = defaultdict(Counter)
        by_account = defaultdict(Counter)
        by_product = defaultdict(Counter)
        accounts = {}
        for row in db.execute("SELECT ordinal,response_json FROM pages ORDER BY rowid"):
            response = json.loads(row["response_json"])
            ordinal = row["ordinal"]
            accounts[ordinal] = response.get("username")
            for post in (response.get("media") or {}).get("data", []):
                media_type = post.get("media_type") or "UNKNOWN"
                product = post.get("media_product_type") or "UNKNOWN"
                is_reels = product.upper() == "REELS"
                for group in (by_type[media_type], by_product[product], by_account[ordinal]):
                    group["posts"] += 1
                    group["reels"] += int(is_reels)
                    for field in ("like_count", "comments_count", "view_count", "media_url"):
                        if post.get(field) is None:
                            group[field + "_missing"] += 1
                        else:
                            group[field + "_present"] += 1
                    if is_reels:
                        group["reels_view_present"] += int(post.get("view_count") is not None)
                        group["reels_view_missing"] += int(post.get("view_count") is None)
        def output(group):
            n = group["posts"]
            reels = group["reels"]
            return {
                "posts": n,
                "reels": reels,
                "likes_present": group["like_count_present"],
                "likes_missing": group["like_count_missing"],
                "likes_coverage_pct": round(100 * group["like_count_present"] / n, 1) if n else 0,
                "views_present": group["view_count_present"],
                "views_missing": group["view_count_missing"],
                "reels_views_present": group["reels_view_present"],
                "reels_views_missing": group["reels_view_missing"],
                "reels_view_coverage_pct": round(100 * group["reels_view_present"] / reels, 1) if reels else None,
                "media_url_present": group["media_url_present"],
                "media_url_missing": group["media_url_missing"],
            }
        return {
            "scope": "All RAW pages in existing SQLite; counts are post occurrences, not necessarily unique post IDs",
            "by_media_type": {k: output(v) for k, v in sorted(by_type.items())},
            "by_media_product_type": {k: output(v) for k, v in sorted(by_product.items())},
            "by_account": [
                {"ordinal": ordinal, "username": accounts.get(ordinal), **output(stats)}
                for ordinal, stats in sorted(by_account.items())
            ],
            "notes": [
                "NULL means the API did not return a value, not zero.",
                "REELS coverage is computed using media_product_type=REELS only.",
                "No country attribution is made without a verified roster mapping.",
                "This report does not determine why Meta omitted likes or views.",
            ],
        }


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--workspace", required=True)
    p.add_argument("--accounts", action="store_true", help="Include all per-account breakdowns")
    args = p.parse_args()
    result = analyze(args.workspace)
    if not args.accounts:
        del result["by_account"]
    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
