"""Read-only synthetic P3/P4 history cutoff proof inspection.

Checks a fixed-target 365/730-day history epoch against the rooted daily
cursor chain, durable synthetic source snapshots and isolated RAW mirror
receipts. It never changes coverage_ledger and cannot authorize production
history or mark real Meta coverage complete.

In particular, MIN(published_at), a cursor being absent, or a forged receipt
alone is NEVER coverage proof. Missing timestamp/receipt/binding fails closed.
"""
from __future__ import annotations

from datetime import datetime, timezone
from hashlib import sha256
import json
import sqlite3

from three_api_epochs import ThreeApiEpochStore, _hash
from three_api_history_offline_bridge import OfflineHistoryRawBridge
from three_api_raw_mirror import OfflineRawMirror, _canonical, _observation_time


class OfflineHistoryCoverageError(ValueError):
    """No untrusted cursor, post, or user media content in error messages."""


def _date(value):
    if not isinstance(value, str):
        raise OfflineHistoryCoverageError("Unverifiable publication timestamp")
    try:
        dt = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        raise OfflineHistoryCoverageError("Unverifiable publication timestamp") from None
    if dt.tzinfo is None or dt.utcoffset() is None:
        raise OfflineHistoryCoverageError("Unverifiable publication timestamp")
    return dt.astimezone(timezone.utc)


def audit_synthetic_history_cutoff(epochs: ThreeApiEpochStore,
                                   mirror: OfflineRawMirror,
                                   history_epoch: str,
                                   account_key: str) -> dict:
    """Prove only currently stored, synthetic, locally linked history pages.

    No DB mutations, no HTTP, no backfill scheduling. Completeness is
    reported as synthetic_cutoff_verified and NEVER production verification.
    An already-open history checkpoint cannot imply cutoff by itself.
    """
    if not isinstance(epochs, ThreeApiEpochStore) or not isinstance(
            mirror, OfflineRawMirror):
        raise OfflineHistoryCoverageError("Isolated offline stores required")
    try:
        info = epochs.db.execute(
            "SELECT kind,roster_digest,anchor_utc,cutoff_utc "
            "FROM collection_epochs WHERE epoch_id=?", (history_epoch,)
        ).fetchone()
        if info is None or info[0] != "history" or info[3] is None:
            raise OfflineHistoryCoverageError("Registered history target required")
        roster,anchor_at,cutoff_at = info[1],_date(info[2]),_date(info[3])
        if cutoff_at >= anchor_at:
            raise OfflineHistoryCoverageError("History cutoff/anchor invalid")
        if epochs.db.execute(
            "SELECT 1 FROM roster_members WHERE roster_digest=? AND account_key=?",
            (roster,account_key)
        ).fetchone() is None:
            raise OfflineHistoryCoverageError("Target account not in immutable roster")
        cp = epochs.history_checkpoint(history_epoch,account_key)
        result = {
            "mode":"SYNTHETIC_HISTORY_CUTOFF_READ_ONLY",
            "account_key":account_key,"history_epoch":history_epoch,
            "pages_checked":0,"posts_checked":0,
            "cutoff_utc":cutoff_at.isoformat().replace("+00:00","Z"),
            "oldest_published_at":None,
            "synthetic_cutoff_verified":False,
            "production_coverage_verified":False,
            "coverage_status":"insufficient_evidence",
            "history_dispatch_enabled":False,
        }
        if cp is None:
            return result
        seed = epochs.db.execute(
            "SELECT cursor,request_id FROM cursor_seen_v2 "
            "WHERE epoch_id=? AND account_key=? AND request_id LIKE 'seed:%'",
            (history_epoch,account_key)
        ).fetchall()
        # An initial cursorless terminal is not a proof of publication-time
        # cutoff; it needs validated daily source timestamps separately.
        if len(seed) != 1:
            return result
        daily = epochs.db.execute(
            "SELECT e.chain_hash,e.cursor_out FROM page_evidence_v2 e "
            "JOIN jobs_v2 j ON j.job_id=e.job_id "
            "JOIN collection_epochs d ON d.epoch_id=j.epoch_id "
            "WHERE e.raw_receipt_id=? AND j.account_key=? "
            "AND j.state='done' AND d.kind='daily' AND d.roster_digest=?",
            (seed[0][1][5:],account_key,roster)
        ).fetchall()
        if len(daily)!=1 or daily[0][1]!=seed[0][0]:
            return result
        prev_chain,prev_cursor=daily[0][0],daily[0][1]
        rows=epochs.db.execute(
            "SELECT j.job_id,j.page_seq,j.state,j.predecessor_job_id,"
            "e.request_id,e.cursor_in,e.cursor_out,e.observed_at,"
            "e.ordered_ids_json,e.terminal_basis,e.source_evidence_id,"
            "e.raw_receipt_id,e.source_digest,e.chain_hash "
            "FROM jobs_v2 j LEFT JOIN page_evidence_v2 e ON e.job_id=j.job_id "
            "WHERE j.epoch_id=? AND j.account_key=? AND j.phase='history' "
            "ORDER BY j.page_seq",(history_epoch,account_key)
        ).fetchall()
        if not rows or any(row[2]!="done" for row in rows):
            return result
        seen=set()
        seen_history_cursors=set()
        seen_raw_receipts=set()
        seen_source_ids=set()
        oldest=None
        previous_job=None
        last_published=None
        last_terminal=False
        for number,row in enumerate(rows,1):
            (job_id,page,state,predecessor,request_id,cursor_in,cursor_out,
             observed_at,ids_json,terminal,source_id,raw_id,source_digest,
             chain_hash)=tuple(row)
            if (page!=number or predecessor!=previous_job or
                    cursor_in!=prev_cursor or
                    source_id!="synthetic-history:"+job_id or
                    raw_id!="history."+job_id or
                    terminal not in ("continued","verified_terminal")):
                return result
            if raw_id in seen_raw_receipts or source_id in seen_source_ids:
                return result
            seen_raw_receipts.add(raw_id)
            seen_source_ids.add(source_id)
            bound=epochs.db.execute(
                "SELECT request_id,requested_cursor FROM request_bindings_v2 "
                "WHERE job_id=?",(job_id,)
            ).fetchone()
            if bound is None or tuple(bound)!=(request_id,cursor_in):
                return result
            source=epochs.db.execute(
                "SELECT request_id,account_key,cursor_in,cursor_out,observed_at,"
                "terminal_basis,posts_json,source_digest "
                "FROM offline_history_capture WHERE job_id=?",(job_id,)
            ).fetchone()
            if (source is None or tuple(source[:6])!=(
                    request_id,account_key,cursor_in,cursor_out,observed_at,terminal)):
                return result
            posts_json=source[6]
            if (source_digest!=source[7] or
                    source_digest!=OfflineHistoryRawBridge._digest(
                        job_id,request_id,account_key,cursor_in,cursor_out,
                        observed_at,terminal,posts_json)):
                return result
            expected_chain=_hash(
                prev_chain,job_id,request_id,cursor_in,cursor_out,
                json.loads(ids_json),source_id,raw_id,source_digest
            )
            if expected_chain!=chain_hash:
                return result
            if cursor_out is not None and (cursor_out == cursor_in or cursor_out in seen_history_cursors):
                return result
            if cursor_in is not None:
                seen_history_cursors.add(cursor_in)
            if cursor_out is not None:
                seen_history_cursors.add(cursor_out)
            receipt=mirror.db.execute(
                "SELECT account_key,observed_at,payload_json,payload_sha256,state "
                "FROM page_receipts WHERE request_id=?",(raw_id,)
            ).fetchone()
            if (receipt is None or receipt["account_key"]!=account_key
                    or receipt["state"]!="committed"
                    or receipt["observed_at"]!=_observation_time(observed_at)
                    or receipt["payload_json"]!=posts_json
                    or receipt["payload_sha256"]!=sha256(
                        posts_json.encode("utf-8")).hexdigest()):
                return result
            posts=json.loads(posts_json)
            if not isinstance(posts,list) or len(posts)>4 or [p["id"] for p in posts]!=json.loads(ids_json):
                return result
            observed=_date(observed_at)
            for post in posts:
                pub=_date(post.get("timestamp"))
                # A publication may not appear after its capture or after the
                # fixed historical target anchor.
                if pub>observed or pub>anchor_at or (last_published is not None and pub>last_published):
                    return result
                if post["id"] in seen:
                    return result
                seen.add(post["id"])
                last_published=pub
                oldest=pub if oldest is None or pub<oldest else oldest
                stored=mirror.db.execute(
                    "SELECT account_key FROM raw_posts WHERE post_id=?",
                    (post["id"],)
                ).fetchone()
                obs=mirror.db.execute(
                    "SELECT observed_at,like_count,comments_count,like_present,"
                    "comments_present FROM raw_post_observations "
                    "WHERE request_id=? AND post_id=?",
                    (raw_id,post["id"])
                ).fetchall()
                if (stored is None or stored[0]!=account_key or len(obs)!=1
                        or tuple(obs[0])!=(
                            receipt["observed_at"],post.get("like_count"),
                            post.get("comments_count"),int("like_count" in post),
                            int("comments_count" in post))):
                    return result
            count=mirror.db.execute(
                "SELECT COUNT(*) FROM raw_post_observations WHERE request_id=?",
                (raw_id,)
            ).fetchone()[0]
            if count!=len(posts):
                return result
            if terminal=="verified_terminal" and (cursor_out is not None or len(posts)>=4):
                return result
            if terminal=="continued" and cursor_out is None:
                return result
            prev_chain,prev_cursor,previous_job=chain_hash,cursor_out,job_id
            last_terminal=terminal=="verified_terminal"
            result["pages_checked"]=number
            result["posts_checked"]+=len(posts)
        if (cp["page_seq"]!=len(rows) or cp["last_receipt"]!=rows[-1][11]
                or (json.loads(rows[-1][8]) and
                    cp["anchor_post_id"]!=json.loads(rows[-1][8])[-1])
                or cp["updated_at"]!=rows[-1][7]
                or cp["chain_hash"]!=prev_chain
                or cp["cursor"]!=prev_cursor or
                cp["status"]!=("terminal" if last_terminal else "open")):
            return result
        if oldest is not None:
            result["oldest_published_at"]=oldest.isoformat().replace("+00:00","Z")
        if oldest is not None and oldest<=cutoff_at:
            result["synthetic_cutoff_verified"]=True
            result["coverage_status"]="cutoff_reached"
        elif last_terminal:
            result["synthetic_cutoff_verified"]=True
            result["coverage_status"]="verified_terminal"
        else:
            result["coverage_status"]="insufficient_depth"
        return result
    except (ValueError,TypeError,KeyError,sqlite3.Error,json.JSONDecodeError):
        raise OfflineHistoryCoverageError(
            "Offline history cutoff proof is malformed"
        ) from None
