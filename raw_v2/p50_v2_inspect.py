"""Read-only local inspection of P50 raw paging and planner progress.

No Meta calls, no tokens, no database writes. Does not print cursor values.
"""
import argparse
import json
from pathlib import Path
import sqlite3

def main():
    ap=argparse.ArgumentParser()
    ap.add_argument("--workspace",required=True)
    ap.add_argument("--ordinal",type=int,default=1)
    a=ap.parse_args()
    root=Path(a.workspace)
    if not 1<=a.ordinal<=50:
        raise SystemExit("ordinal must be 1..50")
    if not (root/"raw.sqlite").is_file() or not (root/"planner.sqlite").is_file():
        raise SystemExit("Missing existing P50 v2 database; no files created")
    with sqlite3.connect(root/"raw.sqlite") as raw, sqlite3.connect(root/"planner.sqlite") as plan:
        record=raw.execute("""SELECT cycle,lane,response_json,created_at
            FROM pages WHERE ordinal=? ORDER BY rowid DESC LIMIT 1""",(a.ordinal,)).fetchone()
        account=plan.execute("""SELECT cursor,finished,finished_reason
            FROM accounts WHERE ordinal=?""",(a.ordinal,)).fetchone()
        state=plan.execute("""SELECT round_no,lane_index,ordinal,slot_count,ready_at
            FROM schedule_state WHERE id=1""").fetchone()
        if record is None or account is None:
            raise SystemExit("No saved page for account")
        body=json.loads(record[2])
        media=body.get("media") or {}
        paging=media.get("paging") or {}
        after=(paging.get("cursors") or {}).get("after")
        posts=media.get("data") or []
        print(json.dumps({
            "ordinal":a.ordinal,
            "latest_page":{"cycle":record[0],"lane":record[1],
                "saved_at":record[3],"post_count":len(posts),
                "has_after_cursor":bool(after),"has_next_url":bool(paging.get("next")),
                "media_count":body.get("media_count")},
            "planner":{"cursor_saved":bool(account[0]),
                "finished":bool(account[1]),"finished_reason":account[2]},
            "schedule":{"round":state[0],"lane":("A","B","C")[state[1]],
                "ordinal":state[2],"slot_count":state[3],
                "waiting":bool(state[4])} if state else None,
        },ensure_ascii=False,indent=2))
if __name__=="__main__":
    main()
