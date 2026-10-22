"""Standalone P50 live collector: manual start/stop, unlimited A/B/C rounds.

Explicit opt-in; never touches old P50 stores. Fail-closed on ambiguous HTTP.
Run from raw_v2 with --source, --workspace, --credentials-dir, --live,
--confirm-owner-approval and --max-requests.
"""
import argparse
from datetime import datetime, timezone, timedelta
import json
from pathlib import Path
import re
import signal
import threading
import sqlite3
import time
import traceback
from urllib.parse import urlencode
from urllib.request import Request
from urllib.error import HTTPError

from p50_cycle_engine_v2 import CyclePlanner, MAX_USAGE
from three_api_env_loader import load_three_api_environment
from three_api_manual_pilot import _credentials
from three_api_graph_http import make_strict_sender
from three_api_p50_roster import load_p50_roster
from three_api_p50_quota_recovery import probe_lane, quota_from_caller_headers
from three_api_usage import parse_usage

FIELDS = ("id,caption,media_type,media_product_type,media_url,permalink,"
          "timestamp,like_count,comments_count,thumbnail_url,view_count")
CURSOR = re.compile(r"^[A-Za-z0-9_+=/\\-]{1,2048}$")


def fetch(lane, username, cursor, caller, token, version, sender, limit=8):
    if not 1 <= limit <= 8:
        raise ValueError('Invalid limit')
    if cursor is not None and not CURSOR.fullmatch(cursor):
        raise ValueError("Invalid cursor")
    media = "media" + (f".after({cursor})" if cursor else "") + f".limit({limit}){{{FIELDS}}}"
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
    if len(posts)>limit or any(not isinstance(p,dict) or not isinstance(p.get("id"),str) for p in posts):
        raise RuntimeError("Invalid posts; reserved job retained")
    paging = media.get("paging") or {}
    # Nested Business Discovery sometimes omits paging.next despite returning
    # a valid after cursor. Follow a valid cursor and let an empty terminal page
    # establish exhaustion; never infer completion solely from missing next.
    after = (paging.get("cursors") or {}).get("after")
    if after is not None and (not isinstance(after,str) or not CURSOR.fullmatch(after)):
        raise RuntimeError("Invalid response cursor; reserved job retained")
    if paging.get("next") and not after:
        raise RuntimeError("Next page indicated without after cursor; reserved job retained")
    next_cursor = after if posts else None
    if next_cursor is not None and next_cursor == cursor:
        raise RuntimeError("Pagination cursor did not advance; reserved job retained")
    if len({post["id"] for post in posts}) != len(posts):
        raise RuntimeError("Duplicate post IDs within one page; reserved job retained")
    dates = []
    for post in posts:
        if isinstance(post.get("timestamp"),str):
            dates.append(datetime.fromisoformat(post["timestamp"].replace("Z","+00:00")))
    oldest = min(dates) if dates else None
    scoped=quota_from_caller_headers(headers,caller)
    # Some successful Business Discovery responses carry only app-level usage.
    # Preserve the stricter observed maximum when both headers are available.
    app_usage=parse_usage({'x-app-usage':headers['x-app-usage']}) if 'x-app-usage' in headers else None
    observed=max(v for v in (scoped,app_usage) if v is not None) if scoped is not None or app_usage is not None else None
    return posts,next_cursor,oldest,observed,bd,{'app': 'x-app-usage' in headers,'business': 'x-business-use-case-usage' in headers}


def run(source,workspace,credentials_dir,*,max_requests=None,live,approved,verified_usage=None,stop_event=None):
    accounts,digest = load_p50_roster(source)
    if not live or not approved:
        return {"status":"offline","accounts":len(accounts),"meta_calls":0}
    if max_requests is not None and not 1<=max_requests<=3000:
        raise ValueError("max_requests must be 1..3000 when provided")
    env=load_three_api_environment(credentials_dir, base_environment=__import__("os").environ)
    credentials=_credentials(env)
    version=env.get("P6_META_GRAPH_VERSION","v26.0")
    if not re.fullmatch(r"v\d{1,3}\.\d{1,2}",version):
        raise ValueError("Invalid Graph version")
    root=Path(workspace)
    if root.name.lower()!="p50_v2" or root.is_symlink():
        raise ValueError("Dedicated p50_v2 workspace required")
    planner=CyclePlanner(root/"planner.sqlite")
    # A changed CSV order must never silently assign an old cursor to a
    # different Instagram account. Keep the fingerprint in the planner DB.
    planner.db.execute("""CREATE TABLE IF NOT EXISTS collection_identity(
        id INTEGER PRIMARY KEY CHECK(id=1),
        roster_sha256 TEXT NOT NULL)""")
    known=planner.db.execute(
        "SELECT roster_sha256 FROM collection_identity WHERE id=1").fetchone()
    if known is not None and known["roster_sha256"] != digest:
        planner.db.close()
        raise RuntimeError("P50 roster has changed; refusing to reuse saved cursors")
    if known is None:
        planner.db.execute("INSERT INTO collection_identity(id,roster_sha256) VALUES (1,?)",
                           (digest,))
    raw=sqlite3.connect(root/"raw.sqlite",isolation_level=None)
    raw.execute("""CREATE TABLE IF NOT EXISTS pages(
      cycle INTEGER,lane TEXT,ordinal INTEGER,receipt_id TEXT UNIQUE,
      response_json TEXT NOT NULL,created_at TEXT NOT NULL,
      PRIMARY KEY(cycle,lane,ordinal))""")
    raw.execute("""CREATE TABLE IF NOT EXISTS post_ids(
      post_id TEXT PRIMARY KEY, first_cycle INTEGER NOT NULL,
      first_lane TEXT NOT NULL, first_ordinal INTEGER NOT NULL,
      published_at TEXT)""")
    raw.execute("""CREATE TABLE IF NOT EXISTS post_observations(
      receipt_id TEXT NOT NULL, post_id TEXT NOT NULL,
      ordinal INTEGER NOT NULL, lane TEXT NOT NULL,
      observed_at TEXT NOT NULL, published_at TEXT,
      media_type TEXT, media_product_type TEXT,
      like_count INTEGER, comments_count INTEGER, view_count INTEGER,
      permalink TEXT, PRIMARY KEY(receipt_id,post_id))""")
    recovered=planner.reconcile_saved_receipts(raw)
    reopened=planner.reopen_false_exhausted_accounts(raw)
    if reopened:
        print(json.dumps({"event":"restored_after_cursor","accounts":reopened}),flush=True)
    if planner.summary()['reserved_requests']:
        raw.close(); planner.db.close()
        raise RuntimeError('Unreconciled HTTP attempt; manual review required')
    sender=make_strict_sender()
    done=0
    stop_event = stop_event if stop_event is not None else threading.Event()
    try:
        # Verify all three independent API quotas on every manual start.
        # A quota hold does not permit jumping ahead to B or C.
        for candidate in ("A","B","C"):
            observation=probe_lane(candidate,env,sender=sender)
            planner.set_usage(candidate,observation["usage_percent"]
                              if observation["quota_verified"] else None)
            print(json.dumps({"event":"quota_probe","lane":candidate,
                              "usage":observation["usage_percent"],
                              "verified":observation["quota_verified"]}),flush=True)
        while not stop_event.is_set() and (max_requests is None or done < max_requests):
            job=planner.next_job()
            if job is None:
                pause=planner.pause()
                reason=pause["reason"]
                if reason == "complete":
                    return {"status":"all_accounts_exhausted",
                            "recovered_requests":recovered,"committed_requests":done,
                            "summary":planner.summary()}
                if reason not in ("round_wait","quota_hold") or not pause.get("until"):
                    raise RuntimeError(f"Scheduler cannot safely proceed: {reason}")
                until=datetime.fromisoformat(pause["until"])
                seconds=max(0,(until-datetime.now(timezone.utc)).total_seconds())
                print(json.dumps({"status":reason,"wait_seconds":round(seconds),
                                  "committed":done}),flush=True)
                if stop_event.wait(seconds):
                    break
                # 63m barrier: refresh all lanes before starting the next A pass.
                # Quota stop: refresh only the blocked lane, then resume its slot.
                candidates=("A","B","C") if reason=="round_wait" else (pause["lane"],)
                for candidate in candidates:
                    if stop_event.is_set():
                        break
                    observation=probe_lane(candidate,env,sender=sender)
                    planner.set_usage(candidate,observation["usage_percent"]
                                      if observation["quota_verified"] else None)
                    print(json.dumps({"event":"quota_probe","lane":candidate,
                                      "usage":observation["usage_percent"],
                                      "verified":observation["quota_verified"]}),flush=True)
                continue
            lane=job["lane"]
            ordinal=job["ordinal"]
            ref,caller,token=credentials[lane]
            try:
                posts,cursor,oldest,usage,body,header_presence=fetch(
                    lane,accounts[ordinal-1]["username"],job["cursor"],
                    caller,token,version,sender,limit=job['limit'])
                # Missing usage means halt this lane after the committed page.
                receipt=f"p50v2-{job['cycle']}-{lane}-{ordinal}"
                observed_at=datetime.now(timezone(timedelta(hours=9))).isoformat()
                raw.execute("BEGIN IMMEDIATE")
                raw.execute("INSERT INTO pages VALUES (?,?,?,?,?,?)",
                    (job["cycle"],lane,ordinal,receipt,
                     json.dumps(body,ensure_ascii=False),
                     datetime.now(timezone.utc).isoformat()))
                for post in posts:
                    published = post.get("timestamp")
                    raw.execute("""INSERT OR IGNORE INTO post_observations
                        (receipt_id,post_id,ordinal,lane,observed_at,published_at,
                         media_type,media_product_type,like_count,comments_count,
                         view_count,permalink) VALUES (?,?,?,?,?,?,?,?,?,?,?,?)""",
                        (receipt,post["id"],ordinal,lane,observed_at,published,
                         post.get("media_type"),post.get("media_product_type"),
                         post.get("like_count"),post.get("comments_count"),
                         post.get("view_count"),post.get("permalink")))
                    raw.execute("""INSERT OR IGNORE INTO post_ids
                        (post_id,first_cycle,first_lane,first_ordinal,published_at)
                        VALUES (?,?,?,?,?)""",
                        (post["id"],job["cycle"],lane,ordinal,published))
                raw.execute("COMMIT")
                planner.acknowledge(job,receipt_id=receipt,posts=posts,
                    next_cursor=cursor,exhausted=cursor is None,
                    oldest_published_at=oldest)
                # A successful page with no utilization header must not silently
                # disable a healthy lane: verify with a scoped caller probe.
                if usage is None:
                    observation=probe_lane(lane,env,sender=sender)
                    usage=observation["usage_percent"] if observation["quota_verified"] else None
                    print(json.dumps({"event":"quota_after_page","lane":lane,"header_presence":header_presence,"verified":observation["quota_verified"],"usage":usage}),flush=True)
                planner.set_usage(lane,usage)
                done+=1
                print(json.dumps({"event":"page_saved","lane":lane,"round":job["round"],"cycle":job["cycle"],"ordinal":ordinal,"posts":len(posts),"usage":usage,"receipt":receipt}),flush=True)
            except BaseException:
                # Keep the reservation: unknown HTTP outcomes must never resend.
                planner.set_usage(lane,None)
                raise
        return {"status":"operator_stopped" if stop_event.is_set() else "request_limit_reached",
                "recovered_requests":recovered,"committed_requests":done,
                "summary":planner.summary()}
    finally:
        raw.close()
        planner.db.close()


def main():
    p=argparse.ArgumentParser()
    p.add_argument("--source",required=True)
    p.add_argument("--workspace",required=True)
    p.add_argument("--credentials-dir",required=True)
    p.add_argument("--max-requests",type=int,default=None)
    p.add_argument("--verified-usage-a",type=int)
    p.add_argument("--verified-usage-b",type=int)
    p.add_argument("--verified-usage-c",type=int)
    p.add_argument("--live",action="store_true")
    p.add_argument("--confirm-owner-approval",action="store_true")
    a=p.parse_args()
    stopping = threading.Event()
    # Ctrl+C requests a graceful stop, retaining all committed page cursors.
    # An HTTP request already in flight is allowed to finish and checkpoint.
    def request_stop(signum, frame):
        stopping.set()
    signal.signal(signal.SIGINT, request_stop)
    if hasattr(signal, "SIGTERM"):
        signal.signal(signal.SIGTERM, request_stop)
    try:
        result=run(a.source,a.workspace,a.credentials_dir,
                   max_requests=a.max_requests,live=a.live,
                   approved=a.confirm_owner_approval,stop_event=stopping,
                   verified_usage={lane:value for lane,value in (
                       ("A",a.verified_usage_a),("B",a.verified_usage_b),
                       ("C",a.verified_usage_c)) if value is not None})
        print(json.dumps(result,ensure_ascii=False))
    except Exception as exc:
        frames=traceback.extract_tb(exc.__traceback__)
        own=[frame for frame in frames if frame.filename.replace("\\","/").endswith(("/p50_v2_collector.py","/p50_cycle_engine_v2.py","/three_api_p50_quota_recovery.py"))]
        location=own[-1] if own else None
        print(json.dumps({"status":"safely_stopped","error_type":type(exc).__name__,
                          "error_location":location.name if location else None,
                          "error_line":location.lineno if location else None,
                          "message":"Inspect local planner; no automatic retry"}))
        raise SystemExit(2)


if __name__=="__main__":
    main()
