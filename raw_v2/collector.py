#!/usr/bin/env python3
"""Opt-in OHORA RAW v2; independent of all legacy stores. Python 3.11+.

A slot may span manual invocations: up to 150 distinct accounts then 60-minute cooldown.
No scheduler is installed. No requests without --live.
The collector prioritizes unscanned accounts, writes an immutable sanitized API
response per subrequest, and uses separate latest/catchup/history checkpoints.
"""
from __future__ import annotations
import argparse
import csv
from datetime import datetime, timezone, timedelta
import hashlib
import json
import os
from pathlib import Path
import re
import sqlite3
import sys
import subprocess
import tempfile
import time
import uuid
from urllib.parse import urlencode
from urllib.request import Request, urlopen
from urllib.error import HTTPError, URLError
from contextlib import closing, contextmanager

ROOT = Path(__file__).resolve().parent.parent
BASE_PROFILE = 'id,username,followers_count,media_count'
EXTRA_PROFILE = 'ig_id,name,biography,website,profile_picture_url,follows_count'
MEDIA_FIELDS = ('id,username,caption,timestamp,permalink,media_type,media_product_type,'
                'media_url,thumbnail_url,like_count,comments_count,'
                'children.limit(50){id,media_type,media_url,thumbnail_url}')
PROFILE_FIELDS = ('id','ig_id','username','name','biography','website','profile_picture_url',
                  'followers_count','follows_count','media_count')
POST_FIELDS = ('id','username','caption','timestamp','permalink','media_type',
               'media_product_type','media_url','thumbnail_url','like_count','comments_count',
               'shortcode','view_count')
MEDIA_FIELDS_OUT = ('id','media_type','media_url','thumbnail_url')
STOP_ERRORS = {'4','17','32','613','10','102','104','190','200'}
SCHEMA_VERSION = 'raw-v2.1'
LOG_COLS = ('run_id,request_id,account_key,resource_type,parent_post_id,started_at,finished_at,'
            'api_version,requested_fields,requested_post_limit,requested_since,requested_until,'
            'page_index,attempt_no,http_status,status,records_received,has_next_page,stop_reason,'
            'error_code,error_subcode,error_message,raw_response_path').split(',')


def now():
    return datetime.now(timezone.utc).isoformat(timespec='seconds')


def redact(obj, token=''):
    """Redact credentials also in paging URL and free-form error message."""
    if isinstance(obj, dict):
        return {k: ('[REDACTED]' if re.search(r'(access_token|client_secret|app_secret|authorization)',k,re.I)
                    else redact(v,token)) for k,v in obj.items()}
    if isinstance(obj, list):
        return [redact(x,token) for x in obj]
    if isinstance(obj, str):
        s = obj.replace(token,'[REDACTED]') if token else obj
        return re.sub(r'(?i)(access_token|appsecret_proof|client_secret)=([^&\s"\']+)',r'\1=[REDACTED]',s)
    return obj


def load_accounts(path):
    raw = Path(path).read_bytes()
    with Path(path).open(encoding='utf-8-sig',newline='') as f:
        reader = csv.DictReader(f)
        if not {'account_key','username','country'}.issubset(reader.fieldnames or []):
            raise ValueError('official CSV must have account_key,username,country')
        rows = list(reader)
    if len(rows) != 294 or [r['country'].strip() for r in rows] != ['KR']*100+['US']*101+['JP']*93:
        raise ValueError('official 294 rows / KR100 US101 JP93 order required')
    keys = [r['account_key'].strip() for r in rows]
    usernames = [r['username'].strip().lstrip('@') for r in rows]
    if len(set(keys)) != 294 or len(set(u.casefold() for u in usernames)) != 294:
        raise ValueError('duplicate official keys/usernames')
    if not all(re.fullmatch(r'[A-Za-z0-9._]{1,30}',u) for u in usernames):
        raise ValueError('invalid username')
    return [dict(key=keys[i],input_username=rows[i]['username'],username=usernames[i],
                 country=rows[i]['country'],input_profile_url=rows[i].get('profile_url') or '',ordinal=i+1)
            for i in range(294)], hashlib.sha256(raw).hexdigest()


def load_env(path):
    if not Path(path).is_file():
        raise ValueError('local credential file not found')
    settings={}
    for line in Path(path).read_text(encoding='utf-8-sig').splitlines():
        if '=' in line and not line.strip().startswith('#'):
            k,v=line.split('=',1); settings[k.strip()]=v.strip().strip('"\'')
    token=settings.get('META_ACCESS_TOKEN','')
    caller=settings.get('META_IG_USER_ID','')
    version=settings.get('META_GRAPH_VERSION','v26.0')
    if not token or not caller.isdigit() or not re.fullmatch(r'v\d+\.\d+',version):
        raise ValueError('META_ACCESS_TOKEN/META_IG_USER_ID/META_GRAPH_VERSION invalid or missing')
    return token,caller,version


def connect(path, accounts, source_sha):
    path=Path(path); path.parent.mkdir(parents=True,exist_ok=True)
    db=sqlite3.connect(path)
    db.row_factory=sqlite3.Row
    db.execute('PRAGMA journal_mode=WAL')
    db.execute('PRAGMA foreign_keys=ON')
    db.executescript('''
    CREATE TABLE IF NOT EXISTS config(k TEXT PRIMARY KEY,v TEXT NOT NULL);
    CREATE TABLE IF NOT EXISTS accounts(account_key TEXT PRIMARY KEY,ordinal INTEGER UNIQUE,
       input_username TEXT NOT NULL,username TEXT NOT NULL,country TEXT NOT NULL,input_profile_url TEXT NOT NULL,
       id TEXT,ig_id TEXT,name TEXT,biography TEXT,website TEXT,profile_picture_url TEXT,
       followers_count INTEGER,follows_count INTEGER,media_count INTEGER,collected_at TEXT,
       latest_id TEXT,oldest_time TEXT,newest_time TEXT,window_start TEXT,window_end TEXT,backfill_cursor TEXT,
       catchup_cursor TEXT,catchup_anchor TEXT,backfill_done INTEGER NOT NULL DEFAULT 0,
       last_front TEXT,last_history TEXT,attempts INTEGER NOT NULL DEFAULT 0,
       last_error TEXT,blocked INTEGER NOT NULL DEFAULT 0,last_attempt TEXT);
    CREATE TABLE IF NOT EXISTS posts(post_id TEXT PRIMARY KEY,account_key TEXT NOT NULL REFERENCES accounts(account_key),
       username TEXT,caption TEXT,timestamp TEXT,permalink TEXT,media_type TEXT,
       media_product_type TEXT,media_url TEXT,thumbnail_url TEXT,first_seen_run TEXT NOT NULL);
    CREATE TABLE IF NOT EXISTS post_media(media_id TEXT PRIMARY KEY,post_id TEXT NOT NULL REFERENCES posts(post_id),
       account_key TEXT NOT NULL,media_role TEXT,item_order INTEGER,media_type TEXT,
       media_url TEXT,thumbnail_url TEXT,first_seen_run TEXT NOT NULL);
    CREATE TABLE IF NOT EXISTS post_observations(id INTEGER PRIMARY KEY,post_id TEXT NOT NULL REFERENCES posts(post_id),
       observed_at TEXT NOT NULL,run_id TEXT NOT NULL,like_count INTEGER,comments_count INTEGER,
       like_present INTEGER NOT NULL,comments_present INTEGER NOT NULL,request_id TEXT NOT NULL,
       UNIQUE(post_id,request_id));
    CREATE TABLE IF NOT EXISTS carousel_status(post_id TEXT PRIMARY KEY REFERENCES posts(post_id),
       account_key TEXT,child_rows INTEGER,has_next_page TEXT,coverage TEXT,last_request_id TEXT);
    CREATE TABLE IF NOT EXISTS account_observations(id INTEGER PRIMARY KEY,account_key TEXT NOT NULL,
       observed_at TEXT NOT NULL,run_id TEXT NOT NULL,followers_count INTEGER,follows_count INTEGER,
       media_count INTEGER,request_id TEXT NOT NULL,UNIQUE(account_key,request_id));
    CREATE TABLE IF NOT EXISTS slots(slot_id INTEGER PRIMARY KEY AUTOINCREMENT,started_at TEXT NOT NULL,
       finished_at TEXT,ready_at TEXT,status TEXT NOT NULL,reason TEXT);
    CREATE TABLE IF NOT EXISTS slot_accounts(slot_id INTEGER NOT NULL REFERENCES slots(slot_id),
       account_key TEXT NOT NULL REFERENCES accounts(account_key),request_id TEXT NOT NULL,
       PRIMARY KEY(slot_id,account_key));
    CREATE TABLE IF NOT EXISTS runs(run_id TEXT PRIMARY KEY,started_at TEXT,finished_at TEXT,api_version TEXT,mode TEXT,
       status TEXT,requests INTEGER DEFAULT 0,successes INTEGER DEFAULT 0,failures INTEGER DEFAULT 0,
       usage_max REAL);
    CREATE TABLE IF NOT EXISTS request_log(run_id TEXT,request_id TEXT PRIMARY KEY,account_key TEXT,
       phase TEXT,started_at TEXT,finished_at TEXT,http_status INTEGER,status TEXT,records_received INTEGER,
       has_next_page TEXT,stop_reason TEXT,error_code TEXT,error_subcode TEXT,error_message TEXT,
       raw_response_path TEXT,requested_fields TEXT,usage_json TEXT);
    CREATE TABLE IF NOT EXISTS field_observations(run_id TEXT,request_id TEXT,account_key TEXT,resource_type TEXT,
       media_type TEXT,field_name TEXT,status TEXT,item_id TEXT,PRIMARY KEY(request_id,resource_type,media_type,field_name,item_id));
    CREATE INDEX IF NOT EXISTS ix_posts_account ON posts(account_key);
    CREATE INDEX IF NOT EXISTS ix_obs_post ON post_observations(post_id,observed_at);
    ''')
    cfg=dict(db.execute('SELECT k,v FROM config'))
    if cfg and (cfg.get('source_sha')!=source_sha or cfg.get('schema')!=SCHEMA_VERSION):
        db.close();raise ValueError('Source hash or schema changed; create separate output directory')
    with db:
        if not cfg:
            db.executemany('INSERT INTO config(k,v) VALUES (?,?)',[('source_sha',source_sha),('schema',SCHEMA_VERSION)])
            db.executemany('INSERT INTO accounts(account_key,ordinal,input_username,username,country,input_profile_url) '
                           'VALUES (:key,:ordinal,:input_username,:username,:country,:input_profile_url)',accounts)
        else:
            stored=[(x['account_key'],x['ordinal'],x['input_username'],x['country']) for x in db.execute('SELECT * FROM accounts ORDER BY ordinal')]
            expected=[(a['key'],a['ordinal'],a['input_username'],a['country']) for a in accounts]
            if stored!=expected: raise ValueError('official source list changed')
    return db


def make_op(account,caller,phase,limit=5,extended=False):
    fields=BASE_PROFILE + (','+EXTRA_PROFILE if extended else '')
    media='media.limit(%d)'%limit
    cursor = account['catchup_cursor'] if phase=='catchup' else account['backfill_cursor'] if phase=='history' else None
    if cursor:
        if not re.fullmatch(r'[A-Za-z0-9_=+/\-]{1,4096}',cursor):raise ValueError('unsafe cursor')
        media+='.after('+cursor+')'
    f='business_discovery.username(%s){%s,%s{%s}}'%(account['username'],fields,media,MEDIA_FIELDS)
    return {'method':'GET','relative_url':caller+'?'+urlencode({'fields':f})}


def select_tasks(db,mode,max_tasks,cutoff_days=365,refresh_hours=24):
    """Fair one-request/account queue. In balanced mode: latest >= once/24h, otherwise history.

    When API headroom is too low for 294 requests, accounts with older last-attempts
    have priority. Catch-up gaps retain their own cursor, never silently skipped.
    """
    todo=[]
    threshold=datetime.now(timezone.utc)-timedelta(hours=refresh_hours)
    for a in db.execute('SELECT * FROM accounts WHERE blocked=0 ORDER BY ordinal'):
        r=dict(a)
        latest_due=not r['last_front'] or (parse_date(r['last_front']) or threshold)<=threshold
        if r['catchup_cursor']:
            phase='catchup'; priority=r['last_attempt'] or ''
        elif mode=='front':
            phase='front';priority=r['last_attempt'] or ''
        elif mode=='history':
            if r['backfill_done']:continue
            phase='history' if r['latest_id'] else 'front'
            priority=r['last_attempt'] or ''
        elif not r['latest_id'] or latest_due:
            phase='front';priority=r['last_attempt'] or ''
        elif not r['backfill_done']:
            phase='history';priority=r['last_attempt'] or ''
        else:
            continue  # recent front already observed, historical target complete
        todo.append((priority,r['ordinal'],phase,r))
    todo.sort(key=lambda x:(x[0],x[1]))
    return [(phase,r) for _,_,phase,r in todo[:max_tasks]]


def request_batch(version,token,ops):
    data=urlencode({'access_token':token,'batch':json.dumps(ops),'include_headers':'true'}).encode()
    req=Request('https://graph.facebook.com/'+version,data=data,method='POST',headers={'Content-Type':'application/x-www-form-urlencoded'})
    try:
        with urlopen(req,timeout=120) as response: return response.status,json.loads(response.read()),response.headers
    except HTTPError as e:
        try: return e.code,json.loads(e.read(1048576)),e.headers
        except ValueError: return e.code,{},e.headers


def usage(headers):
    if isinstance(headers,list):headers={str(x.get('name','')).lower():x.get('value') for x in headers if isinstance(x,dict)}
    elif hasattr(headers,'items'):headers={str(k).lower():v for k,v in headers.items()}
    else:return None
    try: val=json.loads(headers.get('x-app-usage') or '{}')
    except (ValueError,TypeError):return None
    x=val.get('total_time') if isinstance(val,dict) else None
    return float(x) if isinstance(x,(int,float)) else None


def record_fields(db,run,req,key,kind,obj,media_type='',item_id='account',extended=False):
    fields=PROFILE_FIELDS if kind=='account' else POST_FIELDS if kind=='posts' else MEDIA_FIELDS_OUT
    requested=(set(BASE_PROFILE.split(',')) | (set(EXTRA_PROFILE.split(',')) if extended else set())) if kind=='account' else set(POST_FIELDS)-{'shortcode','view_count'} if kind=='posts' else set(MEDIA_FIELDS_OUT)
    for f in fields:
        status=('not_tested' if f not in requested else 'returned' if f in obj and obj[f] is not None else 'null' if f in obj else 'absent')
        db.execute('INSERT OR IGNORE INTO field_observations VALUES (?,?,?,?,?,?,?,?)',
                   (run,req,key,kind,media_type,f,status,item_id))


def parse_date(value):
    if not isinstance(value,str):return None
    try:return datetime.fromisoformat(value.replace('Z','+00:00')).astimezone(timezone.utc)
    except (ValueError,TypeError):return None


def insert_success(db,account,phase,payload,run,req,ts,history_days,extended=False,window_days=7):
    discovery=payload.get('business_discovery')
    if not isinstance(discovery,dict):raise ValueError('missing business_discovery')
    media=discovery.get('media')
    if not isinstance(media,dict) or not isinstance(media.get('data'),list):raise ValueError('missing media.data')
    posts=media['data']
    if len(posts)>5:raise ValueError('more than requested five posts')
    key=account['account_key']
    old_ids={p['post_id'] for p in db.execute('SELECT post_id FROM posts WHERE account_key=?',(key,))}
    old_latest=account['latest_id']
    after=((media.get('paging') or {}).get('cursors') or {}).get('after') if isinstance(media.get('paging'),dict) else None
    if after is not None and (not isinstance(after,str) or not re.fullmatch(r'[A-Za-z0-9_=+/\-]{1,4096}',after)):
        raise ValueError('invalid paging cursor')
    before=account['catchup_cursor'] if phase=='catchup' else account['backfill_cursor'] if phase=='history' else None
    if after and before and after==before:raise ValueError('unchanged paging cursor')
    if len(posts)==5 and not after and discovery.get('media_count',0)>5:
        raise ValueError('full page but missing cursor: completeness unknown')
    oldest=account['oldest_time'];newest=account['newest_time'];new_post_count=0
    seen_ids=[]
    with db:
        record_fields(db,run,req,key,'account',discovery,extended=extended)
        db.execute('INSERT OR IGNORE INTO account_observations(account_key,observed_at,run_id,followers_count,follows_count,media_count,request_id) VALUES (?,?,?,?,?,?,?)',
                   (key,ts,run,discovery.get('followers_count'),discovery.get('follows_count'),discovery.get('media_count'),req))
        for p in posts:
            if not isinstance(p,dict) or not str(p.get('id') or '').isdigit():raise ValueError('malformed post ID')
            pid=str(p['id']);seen_ids.append(pid)
            owner=db.execute('SELECT account_key FROM posts WHERE post_id=?',(pid,)).fetchone()
            if owner and owner[0]!=key:raise ValueError('post ID belongs to another account; payload/account mismatch')
            if seen_ids.count(pid)>1:raise ValueError('duplicate post ID in one page')
            if pid not in old_ids:
                db.execute('INSERT OR IGNORE INTO posts(post_id,account_key,username,caption,timestamp,permalink,media_type,media_product_type,media_url,thumbnail_url,first_seen_run) VALUES (?,?,?,?,?,?,?,?,?,?,?)',
                    (pid,key,p.get('username'),p.get('caption'),p.get('timestamp'),p.get('permalink'),p.get('media_type'),p.get('media_product_type'),p.get('media_url'),p.get('thumbnail_url'),run))
                new_post_count+=1
                if p.get('media_type')=='CAROUSEL_ALBUM':
                    children=p.get('children',{}).get('data',[]) if isinstance(p.get('children'),dict) else []
                    if not isinstance(children,list):raise ValueError('bad carousel children')
                    child_paging=p['children'].get('paging') or {}
                    child_more=bool((child_paging.get('cursors') or {}).get('after')) if isinstance(child_paging,dict) else False
                    db.execute('INSERT OR REPLACE INTO carousel_status VALUES (?,?,?,?,?,?)',
                               (pid,key,len(children),'true' if child_more else 'unknown' if len(children)==50 else 'false',
                                'partial' if child_more or len(children)==50 else 'complete',req))
                    for n,child in enumerate(children,1):
                        if not isinstance(child,dict) or not str(child.get('id') or '').isdigit():raise ValueError('missing carousel child id')
                        db.execute('INSERT OR IGNORE INTO post_media VALUES (?,?,?,?,?,?,?,?,?)',
                            (str(child['id']),pid,key,'carousel_child',n,child.get('media_type'),child.get('media_url'),child.get('thumbnail_url'),run))
                        record_fields(db,run,req,key,'media',child,child.get('media_type') or '',str(child['id']))
                else:
                    db.execute('INSERT OR IGNORE INTO post_media VALUES (?,?,?,?,?,?,?,?,?)',
                               (pid,pid,key,'single',1,p.get('media_type'),p.get('media_url'),p.get('thumbnail_url'),run))
            db.execute('INSERT OR IGNORE INTO post_observations(post_id,observed_at,run_id,like_count,comments_count,like_present,comments_present,request_id) VALUES (?,?,?,?,?,?,?,?)',
                       (pid,ts,run,p.get('like_count'),p.get('comments_count'),int('like_count' in p and p['like_count'] is not None),
                        int('comments_count' in p and p['comments_count'] is not None),req))
            record_fields(db,run,req,key,'posts',p,p.get('media_type') or '',pid)
            dt=parse_date(p.get('timestamp'))
            if dt:
                normalized=dt.isoformat()
                if oldest is None or normalized<oldest:oldest=normalized
                if newest is None or normalized>newest:newest=normalized
        latest=old_latest;back=account['backfill_cursor'];catch=account['catchup_cursor'];anchor=account['catchup_anchor']
        done=account['backfill_done']
        if phase=='front':
            if seen_ids:latest=seen_ids[0]
            if old_latest and old_latest not in seen_ids and len(posts)==5 and after:
                catch=after;anchor=old_latest
            elif not old_latest:
                back=after  # first page provides historical continuation
            else:
                catch=None;anchor=None
        elif phase=='catchup':
            if anchor not in seen_ids and (not after or len(posts)<5):
                raise ValueError('latest gap has no boundary; do not mark complete')
            if anchor in seen_ids:catch=None;anchor=None
            else:catch=after
        else:
            back=after
            if not after or len(posts)<5:done=1
        target=(parse_date(ts) or datetime.now(timezone.utc))-timedelta(days=history_days)
        observation_day=(parse_date(ts) or datetime.now(timezone.utc)).date()
        delta_days=max(0,(observation_day-(parse_date(oldest).date() if oldest and parse_date(oldest) else observation_day)).days)
        bucket=delta_days//window_days
        window_end=(observation_day+timedelta(days=1-bucket*window_days)).isoformat()
        window_start=(observation_day+timedelta(days=1-(bucket+1)*window_days)).isoformat()
        if oldest and parse_date(oldest) and parse_date(oldest)<=target:done=1
        db.execute('UPDATE accounts SET id=?,ig_id=?,username=?,name=?,biography=?,website=?,profile_picture_url=?,followers_count=?,follows_count=?,media_count=?,collected_at=?,latest_id=?,oldest_time=?,newest_time=?,window_start=?,window_end=?,backfill_cursor=?,catchup_cursor=?,catchup_anchor=?,backfill_done=?,last_front=CASE WHEN ? IN (\'front\',\'catchup\') THEN ? ELSE last_front END,last_history=CASE WHEN ?=\'history\' THEN ? ELSE last_history END,attempts=attempts+1,last_attempt=?,last_error=NULL WHERE account_key=?',
                   (str(discovery['id']) if discovery.get('id') is not None else None,str(discovery['ig_id']) if discovery.get('ig_id') is not None else None,discovery.get('username') or account['username'],discovery.get('name'),discovery.get('biography'),discovery.get('website'),discovery.get('profile_picture_url'),discovery.get('followers_count'),discovery.get('follows_count'),discovery.get('media_count'),ts,latest,oldest,newest,window_start,window_end,back,catch,anchor,done,phase,ts,phase,ts,ts,key))
    return new_post_count,len(posts),bool(after)


def persist_response(base,run,req,body,token):
    folder=base/'runs'/run/'raw';folder.mkdir(parents=True,exist_ok=True)
    target=folder/(req+'.json')
    data=json.dumps(redact(body,token),ensure_ascii=False,indent=2).encode('utf-8')
    fd,tmp=tempfile.mkstemp(dir=folder,prefix='.raw-',suffix='.tmp')
    try:
        with os.fdopen(fd,'wb') as f:f.write(data);f.flush();os.fsync(f.fileno())
        os.replace(tmp,target)
    finally:
        if os.path.exists(tmp):os.unlink(tmp)
    return target


def export_run(db,base,run,source):
    out=base/'runs'/run;out.mkdir(parents=True,exist_ok=True)
    (out/'input_accounts.csv').write_bytes(Path(source).read_bytes())
    queries={
      'accounts.csv':("SELECT ? AS run_id,a.account_key,a.input_username,a.input_profile_url,a.country AS source_country,a.id,a.ig_id,a.username,a.name,a.biography,a.website,a.profile_picture_url,a.followers_count,a.follows_count,a.media_count,a.collected_at FROM accounts a WHERE a.account_key IN (SELECT account_key FROM account_observations WHERE run_id=?) ORDER BY ordinal",(run,run)),
      'posts.csv':("SELECT p.first_seen_run AS run_id,p.account_key,(SELECT id FROM accounts a WHERE a.account_key=p.account_key) AS ig_user_id,p.post_id AS id,p.username,p.caption,p.timestamp,p.permalink,p.media_type,p.media_product_type,p.media_url,p.thumbnail_url,o.like_count,o.comments_count,'' AS shortcode,'' AS view_count,p.first_seen_run AS first_seen_run,o.observed_at AS collected_at FROM posts p LEFT JOIN post_observations o ON o.post_id=p.post_id AND o.run_id=p.first_seen_run WHERE p.first_seen_run=? ORDER BY p.account_key,p.timestamp DESC",(run,)),
      'post_media.csv':("SELECT first_seen_run AS run_id,account_key,post_id,media_id AS id,media_role,item_order,media_type,media_url,thumbnail_url,(SELECT MIN(o.observed_at) FROM post_observations o WHERE o.post_id=post_media.post_id AND o.run_id=post_media.first_seen_run) AS collected_at FROM post_media WHERE first_seen_run=? ORDER BY account_key,post_id,item_order",(run,)),
      'collection_log.csv':("SELECT l.run_id,l.request_id,l.account_key,l.phase AS resource_type,'' AS parent_post_id,l.started_at,l.finished_at,r.api_version,l.requested_fields,5 AS requested_post_limit,'' AS requested_since,'' AS requested_until,1 AS page_index,1 AS attempt_no,l.http_status,l.status,l.records_received,l.has_next_page,l.stop_reason,l.error_code,l.error_subcode,l.error_message,l.raw_response_path FROM request_log l JOIN runs r ON r.run_id=l.run_id WHERE l.run_id=? ORDER BY l.request_id",(run,)),
      'like_missing_audit.csv':("SELECT o.post_id,p.account_key,p.media_type,p.media_product_type,p.timestamp AS post_timestamp,o.observed_at,o.request_id,CASE WHEN o.like_present=0 THEN 'missing_or_null' ELSE 'returned' END AS field_state FROM post_observations o JOIN posts p ON p.post_id=o.post_id WHERE o.run_id=? AND o.like_present=0 ORDER BY p.account_key,o.post_id",(run,)),
      'field_support.csv':("SELECT runs.api_version,resource_type,field_name,MAX(runs.started_at) AS checked_at,field_observations.status,COUNT(*) AS sample_count,'' AS error_code,media_type AS note FROM field_observations JOIN runs ON runs.run_id=field_observations.run_id WHERE field_observations.run_id=? GROUP BY resource_type,media_type,field_name,field_observations.status ORDER BY resource_type,field_name",(run,))}
    counts={}
    for name,(query,params) in queries.items():
        c=db.execute(query,params);names=[x[0] for x in c.description];rows=c.fetchall()
        with (out/name).open('w',encoding='utf-8-sig',newline='') as f:
            writer=csv.writer(f);writer.writerow(names);writer.writerows(rows)
        counts[name]=len(rows)
    db_run=db.execute('SELECT * FROM runs WHERE run_id=?',(run,)).fetchone()
    files={p.relative_to(out).as_posix():hashlib.sha256(p.read_bytes()).hexdigest() for p in out.rglob('*') if p.is_file() and p.name!='run_manifest.json'}
    result={'run_id':run,'created_at':now(),'schema':SCHEMA_VERSION,'input_accounts_sha256':dict(db.execute('SELECT k,v FROM config')).get('source_sha'),'run':dict(db_run),'file_sha256':files,'row_count':counts,
            'cumulative_posts':db.execute('SELECT COUNT(*) FROM posts').fetchone()[0],
            'cumulative_media':db.execute('SELECT COUNT(*) FROM post_media').fetchone()[0],
            'cumulative_accounts':db.execute('SELECT COUNT(DISTINCT account_key) FROM account_observations').fetchone()[0],
            'incomplete_carousels':db.execute("SELECT COUNT(*) FROM carousel_status WHERE coverage='partial'").fetchone()[0],
            'accounts_summary':[dict(r) for r in db.execute("SELECT a.account_key,a.ordinal,a.country,a.oldest_time,a.newest_time,a.window_start,a.window_end,a.backfill_done,a.blocked,a.last_error,(SELECT COUNT(*) FROM posts p WHERE p.account_key=a.account_key) AS saved_posts FROM accounts a ORDER BY ordinal")],
            'note':'No claim of uninterrupted seven-day coverage; API date filters unverified, client-side dates/cursors.'}
    (out/'run_manifest.json').write_text(json.dumps(result,ensure_ascii=False,indent=2),encoding='utf-8')
    return result


@contextmanager
def process_lock(base):
    """Cross-process Windows/Linux nonblocking lock for independent RAW v2 store."""
    path=Path(base)/'.collector.lock'
    fp=path.open('a+b')
    try:
        if path.stat().st_size==0:fp.write(b'0');fp.flush()
        fp.seek(0)
        try:
            if os.name=='nt':
                import msvcrt
                msvcrt.locking(fp.fileno(),msvcrt.LK_NBLCK,1)
            else:
                import fcntl
                fcntl.flock(fp.fileno(),fcntl.LOCK_EX|fcntl.LOCK_NB)
        except OSError:
            raise ValueError('another RAW v2 process is using this output directory') from None
        yield
    finally:
        try:
            fp.seek(0)
            if os.name=='nt':
                import msvcrt
                msvcrt.locking(fp.fileno(),msvcrt.LK_UNLCK,1)
            else:
                import fcntl
                fcntl.flock(fp.fileno(),fcntl.LOCK_UN)
        except OSError:pass
        fp.close()


def active_slot(db, cooldown_minutes=60):
    """Resume partial 150-account slot; cool down only after slot closes.

    Adopt a prior pilot made by RAW v2.1 (before slots existed) if it is recent,
    preventing the first five real requests from being counted twice.
    """
    prior=db.execute('SELECT * FROM slots ORDER BY slot_id DESC LIMIT 1').fetchone()
    if not prior:
        old=db.execute('SELECT * FROM runs WHERE finished_at IS NOT NULL ORDER BY started_at DESC LIMIT 1').fetchone()
        recent=(old and old['finished_at'] and
                (datetime.now(timezone.utc)-parse_date(old['finished_at'])).total_seconds()<70*60)
        if recent and 0 < old['requests'] < 150:
            with db:
                cur=db.execute('INSERT INTO slots(started_at,status) VALUES (?,?)',(old['started_at'],'active'))
                sid=cur.lastrowid
                db.execute('INSERT OR IGNORE INTO slot_accounts(slot_id,account_key,request_id) '
                           'SELECT ?,account_key,request_id FROM request_log WHERE run_id=?',(sid,old['run_id']))
            return sid
    else:
        if prior['status']=='active':return prior['slot_id']
        next_time=parse_date(prior['ready_at'])
        if next_time and datetime.now(timezone.utc)<next_time:
            print('SKIPPED: 60-minute post-completion cooldown; next start after '+prior['ready_at'])
            return None
    with db:
        row=db.execute('INSERT INTO slots(started_at,status) VALUES (?,?)',(now(),'active'))
    return row.lastrowid


def close_slot(db,sid,reason,cooldown_minutes=60):
    finished=now()
    ready=(parse_date(finished)+timedelta(minutes=cooldown_minutes)).isoformat()
    with db:
        db.execute('UPDATE slots SET finished_at=?,ready_at=?,status=?,reason=? WHERE slot_id=? AND status=?',
                   (finished,ready,'cooldown',reason,sid,'active'))
    print(f'SLOT CLOSED: {reason}; next slot after {ready} (60min after finish)')


def run(args,client=request_batch):
    accounts,digest=load_accounts(args.source)
    if args.batch_size<1 or args.batch_size>10 or args.limit!=5 or args.max_batches<1 or not 0<args.stop_usage<100:
        raise ValueError('batch-size 1..10, limit 5, max-batches>=1, 0<stop-usage<100 required')
    if not 1 <= args.window_days <= 31:raise ValueError('window-days 1..31 required')
    if args.history_days not in (365,730):raise ValueError('history-days must be 365 or 730')
    if not args.live:
        print(f'DRY RUN: official {len(accounts)} accounts; {args.mode}, limit=5, {args.window_days}d logical windows, 70min suggested cadence; NO writes/API')
        return 0
    token,caller,version=load_env(args.env_file)
    base=Path(args.output_dir).resolve()
    if base==ROOT or base.is_relative_to(ROOT/'kq3_294_collection') or base.is_relative_to(ROOT/'experiments'):
        raise ValueError('Output must not overwrite legacy stores')
    base.mkdir(parents=True,exist_ok=True)
    with process_lock(base):
        db=connect(base/'collection.sqlite',accounts,digest)
        start=now();run_id=datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ')+'_'+str(os.getpid())+'_'+uuid.uuid4().hex[:8]
        halted=False;max_seen=None
        try:
            slot=active_slot(db,60)
            if slot is None:return 0
            already={r[0] for r in db.execute('SELECT account_key FROM slot_accounts WHERE slot_id=?',(slot,))}
            remaining=150-len(already)
            if remaining<=0:
                close_slot(db,slot,'150_accounts_completed')
                return 0
            with db:db.execute('INSERT INTO runs(run_id,started_at,api_version,mode,status) VALUES (?,?,?,?,?)',
                               (run_id,start,version,args.mode,'started'))
            # Resume unfinished slot. Each account is queried at most once per slot.
            tasks=[(phase,a) for phase,a in select_tasks(db,args.mode,294) if a['account_key'] not in already][:remaining]
            for b in range(args.max_batches):
                if not tasks:break
                batch,tasks=tasks[:args.batch_size],tasks[args.batch_size:]
                ops=[make_op(a,caller,phase,extended=args.extended_fields) for phase,a in batch]
                t0=now()
                try:http,body,headers=client(version,token,ops)
                except (URLError,OSError,TimeoutError) as e:
                    print('HALTED: network error; no checkpoint advanced');halted=True;break
                outer_usage=usage(headers)
                if not isinstance(body,list) or len(body)!=len(batch):
                    print('HALTED: outer batch invalid or API error; no checkpoints advanced');halted=True
                    persist_response(base,run_id,f'outer_{b:04}',body,token)
                    break
                batch_usage=[x for x in [outer_usage]+[usage(x.get('headers')) if isinstance(x,dict) else None for x in body] if x is not None]
                if batch_usage:max_seen=max(batch_usage+[max_seen or 0])
                fatal=False
                for i,((phase,a),entry) in enumerate(zip(batch,body)):
                    req=f'{b:04}_{i:02}_{a["ordinal"]:03}_{phase}'
                    ts=now();code=int(entry.get('code') or 0) if isinstance(entry,dict) else 0
                    try:payload=json.loads(entry.get('body') or '{}') if isinstance(entry,dict) else {}
                    except (ValueError,TypeError):payload={}
                    raw=persist_response(base,run_id,req,payload,token)
                    err=payload.get('error') if isinstance(payload,dict) else None
                    error_code=str(err.get('code','')) if isinstance(err,dict) else ''
                    error_sub=str(err.get('error_subcode','')) if isinstance(err,dict) else ''
                    error_message=str(redact(err.get('message',''),token))[:300] if isinstance(err,dict) else ''
                    status='failed';count=0;new=0;has_next='unknown';reason='api_error' if code!=200 else 'invalid_payload'
                    if code==200 and not err and isinstance(payload,dict):
                        try:
                            new,count,more=insert_success(db,a,phase,payload,run_id,req,ts,args.history_days,args.extended_fields,args.window_days)
                            status='success';reason='page_saved';has_next='true' if more else 'false'
                        except (ValueError,sqlite3.Error) as exc:
                            error_code='invalid_payload';error_message=str(exc)[:300]
                            # insert_success commits atomically; on failure don't advance cursor
                    else:
                        if error_code in STOP_ERRORS or code in (401,403,429):fatal=True
                    with db:
                        if status!='success':
                            if code in (401,403,429):fatal=True
                            db.execute("UPDATE accounts SET attempts=attempts+1,last_attempt=?,last_error=?,blocked=CASE WHEN ? = '110' THEN 1 ELSE blocked END WHERE account_key=?",
                                       (ts,error_code,error_code,a['account_key']))
                        db.execute('INSERT INTO request_log(run_id,request_id,account_key,phase,started_at,finished_at,http_status,status,records_received,has_next_page,stop_reason,error_code,error_subcode,error_message,raw_response_path,requested_fields,usage_json) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)',
                                   (run_id,req,a['account_key'],phase,t0,ts,code,status,count,has_next,reason,error_code,error_sub,error_message,
                                    str(raw.relative_to(base)),BASE_PROFILE+(','+EXTRA_PROFILE if args.extended_fields else '')+','+MEDIA_FIELDS,json.dumps({'outer_total_time':outer_usage})))
                        db.execute('UPDATE runs SET requests=requests+1,successes=successes+?,failures=failures+? WHERE run_id=?',
                                   (int(status=='success'),int(status!='success'),run_id))
                        db.execute('INSERT INTO slot_accounts(slot_id,account_key,request_id) VALUES (?,?,?)',
                                   (slot,a['account_key'],req))
                    print(f'{a["account_key"]} {phase}: {status} HTTP={code} received={count} new={new} like-field diagnostics saved')
                if fatal or max_seen is None or max_seen>=args.stop_usage:
                    print(f'HALTED: stop flag or usage unavailable/at threshold; total_time_max={max_seen}')
                    halted=True;break
            with db: db.execute('UPDATE runs SET finished_at=?,status=?,usage_max=? WHERE run_id=?',
                                (now(),'paused' if halted else 'closed',max_seen,run_id))
            used=db.execute('SELECT COUNT(*) FROM slot_accounts WHERE slot_id=?',(slot,)).fetchone()[0]
            if halted or used>=150 or not tasks:
                close_slot(db,slot,'api_halted' if halted else '150_accounts_completed' if used>=150 else 'no_eligible_accounts')
            else:
                print(f'SLOT OPEN: {used}/150 accounts processed. May continue immediately; no cooldown yet.')
            output=export_run(db,base,run_id,args.source)
            print('Run:',run_id,'new posts:',output['row_count']['posts.csv'],'cumulative:',output['cumulative_posts'])
            print('Saved:',base/'runs'/run_id)
            return 2 if halted else 0
        finally:
            db.close()


def main(argv=None):
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--source',type=Path,required=True)
    p.add_argument('--env-file',type=Path)
    p.add_argument('--output-dir',type=Path,default=ROOT/'raw_v2_results')
    p.add_argument('--live',action='store_true')
    p.add_argument('--mode',choices=('balanced','front','history'),default='balanced')
    p.add_argument('--batch-size',type=int,default=5)
    p.add_argument('--max-batches',type=int,default=1,help='Safest pilot default: one batch')
    p.add_argument('--limit',type=int,default=5)
    p.add_argument('--window-days',type=int,default=7,help='Logical client-side coverage; not API server filter')
    p.add_argument('--history-days',type=int,default=365)
    p.add_argument('--cycle-minutes',type=int,default=70,help='Deprecated: previous start-to-start cooldown; slot now cools 60min after finish')
    p.add_argument('--stop-usage',type=float,default=70)
    p.add_argument('--extended-fields',action='store_true',help='Unverified profile fields; separate controlled trial only')
    a=p.parse_args(argv)
    try:return run(a)
    except (ValueError,sqlite3.Error) as e:
        print('HALTED:',str(e),file=sys.stderr);return 1

if __name__=='__main__':raise SystemExit(main())
