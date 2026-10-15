#!/usr/bin/env python3
"""Resumable, isolated 294-account Meta Business Discovery batch collector.

Dry run by default. The --live flag permits HTTPS requests. A batch is limited
by default to 10 target accounts (the largest batch size verified locally).
No automatic retries. This program NEVER modifies the older kq3_294_collection.
"""
from __future__ import annotations

import argparse
import csv
from contextlib import contextmanager
from datetime import datetime, timezone
from email.message import Message
import hashlib
import json
import os
from pathlib import Path
import re
import sqlite3
import sys
import tempfile
import time
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import Request, urlopen

ROOT = Path(__file__).resolve().parent.parent
DEFAULT_SOURCE = ROOT / "account_list.csv"
DEFAULT_ENV = ROOT / "kq3_294_collection" / ".env.local"
DEFAULT_OUTPUT = ROOT / "experiments" / "results" / "full_294"
EXPECTED_COUNTS = {"KR": 100, "US": 101, "JP": 93}
STOP_CODES = {"4", "17", "32", "613", "10", "102", "104", "190", "200"}
STOP_HTTP = {401, 403, 429}
MEDIA_FIELDS = (
    "id,caption,media_type,media_product_type,permalink,timestamp,"
    "like_count,comments_count,media_url,thumbnail_url,"
    "children.limit(50){id,media_type,media_url,thumbnail_url}"
)
CSV_TABLES = {
    "accounts.csv": "accounts",
    "posts.csv": "posts",
    "media.csv": "media",
    "collection_status.csv": "accounts",
    "batch_log.csv": "batches",
}


def stamp() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def load_source(path: Path) -> tuple[list[dict[str, str]], str]:
    digest = hashlib.sha256(path.read_bytes()).hexdigest()
    with path.open("r", encoding="utf-8-sig", newline="") as fp:
        reader = csv.DictReader(fp)
        mandatory = {"account_key", "username", "country", "image_url"}
        if not reader.fieldnames or not mandatory.issubset(reader.fieldnames):
            raise ValueError("Official account_list.csv schema is missing required columns")
        accounts = []
        for i, row in enumerate(reader, 1):
            a = {x: (row.get(x) or "").strip() for x in mandatory}
            a["ordinal"] = i
            if not all(a.get(k) for k in mandatory):
                raise ValueError(f"Missing source field in row {i}")
            if not re.fullmatch(r"[A-Za-z0-9._]{1,30}", a["username"].lstrip("@")):
                raise ValueError(f"Invalid Instagram username at row {i}")
            a["username"] = a["username"].lstrip("@")
            accounts.append(a)
    if len(accounts) != 294 or {c: sum(x["country"] == c for x in accounts) for c in EXPECTED_COUNTS} != EXPECTED_COUNTS:
        raise ValueError("Official account list must contain 294 entries (KR100/US101/JP93)")
    if len({x["account_key"] for x in accounts}) != 294 or len({x["username"].casefold() for x in accounts}) != 294:
        raise ValueError("Duplicate official account_key or username")
    return accounts, digest


def load_env(path: Path) -> dict[str, str]:
    values = dict(os.environ)
    if path.exists():
        for raw in path.read_text(encoding="utf-8-sig").splitlines():
            line = raw.strip()
            if line and not line.startswith("#") and "=" in line:
                key, val = line.split("=", 1)
                values.setdefault(key.strip(), val.strip().strip('"\''))
    return values


def operations_for(accounts: list[dict], caller_id: str, count: int) -> list[dict[str, str]]:
    ops = []
    for a in accounts:
        fields = (
            f'business_discovery.username({a["username"]})'
            f'{{id,username,followers_count,media_count,media.limit({count}){{{MEDIA_FIELDS}}}}}'
        )
        ops.append({"method": "GET", "relative_url": f"{caller_id}?{urlencode({'fields': fields})}"})
    return ops


def usage(headers: Any) -> dict[str, float]:
    if isinstance(headers, list):
        headers = {str(h.get("name", "")).lower(): h.get("value") for h in headers if isinstance(h, dict)}
    elif hasattr(headers, "items"):
        headers = {str(k).lower(): v for k, v in headers.items()}
    else:
        return {}
    raw = headers.get("x-app-usage")
    try:
        val = json.loads(raw) if raw else {}
    except (TypeError, ValueError):
        return {}
    if not isinstance(val, dict):
        return {}
    return {k: float(v) for k in ("call_count", "total_time", "total_cputime")
            if isinstance((v := val.get(k)), (int, float)) and not isinstance(v, bool)}


def safe_error(error: object, token: str) -> tuple[str, str]:
    if not isinstance(error, dict):
        return "unknown", "Meta subrequest failed"
    code = str(error.get("code", "unknown"))[:40]
    message = str(error.get("message") or "Meta subrequest failed")
    if token:
        message = message.replace(token, "[REDACTED]")
    return code, message[:300]


def request_batch(version: str, token: str, ops: list[dict[str, str]], timeout: int = 120) -> tuple[int, object, Any, float]:
    payload = urlencode({"access_token": token, "batch": json.dumps(ops), "include_headers": "true"}).encode("utf-8")
    req = Request(f"https://graph.facebook.com/{version}", data=payload, method="POST",
                  headers={"Content-Type": "application/x-www-form-urlencoded", "Accept": "application/json"})
    started = time.monotonic()
    try:
        with urlopen(req, timeout=timeout) as resp:
            body = resp.read()
            try:
                parsed = json.loads(body)
            except (TypeError, ValueError):
                parsed = None
            return resp.status, parsed, resp.headers, time.monotonic() - started
    except HTTPError as e:
        # Never print the request, raw body or access token.
        try:
            body = json.loads(e.read(1_000_000))
        except (TypeError, ValueError):
            body = None
        return e.code, body, e.headers, time.monotonic() - started


@contextmanager
def process_lock(path: Path):
    lock = path / ".collection.lock"
    fp = lock.open("a+b")
    try:
        if lock.stat().st_size == 0:
            fp.write(b"0")
            fp.flush()
        fp.seek(0)
        try:
            if os.name == "nt":
                import msvcrt
                msvcrt.locking(fp.fileno(), msvcrt.LK_NBLCK, 1)
            else:
                import fcntl
                fcntl.flock(fp.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError:
            raise RuntimeError("Another collector process is using this output directory") from None
        yield
    finally:
        try:
            if os.name == "nt":
                import msvcrt
                fp.seek(0)
                msvcrt.locking(fp.fileno(), msvcrt.LK_UNLCK, 1)
            else:
                import fcntl
                fcntl.flock(fp.fileno(), fcntl.LOCK_UN)
        except OSError:
            pass
        fp.close()


def database(path: Path, accounts: list[dict], digest: str, posts_requested: int) -> sqlite3.Connection:
    db = sqlite3.connect(path)
    db.row_factory = sqlite3.Row
    db.execute("PRAGMA journal_mode=WAL")
    db.execute("PRAGMA synchronous=FULL")
    db.executescript("""
    CREATE TABLE IF NOT EXISTS manifest (key TEXT PRIMARY KEY, value TEXT NOT NULL);
    CREATE TABLE IF NOT EXISTS accounts (
        ordinal INTEGER NOT NULL UNIQUE, account_key TEXT PRIMARY KEY, username TEXT NOT NULL,
        country TEXT NOT NULL, image_url TEXT NOT NULL, ig_user_id TEXT,
        followers_count INTEGER, media_count INTEGER, posts_collected INTEGER DEFAULT 0,
        status TEXT NOT NULL DEFAULT 'pending', coverage TEXT NOT NULL DEFAULT 'not_assessed',
        attempt_count INTEGER DEFAULT 0,
        last_attempt_at TEXT, completed_at TEXT, error_code TEXT, error_message TEXT,
        http_status INTEGER, batch_id INTEGER
    );
    CREATE TABLE IF NOT EXISTS posts (
        post_id TEXT PRIMARY KEY, account_key TEXT NOT NULL, username TEXT NOT NULL,
        country TEXT NOT NULL, timestamp TEXT, caption TEXT, media_type TEXT,
        media_product_type TEXT, permalink TEXT, like_count INTEGER,
        comments_count INTEGER, engagement_count INTEGER, engagement_rate_pct REAL,
        media_count INTEGER, collected_at TEXT
    );
    CREATE TABLE IF NOT EXISTS media (
        media_id TEXT PRIMARY KEY, post_id TEXT NOT NULL, parent_media_id TEXT,
        account_key TEXT NOT NULL, username TEXT NOT NULL, country TEXT NOT NULL,
        media_role TEXT, media_type TEXT, media_url TEXT, thumbnail_url TEXT,
        sort_order INTEGER, collected_at TEXT
    );
    CREATE TABLE IF NOT EXISTS batches (
        batch_id INTEGER PRIMARY KEY AUTOINCREMENT, started_at TEXT NOT NULL,
        finished_at TEXT, account_keys_json TEXT NOT NULL, size INTEGER NOT NULL,
        request_posts INTEGER NOT NULL, http_status INTEGER, duration_seconds REAL,
        success_count INTEGER DEFAULT 0, failed_count INTEGER DEFAULT 0,
        error TEXT, rate_limited INTEGER DEFAULT 0, usage_json TEXT,
        status TEXT NOT NULL DEFAULT 'started'
    );
    CREATE INDEX IF NOT EXISTS ix_posts_account ON posts(account_key);
    CREATE INDEX IF NOT EXISTS ix_media_account ON media(account_key);
    """)
    new_manifest = {"source_sha256": digest, "posts_requested": str(posts_requested),
                    "account_count": "294", "schema_version": "1"}
    stored = dict(db.execute("SELECT key,value FROM manifest").fetchall())
    if stored and any(stored.get(k) != v for k, v in new_manifest.items()):
        db.close()
        raise ValueError("Output database source/schema/limit differs: choose a new --output-dir, do not mix runs")
    with db:
        if not stored:
            db.executemany("INSERT INTO manifest (key,value) VALUES (?,?)", new_manifest.items())
            db.executemany("""INSERT INTO accounts (ordinal,account_key,username,country,image_url,status)
                VALUES (:ordinal,:account_key,:username,:country,:image_url,'pending')""", accounts)
    return db


def as_integer(v: object) -> int | None:
    if v is None or v == "":
        return None
    try:
        return int(v)
    except (TypeError, ValueError):
        return None


def decode_success(entry: dict, a: dict) -> tuple[dict, list[dict], list[dict]]:
    discovery = entry.get("business_discovery")
    if not isinstance(discovery, dict):
        raise ValueError("Missing business_discovery in successful response")
    edge = discovery.get("media")
    items = edge.get("data", []) if isinstance(edge, dict) else []
    if not isinstance(items, list):
        raise ValueError("Malformed media data")
    received_at = stamp()
    followers = as_integer(discovery.get("followers_count"))
    posts: list[dict] = []
    media: list[dict] = []
    for post in items:
        if not isinstance(post, dict) or not post.get("id"):
            raise ValueError("Post without valid ID")
        pid = str(post["id"])
        likes = as_integer(post.get("like_count"))
        comments = as_integer(post.get("comments_count"))
        engagement = likes + comments if likes is not None and comments is not None else None
        rate = (engagement * 100 / followers) if engagement is not None and followers else None
        children_edge = post.get("children")
        children = children_edge.get("data", []) if isinstance(children_edge, dict) else []
        if not isinstance(children, list):
            raise ValueError("Malformed carousel children")
        carousel = post.get("media_type") == "CAROUSEL_ALBUM"
        posts.append({"post_id": pid, "account_key": a["account_key"], "username": a["username"],
                      "country": a["country"], "timestamp": post.get("timestamp"),
                      "caption": post.get("caption"), "media_type": post.get("media_type"),
                      "media_product_type": post.get("media_product_type"), "permalink": post.get("permalink"),
                      "like_count": likes, "comments_count": comments, "engagement_count": engagement,
                      "engagement_rate_pct": rate, "media_count": len(children) if carousel else 1,
                      "collected_at": received_at})
        if carousel:
            media.append({"media_id": pid, "post_id": pid, "parent_media_id": None,
                          "account_key": a["account_key"], "username": a["username"],
                          "country": a["country"], "media_role": "carousel_container",
                          "media_type": "CAROUSEL_ALBUM", "media_url": post.get("media_url"),
                          "thumbnail_url": post.get("thumbnail_url"), "sort_order": 0,
                          "collected_at": received_at})
            for idx, child in enumerate(children, 1):
                if not isinstance(child, dict):
                    raise ValueError("Malformed carousel child")
                cid = str(child.get("id") or f"{pid}:child:{idx}")
                media.append({"media_id": cid, "post_id": pid, "parent_media_id": pid,
                              "account_key": a["account_key"], "username": a["username"],
                              "country": a["country"], "media_role": "carousel_item",
                              "media_type": child.get("media_type"), "media_url": child.get("media_url"),
                              "thumbnail_url": child.get("thumbnail_url"), "sort_order": idx,
                              "collected_at": received_at})
        else:
            media.append({"media_id": pid, "post_id": pid, "parent_media_id": None,
                          "account_key": a["account_key"], "username": a["username"],
                          "country": a["country"], "media_role": "primary",
                          "media_type": post.get("media_type"), "media_url": post.get("media_url"),
                          "thumbnail_url": post.get("thumbnail_url"), "sort_order": 1,
                          "collected_at": received_at})
    if len({p["post_id"] for p in posts}) != len(posts):
        raise ValueError("Duplicated post IDs within one account")
    if len({m["media_id"] for m in media}) != len(media):
        raise ValueError("Duplicated media IDs within one account")
    account_info = {"ig_user_id": str(discovery.get("id") or ""), "followers_count": followers,
                    "media_count": as_integer(discovery.get("media_count")), "posts_collected": len(posts)}
    return account_info, posts, media


def save_row(conn: sqlite3.Connection, table: str, row: dict) -> None:
    cols = list(row)
    conn.execute(f"INSERT INTO {table} ({','.join(cols)}) VALUES ({','.join('?' for _ in cols)})",
                 [row[k] for k in cols])


def begin_batch(conn: sqlite3.Connection, selected: list[dict], count: int) -> int:
    with conn:
        cur = conn.execute("INSERT INTO batches (started_at,account_keys_json,size,request_posts) VALUES (?,?,?,?)",
                           (stamp(), json.dumps([a["account_key"] for a in selected]), len(selected), count))
        return int(cur.lastrowid)


def apply_batch(conn: sqlite3.Connection, batch_id: int, targets: list[dict], http_code: int,
                body: object, outer_headers: Any, duration: float, count: int, token: str) -> tuple[int, int, bool, dict]:
    """Atomically store a batch; never mark an unparseable/mismatched batch as collected."""
    inner = body if isinstance(body, list) and len(body) == len(targets) else None
    app_usages: list[dict] = []
    outer = usage(outer_headers)
    successes = 0
    failures = 0
    stop = http_code in STOP_HTTP
    parsed: list[tuple[dict, int, dict, dict, list[dict], list[dict], str | None, str | None]] = []
    if inner is not None:
        for account, item in zip(targets, inner):
            if not isinstance(item, dict):
                inner = None
                break
            code = as_integer(item.get("code")) or 0
            headers = item.get("headers")
            metrics = usage(headers)
            if metrics:
                app_usages.append(metrics)
            try:
                payload = json.loads(item.get("body") or "{}")
            except (ValueError, TypeError):
                payload = None
            err_code = None
            err_msg = None
            info: dict = {}
            posts: list[dict] = []
            media: list[dict] = []
            if code == 200 and isinstance(payload, dict) and not isinstance(payload.get("error"), dict):
                try:
                    info, posts, media = decode_success(payload, account)
                except ValueError:
                    err_code, err_msg = "invalid_payload", "Malformed Business Discovery payload"
            else:
                err_code, err_msg = safe_error(payload.get("error") if isinstance(payload, dict) else None, token)
            if code in STOP_HTTP or err_code in STOP_CODES:
                stop = True
            parsed.append((account, code, metrics, info, posts, media, err_code, err_msg))
    worst = max((m.get("total_time", 0.0) for m in ([outer] + app_usages)), default=0.0)
    snapshot = {"outer": outer, "inner": app_usages,
                "max_total_time_pct": worst if outer or app_usages else None}
    batch_error = None
    status = "completed"
    if inner is None:
        stop = True
        status = "invalid_response"
        if isinstance(body, dict) and isinstance(body.get("error"), dict):
            code, message = safe_error(body["error"], token)
            status = "outer_error"
            batch_error = f"Meta outer error code={code}: {message}"
        else:
            batch_error = "Unexpected batch response shape or count; all selected accounts remain pending"
    else:
        failures_codes = sorted({f"{err_code} (HTTP {code})" for _, code, _, _, _, _, err_code, _ in parsed
                                 if err_code is not None})
        if failures_codes:
            batch_error = "Subrequest errors: " + ", ".join(failures_codes)
    with conn:
        if inner is not None:
            for a, code, metrics, info, posts, media, error, message in parsed:
                key = a["account_key"]
                if error is None:
                    # Delete-and-replace is scoped to this account, inside this transaction.
                    conn.execute("DELETE FROM media WHERE account_key=?", (key,))
                    conn.execute("DELETE FROM posts WHERE account_key=?", (key,))
                    for p in posts:
                        save_row(conn, "posts", p)
                    for m in media:
                        save_row(conn, "media", m)
                    has_more_reported = (info["media_count"] is not None and
                                         info["media_count"] > len(posts) and len(posts) < count)
                    missing_children = any(m["media_role"] == "carousel_container" and
                                           not any(x["parent_media_id"] == m["media_id"] for x in media)
                                           for m in media)
                    coverage = "partial" if has_more_reported or missing_children else "complete"
                    collection_state = "partial" if coverage == "partial" else "success"
                    conn.execute("""UPDATE accounts SET ig_user_id=?,followers_count=?,media_count=?,
                        posts_collected=?,status=?,coverage=?,attempt_count=attempt_count+1,
                        last_attempt_at=?,completed_at=?,error_code=NULL,error_message=NULL,
                        http_status=?,batch_id=? WHERE account_key=?""",
                        (info["ig_user_id"], info["followers_count"], info["media_count"],
                         info["posts_collected"], collection_state, coverage, stamp(), stamp(), code, batch_id, key))
                    successes += 1
                else:
                    limit_failure = code in STOP_HTTP or error in STOP_CODES
                    new_status = "rate_limited" if limit_failure else "failed"
                    conn.execute("""UPDATE accounts SET status=?,coverage='not_assessed',attempt_count=attempt_count+1,
                        last_attempt_at=?,completed_at=?,error_code=?,error_message=?,
                        http_status=?,batch_id=? WHERE account_key=?""",
                        (new_status, stamp(), stamp(), error, message, code, batch_id, key))
                    failures += 1
        conn.execute("""UPDATE batches SET finished_at=?,http_status=?,duration_seconds=?,
            success_count=?,failed_count=?,error=?,rate_limited=?,usage_json=?,status=?
            WHERE batch_id=?""",
            (stamp(), http_code, round(duration, 3), successes, failures, batch_error,
             int(stop), json.dumps(snapshot), status, batch_id))
    return successes, failures, stop, snapshot


def atomic_csv(path: Path, rows: list[sqlite3.Row], columns: list[str]) -> None:
    fd, temporary = tempfile.mkstemp(prefix="."+path.stem+"_", suffix=".tmp", dir=path.parent)
    try:
        with os.fdopen(fd, "w", encoding="utf-8-sig", newline="") as fp:
            writer = csv.writer(fp)
            writer.writerow(columns)
            writer.writerows(tuple(row[col] for col in columns) for row in rows)
            fp.flush()
            os.fsync(fp.fileno())
        os.replace(temporary, path)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


def export_all(conn: sqlite3.Connection, folder: Path) -> dict:
    for file, table in CSV_TABLES.items():
        if file == "collection_status.csv":
            columns = ["ordinal", "account_key", "username", "country", "status", "coverage",
                       "attempt_count", "last_attempt_at", "completed_at", "posts_collected",
                       "error_code", "error_message", "http_status", "batch_id"]
            query = f"SELECT {','.join(columns)} FROM accounts ORDER BY ordinal"
        else:
            columns = [c[1] for c in conn.execute(f"PRAGMA table_info({table})").fetchall()]
            order = ("ordinal" if table == "accounts" else "batch_id" if table == "batches" else "rowid")
            query = f"SELECT * FROM {table} ORDER BY {order}"
        atomic_csv(folder / file, conn.execute(query).fetchall(), columns)
    counters = dict(conn.execute("SELECT status,COUNT(*) FROM accounts GROUP BY status").fetchall())
    totals = {table: conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]
              for table in ("posts", "media", "batches")}
    last_usage = conn.execute("SELECT usage_json FROM batches WHERE status='completed' ORDER BY batch_id DESC LIMIT 1").fetchone()
    report = {"updated_at_utc": stamp(), "accounts": counters, **totals,
              "most_recent_usage": json.loads(last_usage[0]) if last_usage and last_usage[0] else {}}
    path = folder / "summary.json"
    fd, tmp = tempfile.mkstemp(prefix=".summary_", suffix=".tmp", dir=folder)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as fp:
            json.dump(report, fp, ensure_ascii=False, indent=2)
            fp.write("\n")
            fp.flush()
            os.fsync(fp.fileno())
        os.replace(tmp, path)
    finally:
        if os.path.exists(tmp):
            os.unlink(tmp)
    return report


def run(args: argparse.Namespace) -> int:
    if args.output_dir.resolve() == ROOT.resolve() or args.output_dir.resolve().is_relative_to((ROOT / "kq3_294_collection").resolve()):
        raise ValueError("Output directory must be separate from existing legacy data")
    if not 1 <= args.batch_size <= 10:
        raise ValueError("--batch-size must be 1..10 until larger batches are verified")
    if args.posts != 20:
        raise ValueError("Full-collection experiment fixes --posts=20 for comparability")
    if args.delay_seconds < 0 or (args.max_batches is not None and args.max_batches < 1):
        raise ValueError("Invalid delay or max-batches")
    if args.stop_usage is not None and not (0 < args.stop_usage <= 100):
        raise ValueError("--stop-usage must be >0 and <=100")
    source, digest = load_source(args.source)
    if not args.live:
        print(f"DRY RUN: 294 accounts, {args.posts} recent posts each; {args.batch_size} per batch, "
              f"{(294+args.batch_size-1)//args.batch_size} total batches; no API requests or file writes")
        print(f"Source sha256: {digest}")
        print(f"Output (separate from existing collector): {args.output_dir}")
        return 0
    cfg = load_env(args.env_file)
    token = cfg.get("META_ACCESS_TOKEN", "").strip()
    caller = cfg.get("META_IG_USER_ID", "").strip()
    version = cfg.get("META_GRAPH_VERSION", "v26.0").strip()
    if not token or not re.fullmatch(r"[0-9]+", caller) or not re.fullmatch(r"v\d+\.\d+", version):
        raise ValueError("Missing or invalid local Meta configuration; check --env-file")
    args.output_dir.mkdir(parents=True, exist_ok=True)
    with process_lock(args.output_dir):
        conn = database(args.output_dir / "collection.sqlite", source, digest, args.posts)
        try:
            export_all(conn, args.output_dir)
            sent = 0
            while True:
                if args.max_batches and sent >= args.max_batches:
                    print(f"Run paused after --max-batches={args.max_batches}; rerun to resume")
                    break
                eligible = conn.execute("""SELECT ordinal,account_key,username,country,image_url
                    FROM accounts WHERE status IN ('pending','rate_limited') ORDER BY ordinal LIMIT ?""",
                    (args.batch_size,)).fetchall()
                if not eligible:
                    print("No pending or previously rate-limited accounts remain")
                    break
                selected = [dict(x) for x in eligible]
                current = begin_batch(conn, selected, args.posts)
                print(f"Batch {current}: {selected[0]['ordinal']}..{selected[-1]['ordinal']} "
                      f"({len(selected)} accounts), requesting up to {args.posts} posts/account", flush=True)
                try:
                    http, body, headers, seconds = request_batch(version, token, operations_for(selected, caller, args.posts))
                except (URLError, TimeoutError, OSError) as exc:
                    with conn:
                        conn.execute("""UPDATE batches SET finished_at=?,status='transport_error',error=? WHERE batch_id=?""",
                                     (stamp(), type(exc).__name__, current))
                    export_all(conn, args.output_dir)
                    print(f"Transport error ({type(exc).__name__}); stopped without retry; selected accounts remain pending")
                    return 3
                successes, failures, stopped, snapshot = apply_batch(
                    conn, current, selected, http, body, headers, seconds, args.posts, token)
                progress = export_all(conn, args.output_dir)
                totals = progress["accounts"]
                issue = conn.execute("SELECT error FROM batches WHERE batch_id=?", (current,)).fetchone()[0]
                print(f"Batch {current}: {seconds:.3f}s HTTP={http}, success={successes}, failed={failures}, "
                      f"usage_max_total_time={snapshot['max_total_time_pct']}%, "
                      f"total_success={totals.get('success',0)}/294, pending={totals.get('pending',0)}, "
                      f"rate_limited={totals.get('rate_limited',0)}", flush=True)
                if issue:
                    print(f"Batch {current} note: {issue}", flush=True)
                sent += 1
                if stopped:
                    print("HALTED: Meta rate/auth limit or unexpected response. Saved every parseable subresponse.")
                    return 4
                if (args.stop_usage is not None and snapshot["max_total_time_pct"] is not None
                        and snapshot["max_total_time_pct"] >= args.stop_usage):
                    print(f"HALTED: optional --stop-usage={args.stop_usage}% threshold reached")
                    return 5
                if args.delay_seconds and sent and conn.execute(
                        "SELECT COUNT(*) FROM accounts WHERE status IN ('pending','rate_limited')").fetchone()[0]:
                    time.sleep(args.delay_seconds)
            report = export_all(conn, args.output_dir)
            print("Final:", json.dumps(report, ensure_ascii=False))
            return 0 if not any(report["accounts"].get(k) for k in ("failed", "partial", "pending", "rate_limited")) else 1
        finally:
            conn.close()


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", type=Path, default=DEFAULT_SOURCE)
    parser.add_argument("--env-file", type=Path, default=DEFAULT_ENV)
    parser.add_argument("--output-dir", type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument("--batch-size", type=int, default=10)
    parser.add_argument("--posts", type=int, default=20)
    parser.add_argument("--delay-seconds", type=float, default=1.0)
    parser.add_argument("--max-batches", type=int, default=None, help="Optional cap; resume by running again")
    parser.add_argument("--stop-usage", type=float, default=None,
                        help="Optional % cutoff; omitted to observe Meta's actual limit")
    parser.add_argument("--live", action="store_true", help="Authorize Meta API calls")
    args = parser.parse_args(argv)
    try:
        return run(args)
    except (ValueError, RuntimeError, sqlite3.Error, OSError) as exc:
        # Prevent accidental exposure of API key, raw HTTP response or filesystem contents.
        print(f"Collector stopped ({type(exc).__name__}): {str(exc)[:160] if not args.live else 'inspect input, output folder and run log'}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
