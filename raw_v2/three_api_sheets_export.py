"""Read-only, incremental P6 RAW export for downstream n8n / Google Sheets.

Never uses the Sheets API, never mutates SQLite and never owns a sync cursor.
The consumer persists the last acknowledged observation_id only AFTER its
Sheets upsert succeeds. Replaying a page is safe using observation_key.
"""
import argparse
import json
import sqlite3
from pathlib import Path


def export_observations(db_path, *, after_id=0, limit=500):
    if type(after_id) is not int or after_id < 0:
        raise ValueError("after_id must be a nonnegative integer")
    if type(limit) is not int or not 1 <= limit <= 5000:
        raise ValueError("limit must be 1..5000")
    path = Path(db_path).expanduser()
    if path.is_symlink():
        raise ValueError("Symlink RAW database forbidden")
    uri = path.resolve().as_uri() + "?mode=ro"
    db = sqlite3.connect(uri, uri=True)
    db.row_factory = sqlite3.Row
    try:
        # An immutable RAW receipt proves that the source response was saved.
        # JSON extraction is read-only; legacy observations without a receipt
        # are intentionally excluded from this P6 contract export.
        rows = db.execute(
            """SELECT o.id AS observation_id, o.post_id, o.observed_at,
                      o.run_id, o.request_id, o.like_count, o.comments_count,
                      o.like_present, o.comments_present,
                      p.account_key, p.timestamp AS published_at,
                      p.media_type, p.media_product_type, p.caption,
                      p.permalink, a.country, a.ig_id,
                      r.payload_json
               FROM post_observations o
               JOIN posts p ON p.post_id=o.post_id
               JOIN accounts a ON a.account_key=p.account_key
               JOIN three_api_raw_receipts r
                 ON json_extract(r.payload_json,'$.request_id')=o.request_id
                AND r.account_key=p.account_key
               WHERE o.id>?
               ORDER BY o.id LIMIT ?""",
            (after_id, limit),
        ).fetchall()
        # LIMIT applies after the join: a duplicate receipt could otherwise
        # consume a page slot and hide a later observation.
        ambiguous = db.execute(
            """SELECT o.id FROM post_observations o
               JOIN posts p ON p.post_id=o.post_id
               JOIN three_api_raw_receipts r
                 ON json_extract(r.payload_json,'$.request_id')=o.request_id
                AND r.account_key=p.account_key
               WHERE o.id>? GROUP BY o.id HAVING COUNT(*)>1 LIMIT 1""",
            (after_id,),
        ).fetchone()
        if ambiguous is not None:
            raise ValueError("Ambiguous RAW receipts for observation")
        # Do not advance the cursor past an observation without RAW evidence.
        # A receipt committed later must remain eligible for export.
        first_unverified = db.execute(
            """SELECT o.id FROM post_observations o
               LEFT JOIN posts p ON p.post_id=o.post_id
               LEFT JOIN accounts a ON a.account_key=p.account_key
               WHERE o.id>? AND (p.post_id IS NULL OR a.account_key IS NULL OR NOT EXISTS (
                   SELECT 1 FROM three_api_raw_receipts r
                   WHERE r.account_key=p.account_key
                     AND json_extract(r.payload_json,'$.request_id')=o.request_id
               ))
               ORDER BY o.id LIMIT 1""",
            (after_id,),
        ).fetchone()
        blocked_on_gap = first_unverified is not None
        if blocked_on_gap:
            rows = [row for row in rows
                    if row["observation_id"] < first_unverified["id"]]
        # The downstream upsert key must uniquely identify an observation.
        duplicate_key = db.execute(
            """SELECT o.request_id, o.post_id
               FROM post_observations o
               WHERE o.id>? AND (? IS NULL OR o.id<?)
               GROUP BY o.request_id, o.post_id
               HAVING COUNT(*)>1 LIMIT 1""",
            (after_id, first_unverified["id"] if blocked_on_gap else None,
             first_unverified["id"] if blocked_on_gap else None),
        ).fetchone()
        if duplicate_key is not None:
            raise ValueError("Duplicate observation_key in RAW observations")
        output = []
        seen_observation_ids = set()
        for row in rows:
            if row["observation_id"] in seen_observation_ids:
                raise ValueError("Ambiguous RAW receipts for observation")
            seen_observation_ids.add(row["observation_id"])
            capture = json.loads(row["payload_json"])
            if not isinstance(capture, dict) or not isinstance(capture.get("posts"), list):
                raise ValueError("Invalid RAW receipt posts structure")
            if any(not isinstance(p, dict) for p in capture["posts"]):
                raise ValueError("Invalid RAW receipt post entry")
            matches = [p for p in capture["posts"] if p.get("id") == row["post_id"]]
            if len(matches) != 1:
                raise ValueError("RAW receipt post identity mismatch")
            post = matches[0]
            field_statuses = capture.get("post_field_status")
            if field_statuses is None:
                field_statuses = {}
            if not isinstance(field_statuses, dict):
                raise ValueError("Invalid RAW field status structure")
            statuses = field_statuses.get(row["post_id"])
            if statuses is None:
                statuses = {}
            if not isinstance(statuses, dict):
                raise ValueError("Invalid RAW post field status")
            view_status = statuses.get("view_count")
            if view_status is None:
                view_status = ("absent" if "view_count" not in post else
                               "null" if post["view_count"] is None else "returned")
            if view_status not in ("returned", "absent", "null", "not_tested", "error"):
                raise ValueError("Invalid Reels view_count field status")
            if view_status == "returned" and (type(post.get("view_count")) is not int
                                              or post["view_count"] < 0):
                raise ValueError("RAW view_count status conflicts with value")
            output.append({
                "observation_key": row["request_id"] + ":" + row["post_id"],
                "observation_id": row["observation_id"],
                "account_key": row["account_key"],
                "account_id": row["ig_id"],
                "post_id": row["post_id"],
                "country": row["country"],
                "published_at": row["published_at"],
                "collected_at": row["observed_at"],
                "media_type": row["media_type"],
                "media_product_type": row["media_product_type"],
                "caption": row["caption"],
                "like_count": row["like_count"] if row["like_present"] else None,
                "comments_count": row["comments_count"] if row["comments_present"] else None,
                "reels_view_count": post.get("view_count") if view_status == "returned" else None,
                "reels_view_count_status": view_status,
                "post_url": row["permalink"],
                "collection_batch": row["run_id"],
                "request_id": row["request_id"],
            })
        return {
            "schema_version": "p6-sheets-export-v1",
            "after_id": after_id,
            "next_after_id": output[-1]["observation_id"] if output else after_id,
            "has_more": blocked_on_gap or len(rows) == limit,
            "rows": output,
        }
    finally:
        db.close()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--db", required=True, help="Path to P6 RAW SQLite file")
    parser.add_argument("--after-id", type=int, default=0)
    parser.add_argument("--limit", type=int, default=500)
    args = parser.parse_args()
    print(json.dumps(export_observations(args.db, after_id=args.after_id,
                                         limit=args.limit), ensure_ascii=False))


if __name__ == "__main__":
    main()
