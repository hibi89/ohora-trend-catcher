"""Standalone P50 live collector: 50 accounts, 8 posts per lane, 20 cycles.

Explicit opt-in; never touches old P50 stores. Fail-closed on ambiguous HTTP.
Run from raw_v2 with --source, --workspace, --credentials-dir, --live,
--confirm-owner-approval and --max-requests.
"""
import argparse
from datetime import datetime, timezone
import json
from pathlib import Path
import re
import sqlite3
from urllib.parse import urlencode
from urllib.request import Request
from urllib.error import HTTPError

from p50_cycle_engine_v2 import CyclePlanner, MAX_USAGE
from three_api_env_loader import load_three_api_environment
from three_api_manual_pilot import _credentials
from three_api_graph_http import make_strict_sender
from three_api_p50_roster import load_p50_roster

FIELDS = ("id,caption,media_type,media_product_type,media_url,permalink,"
          "timestamp,like_count,comments_count,thumbnail_url,view_count")
CURSOR = re.compile(r"^[A-Za-z0-9_=-]{1,512}$")


def usage_percent(headers):
    found = []
    for key in ("x-app-usage", "x-business-use-case-usage"):
        raw = headers.get(key)
        if raw is None:
            continue
        obj = json.loads(raw)
        if isinstance(obj, dict):
            values = [obj] if key == "x-app-usage" else list(obj.values())
            for value in values:
                if isinstance(value, list):
                    values2 = value
                else:
                    values2 = [value]
                for entry in values2:
                    if isinstance(entry, dict):
                        for field in ("call_count", "total_cputime", "total_time", "estimated_time_to_regain_access"):
                            number = entry.get(field)
                            if type(number) in (int,float) and 0 <= number <= 100:
                                found.append(number)
    return max(found) if found else None


def fetch(lane, username, cursor, caller, token, version, sender):
    if cursor is not None and not CURSOR.fullmatch(cursor):
        raise ValueError("Invalid cursor")
    media = "media" + (f".after({cursor})" if cursor else "") + f".limit(8){{{FIELDS}}}"
    fields = f"business_discovery.username({username}){{id,username,followers_count,media_count,{media}}}"
    url = f"https://graph.facebook.com/{version}/{caller}?" + urlencode({"fields":fields})
    req = Request(url, headers={"Authorization":"Bearer "+token,"Accept":"application/json"})
    try:
        with sender(req, timeout=20) as response:
            status = response.status
            headers = {k.lower():v for k,v in response.headers.items()}
            payload = response.read(1024*1024+1)
    except HTTPError as exc:
        # No blind retry: even an HTTP error is an attempted request.
        raise RuntimeError("Meta HTTP rejected; reserved job retained") from None
    if status != 200 or len(payload) > 1024*1024:
        raise RuntimeError("Meta response rejected; reserved job retained")
    data = json.loads(payload)
    if not isinstance(data,dict) or "error" in data:
        raise RuntimeError("Meta response error; reserved job retained")
    bd = data.get("business_discovery")
    media = bd.get("media") if isinstance(bd,dict) else None
    if not isinstance(media,dict) or not isinstance(media.get("data"),list):
        raise RuntimeError("Missing media data; reserved job retained")
    posts = media["data"]
    if len(posts)>8 or any(not isinstance(p,dict) or not isinstance(p.get("id"),str) for p in posts):
        raise RuntimeError("Invalid posts; reserved job retained")
    paging = media.get("paging") or {}
    next_cursor = (paging.get("cursors") or {}).get("after")
    if next_cursor is not None and (not isinstance(next_cursor,str) or not CURSOR.fullmatch(next_cursor)):
        raise RuntimeError("Invalid response cursor; reserved job retained")
    dates = []
    for post in posts:
        if isinstance(post.get("timestamp"),str):
            dates.append(datetime.fromisoformat(post["timestamp"].replace("Z","+00:00")))
    oldest = min(dates) if dates else None
    return posts,next_cursor,oldest,usage_percent(headers),bd


def run(source,workspace,credentials_dir,*,max_requests,live,approved):
    accounts,digest = load_p50_roster(source)
    if not live or not approved:
        return {"status":"offline","accounts":len(accounts),"meta_calls":0}
    if not 1<=max_requests<=3000:
        raise ValueError("max_requests must be 1..3000")
    env=load_three_api_environment(credentials_dir, base_environment={})
    credentials=_credentials(env)
    version=env.get("P6_META_GRAPH_VERSION","v26.0")
    if not re.fullmatch(r"v\d{1,3}\.\d{1,2}",version):
        raise ValueError("Invalid Graph version")
    root=Path(workspace)
    if root.name.lower()!="p50_v2" or root.is_symlink():
        raise ValueError("Dedicated p50_v2 workspace required")
    planner=CyclePlanner(root/"planner.sqlite")
    # Never resend a reserved request automatically. Reconcile it manually.
    pending=planner.db.execute("SELECT COUNT(*) FROM jobs WHERE state='reserved'").fetchone()[0]
    if pending:
        planner.db.close()
        raise RuntimeError("Unreconciled reserved request; inspect RAW before resuming")
    raw=sqlite3.connect(root/"raw.sqlite",isolation_level=None)
    raw.execute("""CREATE TABLE IF NOT EXISTS pages(
      cycle INTEGER,lane TEXT,ordinal INTEGER,receipt_id TEXT UNIQUE,
      response_json TEXT NOT NULL,created_at TEXT NOT NULL,
      PRIMARY KEY(cycle,lane,ordinal))""")
    raw.execute("""CREATE TABLE IF NOT EXISTS post_ids(
      post_id TEXT PRIMARY KEY, first_cycle INTEGER NOT NULL,
      first_lane TEXT NOT NULL, first_ordinal INTEGER NOT NULL,
      published_at TEXT)""")
    sender=make_strict_sender()
    # Missing usage evidence is not proof of available quota. Start conservatively.
    # The operator must verify quotas for all three lanes before a live run.
    for lane in ("A","B","C"):
        state=planner.db.execute("SELECT usage FROM lanes WHERE lane=?", (lane,)).fetchone()
        if state["usage"] is None:
            planner.set_usage(lane,None)
    done=0
    try:
        for _ in range(max_requests):
            job=planner.next_job()
            if job is None:
                break
            lane=job["lane"]
            ordinal=job["ordinal"]
            ref,caller,token=credentials[lane]
            try:
                posts,cursor,oldest,usage,body=fetch(
                    lane,accounts[ordinal-1]["username"],job["cursor"],
                    caller,token,version,sender)
                # Missing usage means halt this lane after the committed page.
                receipt=f"p50v2-{job['cycle']}-{lane}-{ordinal}"
                raw.execute("BEGIN IMMEDIATE")
                raw.execute("INSERT INTO pages VALUES (?,?,?,?,?,?)",
                    (job["cycle"],lane,ordinal,receipt,
                     json.dumps(body,ensure_ascii=False),
                     datetime.now(timezone.utc).isoformat()))
                for post in posts:
                    published = post.get("timestamp")
                    raw.execute("""INSERT OR IGNORE INTO post_ids
                        (post_id,first_cycle,first_lane,first_ordinal,published_at)
                        VALUES (?,?,?,?,?)""",
                        (post["id"],job["cycle"],lane,ordinal,published))
                raw.execute("COMMIT")
                planner.acknowledge(job,receipt_id=receipt,posts=posts,
                    next_cursor=cursor,exhausted=cursor is None,
                    oldest_published_at=oldest)
                planner.set_usage(lane,usage)
                done+=1
            except BaseException:
                # Keep the reservation: unknown HTTP outcomes must never resend.
                planner.set_usage(lane,None)
                raise
        return {"status":"stopped","committed_requests":done,
                "summary":planner.summary()}
    finally:
        raw.close()
        planner.db.close()


def main():
    p=argparse.ArgumentParser()
    p.add_argument("--source",required=True)
    p.add_argument("--workspace",required=True)
    p.add_argument("--credentials-dir",required=True)
    p.add_argument("--max-requests",type=int,default=1)
    p.add_argument("--live",action="store_true")
    p.add_argument("--confirm-owner-approval",action="store_true")
    a=p.parse_args()
    try:
        result=run(a.source,a.workspace,a.credentials_dir,
                   max_requests=a.max_requests,live=a.live,
                   approved=a.confirm_owner_approval)
        print(json.dumps(result,ensure_ascii=False))
    except Exception as exc:
        print(json.dumps({"status":"safely_stopped","error_type":type(exc).__name__,
                          "message":"Inspect local planner; no automatic retry"}))
        raise SystemExit(2)


if __name__=="__main__":
    main()
