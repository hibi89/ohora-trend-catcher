"""P6 P3 offline-only immutable roster, KST daily epochs, and history cursors.

This is a planning/ledger layer, NOT a Meta sender or unattended scheduler.
It accepts a caller-owned isolated SQLite connection, never opens a file,
never imports credentials, and never changes legacy Coordinator.jobs rows.

Frozen daily assignment: 294 official account ordinals, two rounds:
  round 1: A 1..150/page1, B 151..294/page1, C 1..150/page2
  round 2: A 151..294/page2, B 1..150/page3, C 151..294/page3
Page predecessor receipt/cursor proof must exist before a dependent page is
eligible. History and daily checkpoints live in DISTINCT epochs and survive
KST date rollover. Cross-DB RAW receipt verification is reserved for P4.
"""
from __future__ import annotations

from datetime import date, datetime, timezone, timedelta
from hashlib import sha256
import json
import re
import sqlite3

from three_api_request_spec import RequestSpec, build_request_spec

_USER = re.compile(r"[A-Za-z0-9._]{1,30}\Z")
_CURSOR = re.compile(r"[A-Za-z0-9_=-]{1,512}\Z")
_KEY = re.compile(r"(KR|US|JP)_[0-9]{3}\Z")
_COUNTS = {"KR":100,"US":101,"JP":93}
_SCHEMA_VERSION = "p6-epochs-v1"


class EpochError(ValueError):
    """Safe error messages never include tokens or untrusted paging URLs."""


def _json(value):
    return json.dumps(value,sort_keys=True,separators=(",",":"),
                      ensure_ascii=False,allow_nan=False)


def _hash(*values):
    return sha256(_json(values).encode("utf-8")).hexdigest()


def _utc(value):
    if not isinstance(value,datetime) or value.utcoffset() is None:
        raise EpochError("Timezone-aware timestamp required")
    return value.astimezone(timezone.utc).isoformat(timespec="microseconds").replace("+00:00","Z")


def _day(value):
    if not isinstance(value,date) or isinstance(value,datetime):
        raise EpochError("Explicit KST date required")
    return value.isoformat()


def _cursor(value):
    if value is not None and (not isinstance(value,str) or not _CURSOR.fullmatch(value)):
        raise EpochError("Invalid opaque paging cursor")
    return value


def _roster_rows(roster):
    if not isinstance(roster,(tuple,list)) or len(roster)!=294:
        raise EpochError("Exactly 294 official roster accounts required")
    rows=[]
    counts={key:0 for key in _COUNTS}
    keys=set()
    usernames=set()
    for i,item in enumerate(roster,1):
        if not isinstance(item,dict):
            raise EpochError("Invalid roster record")
        ordinal=item.get("ordinal")
        key=item.get("key",item.get("account_key"))
        username=item.get("username")
        if (type(ordinal) is not int or ordinal!=i or
                not isinstance(key,str) or not _KEY.fullmatch(key) or key in keys or
                not isinstance(username,str) or not _USER.fullmatch(username)):
            raise EpochError("Invalid official roster ordinal/account/username")
        if username.casefold() in usernames:
            raise EpochError("Duplicate username in official roster")
        country=key.split("_",1)[0]
        counts[country]+=1
        keys.add(key)
        usernames.add(username.casefold())
        rows.append((i,key,username,country))
    if counts!=_COUNTS:
        raise EpochError("Official KR/US/JP account counts do not match")
    return rows


class ThreeApiEpochStore:
    def __init__(self, db):
        if not isinstance(db,sqlite3.Connection) or db.in_transaction:
            raise EpochError("Idle caller-owned SQLite connection required")
        # Never give the P3 planner a directly operating RAW connection.
        names=db.execute("PRAGMA database_list").fetchall()
        for _,name,filename in names:
            if name=="main" and filename and (
                    filename.lower().replace("\\","/").endswith("/collection.sqlite")
                    or filename.lower().replace("\\","/").endswith("/control.sqlite")):
                raise EpochError("Operating database connection is not authorized")
        self.db=db
        self.db.execute("PRAGMA foreign_keys=ON")
        self._schema()

    def _schema(self):
        ddl=[
          """CREATE TABLE IF NOT EXISTS roster_versions(
             roster_digest TEXT PRIMARY KEY,member_count INTEGER NOT NULL,
             created_at TEXT NOT NULL)""",
          """CREATE TABLE IF NOT EXISTS roster_members(
             roster_digest TEXT NOT NULL REFERENCES roster_versions(roster_digest),
             account_key TEXT NOT NULL,ordinal INTEGER NOT NULL,
             username TEXT NOT NULL,country TEXT NOT NULL,
             PRIMARY KEY(roster_digest,account_key),
             UNIQUE(roster_digest,ordinal))""",
          """CREATE TABLE IF NOT EXISTS collection_epochs(
             epoch_id TEXT PRIMARY KEY,kind TEXT NOT NULL
             CHECK(kind IN ('daily','catchup','history')),
             day_kst TEXT,anchor_utc TEXT NOT NULL,cutoff_utc TEXT,
             roster_digest TEXT NOT NULL REFERENCES roster_versions(roster_digest),
             status TEXT NOT NULL DEFAULT 'open',
             CHECK((kind='daily' AND day_kst IS NOT NULL) OR
                   (kind<>'daily' AND day_kst IS NULL)))""",
          "CREATE UNIQUE INDEX IF NOT EXISTS idx_daily_epoch ON collection_epochs(day_kst,roster_digest) WHERE kind='daily'",
          """CREATE TABLE IF NOT EXISTS jobs_v2(
             job_id TEXT PRIMARY KEY, epoch_id TEXT NOT NULL REFERENCES collection_epochs(epoch_id),
             account_key TEXT NOT NULL,account_ordinal INTEGER NOT NULL,
             phase TEXT NOT NULL CHECK(phase IN ('daily_front','history','required_catchup')),
             page_seq INTEGER NOT NULL CHECK(page_seq>0),
             round_no INTEGER NOT NULL CHECK(round_no>=1),
             lane TEXT NOT NULL CHECK(lane IN ('A','B','C')),
             predecessor_job_id TEXT REFERENCES jobs_v2(job_id),
             state TEXT NOT NULL DEFAULT 'pending'
               CHECK(state IN ('pending','bound','done','not_required','blocked')),
             blocked_reason TEXT,
             UNIQUE(epoch_id,account_key,phase,page_seq))""",
          "CREATE INDEX IF NOT EXISTS idx_jobs_v2_ready ON jobs_v2(epoch_id,state,round_no,lane,account_ordinal,page_seq)",
          """CREATE TABLE IF NOT EXISTS request_bindings_v2(
             job_id TEXT PRIMARY KEY REFERENCES jobs_v2(job_id),
             request_id TEXT NOT NULL UNIQUE, request_spec_digest TEXT NOT NULL,
             requested_cursor TEXT, bound_at TEXT NOT NULL)""",
          """CREATE TABLE IF NOT EXISTS page_evidence_v2(
             request_id TEXT PRIMARY KEY REFERENCES request_bindings_v2(request_id),
             job_id TEXT NOT NULL UNIQUE REFERENCES jobs_v2(job_id),
             cursor_in TEXT,cursor_out TEXT,
             observed_at TEXT NOT NULL,ordered_ids_json TEXT NOT NULL,
             terminal_basis TEXT NOT NULL,
             source_evidence_id TEXT NOT NULL,raw_receipt_id TEXT NOT NULL,
             source_digest TEXT NOT NULL,chain_hash TEXT NOT NULL)""",
          """CREATE TABLE IF NOT EXISTS cursor_checkpoints(
             epoch_id TEXT NOT NULL REFERENCES collection_epochs(epoch_id),
             account_key TEXT NOT NULL,stream TEXT NOT NULL
               CHECK(stream IN ('daily','history')),
             cursor TEXT,anchor_post_id TEXT,last_receipt TEXT,
             chain_hash TEXT NOT NULL,updated_at TEXT NOT NULL,
             page_seq INTEGER NOT NULL CHECK(page_seq>=0),
             status TEXT NOT NULL CHECK(status IN ('open','terminal','review')),
             PRIMARY KEY(epoch_id,account_key,stream))""",
          """CREATE TABLE IF NOT EXISTS cursor_seen_v2(
             epoch_id TEXT NOT NULL REFERENCES collection_epochs(epoch_id),
             account_key TEXT NOT NULL,cursor TEXT NOT NULL,
             request_id TEXT NOT NULL,
             PRIMARY KEY(epoch_id,account_key,cursor))""",
          """CREATE TABLE IF NOT EXISTS coverage_ledger(
             epoch_id TEXT NOT NULL REFERENCES collection_epochs(epoch_id),
             account_key TEXT NOT NULL,verified_oldest TEXT,
             terminal_basis TEXT,gaps_json TEXT NOT NULL DEFAULT '[]',
             status TEXT NOT NULL CHECK(status IN ('open','partial','verified','needs_review')),
             PRIMARY KEY(epoch_id,account_key))""",
        ]
        for statement in ddl:
            self.db.execute(statement)

    def register_roster(self, roster, *, recorded_at):
        """Store immutable 294-account content hash; caller must supply official list."""
        rows=_roster_rows(roster)
        digest=_hash(rows)
        at=_utc(recorded_at)
        self.db.execute("BEGIN IMMEDIATE")
        try:
            existing=self.db.execute(
                "SELECT member_count FROM roster_versions WHERE roster_digest=?",
                (digest,)).fetchone()
            if existing is None:
                self.db.execute("INSERT INTO roster_versions VALUES(?,?,?)",(digest,294,at))
                self.db.executemany(
                    "INSERT INTO roster_members VALUES(?,?,?,?,?)",
                    [(digest,key,ordinal,user,country)
                     for ordinal,key,user,country in rows])
            else:
                read=self.db.execute("""SELECT ordinal,account_key,username,country
                    FROM roster_members WHERE roster_digest=? ORDER BY ordinal""",
                    (digest,)).fetchall()
                if existing[0]!=294 or [tuple(item) for item in read]!=rows:
                    raise EpochError("Immutable roster fingerprint collision")
            self.db.execute("COMMIT")
        except BaseException:
            self.db.execute("ROLLBACK")
            raise
        return digest

    def _require_roster(self,digest):
        row=self.db.execute("SELECT member_count FROM roster_versions "
                            "WHERE roster_digest=?",(digest,)).fetchone()
        if row is None or row[0]!=294:
            raise EpochError("Registered official roster required")

    @staticmethod
    def daily_assignment(ordinal,page):
        """Exact six-lane-slot design; NEVER infer it from phase names."""
        if type(ordinal) is not int or not 1<=ordinal<=294 or type(page) is not int or not 1<=page<=3:
            raise EpochError("Invalid fixed daily page assignment")
        if ordinal<=150:
            return {1:("A",1),2:("C",1),3:("B",2)}[page]
        return {1:("B",1),2:("A",2),3:("C",2)}[page]

    def seed_daily(self,day_kst,roster_digest,*,anchor_utc):
        """New KST day creates 882 fresh jobs without resetting history state."""
        day=_day(day_kst)
        anchor=_utc(anchor_utc)
        if anchor_utc.astimezone(timezone(timedelta(hours=9))).date()!=day_kst:
            raise EpochError("Daily epoch anchor does not belong to KST date")
        self._require_roster(roster_digest)
        epoch_id="daily-"+_hash(day,roster_digest)
        self.db.execute("BEGIN IMMEDIATE")
        try:
            row=self.db.execute("SELECT kind,day_kst,roster_digest FROM collection_epochs "
                                "WHERE epoch_id=?",(epoch_id,)).fetchone()
            if row is None:
                self.db.execute(
                    "INSERT INTO collection_epochs(epoch_id,kind,day_kst,anchor_utc,roster_digest) "
                    "VALUES(?,'daily',?,?,?)",(epoch_id,day,anchor,roster_digest))
                members=self.db.execute("SELECT ordinal,account_key FROM roster_members "
                    "WHERE roster_digest=? ORDER BY ordinal",(roster_digest,)).fetchall()
                for ordinal,account in members:
                    previous=None
                    for page in (1,2,3):
                        lane,round_no=self.daily_assignment(ordinal,page)
                        job_id="job-"+_hash(epoch_id,account,"daily_front",page)
                        self.db.execute("""INSERT INTO jobs_v2(
                            job_id,epoch_id,account_key,account_ordinal,
                            phase,page_seq,round_no,lane,predecessor_job_id)
                            VALUES(?,?,?,?,?,?,?,?,?)""",
                            (job_id,epoch_id,account,ordinal,"daily_front",
                             page,round_no,lane,previous))
                        previous=job_id
            elif tuple(row)!=("daily",day,roster_digest):
                raise EpochError("Existing daily epoch conflicts with roster")
            self.db.execute("COMMIT")
        except BaseException:
            self.db.execute("ROLLBACK")
            raise
        return epoch_id

    def create_history_epoch(self,roster_digest,*,anchor_utc,cutoff_utc):
        """A durable target epoch independent of future KST date changes."""
        anchor=_utc(anchor_utc)
        cutoff=_utc(cutoff_utc)
        if cutoff>=anchor:
            raise EpochError("History cutoff must precede fixed anchor")
        self._require_roster(roster_digest)
        epoch_id="history-"+_hash(roster_digest,anchor,cutoff)
        self.db.execute("INSERT OR IGNORE INTO collection_epochs("
            "epoch_id,kind,day_kst,anchor_utc,cutoff_utc,roster_digest)"
            "VALUES(?,'history',NULL,?,?,?)",
            (epoch_id,anchor,cutoff,roster_digest))
        return epoch_id

    def _is_latest_daily(self,epoch_id):
        """Only the newest KST day for this immutable roster can start work.

        Previously bound requests retain their state and can still commit
        after rollover; this gate applies only to *new* daily bindings.
        """
        epoch=self.db.execute(
            "SELECT kind,roster_digest,day_kst FROM collection_epochs "
            "WHERE epoch_id=?",(epoch_id,)
        ).fetchone()
        if epoch is None or epoch[0]!="daily":
            raise EpochError("Registered daily epoch required")
        latest=self.db.execute(
            "SELECT MAX(day_kst) FROM collection_epochs "
            "WHERE kind='daily' AND roster_digest=?",(epoch[1],)
        ).fetchone()[0]
        return epoch[2]==latest

    def ready_daily(self,epoch_id,lane):
        """Show only the latest KST round with committed predecessors."""
        if lane not in ("A","B","C"):
            raise EpochError("Unknown API lane")
        if not self._is_latest_daily(epoch_id):
            return []
        first=self.db.execute("SELECT MIN(round_no) FROM jobs_v2 "
            "WHERE epoch_id=? AND state IN ('pending','bound')",(epoch_id,)).fetchone()[0]
        if first is None:
            return []
        rows=self.db.execute("""SELECT j.job_id,j.account_key,j.account_ordinal,
                j.page_seq,j.lane,j.round_no,j.predecessor_job_id
            FROM jobs_v2 j
            LEFT JOIN jobs_v2 p ON p.job_id=j.predecessor_job_id
            WHERE j.epoch_id=? AND j.lane=? AND j.round_no=? AND j.state='pending'
              AND (j.predecessor_job_id IS NULL OR p.state='done')
            ORDER BY j.account_ordinal,j.page_seq""",
            (epoch_id,lane,first)).fetchall()
        keys=("job_id","account_key","account_ordinal","page_seq","lane","round_no","predecessor_job_id")
        return [dict(zip(keys,row)) for row in rows]

    def active_round(self,epoch_id):
        return self.db.execute("SELECT MIN(round_no) FROM jobs_v2 WHERE epoch_id=? "
            "AND state IN ('pending','bound')",(epoch_id,)).fetchone()[0]

    def summary(self,epoch_id):
        status=dict(self.db.execute(
            "SELECT state,COUNT(*) FROM jobs_v2 WHERE epoch_id=? GROUP BY state",(epoch_id,)))
        return {"epoch_id":epoch_id,"jobs":sum(status.values()),
                "states":status,"active_round":self.active_round(epoch_id)}

    def history_checkpoint(self,epoch_id,account_key):
        row=self.db.execute("""SELECT cursor,anchor_post_id,last_receipt,chain_hash,
                updated_at,page_seq,status FROM cursor_checkpoints
                WHERE epoch_id=? AND account_key=? AND stream='history'""",
                (epoch_id,account_key)).fetchone()
        if row is None:
            return None
        return dict(zip(("cursor","anchor_post_id","last_receipt","chain_hash",
                         "updated_at","page_seq","status"),row))

    def initialize_history_from_verified_daily(self,history_epoch,daily_epoch,account_key,
                                                *,at):
        """Create ONE history seed from an actual linked, receipted daily page.

        Subsequent calls never reset the history cursor, even on a new day.
        The P4 writer must verify cross-DB receipts before activation.
        """
        stamp=_utc(at)
        self.db.execute("BEGIN IMMEDIATE")
        try:
            target=self.db.execute("SELECT kind,roster_digest FROM collection_epochs "
                                   "WHERE epoch_id=?",(history_epoch,)).fetchone()
            source=self.db.execute("SELECT kind,roster_digest FROM collection_epochs "
                                   "WHERE epoch_id=?",(daily_epoch,)).fetchone()
            if (target is None or target[0]!="history" or source is None or
                    tuple(source)!=("daily",target[1])):
                raise EpochError("Daily/history roster lineage mismatch")
            existing=self.history_checkpoint(history_epoch,account_key)
            if existing is not None:
                self.db.execute("COMMIT")
                return {**existing,"history_state":existing["status"],"status":"already_initialized"}
            rows=self.db.execute("""SELECT e.job_id,e.cursor_out,e.ordered_ids_json,
                   e.raw_receipt_id,e.chain_hash,e.terminal_basis,j.page_seq
                   FROM page_evidence_v2 e JOIN jobs_v2 j ON j.job_id=e.job_id
                   WHERE j.epoch_id=? AND j.account_key=? AND j.state='done'
                   ORDER BY j.page_seq DESC LIMIT 1""",
                   (daily_epoch,account_key)).fetchone()
            if rows is None:
                raise EpochError("Verified daily page receipt required")
            _,cursor,ids_json,receipt,chain,terminal,page=rows
            # Without page3 or an earlier PROVEN terminal, page2/3 might
            # contain unseen posts. Never start history from page1 alone.
            if page<3 and terminal!="verified_terminal":
                raise EpochError("History root requires the full daily chain or verified terminal")
            ids=json.loads(ids_json)
            anchor=ids[-1] if ids else None
            state="terminal" if cursor is None else "open"
            self.db.execute("""INSERT INTO cursor_checkpoints(
                epoch_id,account_key,stream,cursor,anchor_post_id,last_receipt,
                chain_hash,updated_at,page_seq,status)
                VALUES(?,?,'history',?,?,?,?,?,0,?)""",
                (history_epoch,account_key,cursor,anchor,receipt,chain,stamp,state))
            if cursor is not None:
                # Initial history root is itself a seen cursor. Returning to
                # this anchor on later pages is a cycle, not a new page.
                self.db.execute("""INSERT INTO cursor_seen_v2(
                    epoch_id,account_key,cursor,request_id) VALUES(?,?,?,?)""",
                    (history_epoch,account_key,cursor,"seed:"+receipt))
            self.db.execute("COMMIT")
            return {"status":"initialized","cursor":cursor,"history_state":state,
                    "daily_page_seq":page}
        except BaseException:
            self.db.execute("ROLLBACK")
            raise

    def _job_row(self,job_id):
        row=self.db.execute("""SELECT j.job_id,j.epoch_id,j.account_key,
            j.account_ordinal,j.phase,j.page_seq,j.round_no,j.lane,
            j.predecessor_job_id,j.state,e.kind,e.roster_digest
            FROM jobs_v2 j JOIN collection_epochs e ON e.epoch_id=j.epoch_id
            WHERE j.job_id=?""",(job_id,)).fetchone()
        if row is None:
            raise EpochError("Unknown job identity")
        keys=("job_id","epoch_id","account_key","ordinal","phase","page_seq",
              "round_no","lane","predecessor_job_id","state","epoch_kind","roster_digest")
        return dict(zip(keys,row))

    def bind_daily(self,job_id,request_id,spec: RequestSpec,*,at):
        """Reserve one immutable 4-post request. Binding never sends HTTP."""
        stamp=_utc(at)
        if not isinstance(request_id,str) or not re.fullmatch(r"[A-Za-z0-9._:-]{1,160}",request_id):
            raise EpochError("Invalid immutable request identity")
        job=self._job_row(job_id)
        if job["epoch_kind"]!="daily" or job["phase"]!="daily_front" or job["state"]!="pending":
            raise EpochError("Daily job is not pending")
        if not self._is_latest_daily(job["epoch_id"]):
            raise EpochError("Older KST daily epoch cannot start a new request")
        member=self.db.execute("SELECT username FROM roster_members "
            "WHERE roster_digest=? AND account_key=?",
            (job["roster_digest"],job["account_key"])).fetchone()
        if member is None:
            raise EpochError("Unregistered roster identity")
        if not isinstance(spec,RequestSpec) or spec.limit!=4 or spec.lane!=job["lane"] or spec.username!=member[0]:
            raise EpochError("Immutable 3-lane/4-post request specification required")
        canonical=build_request_spec(spec.lane,spec.username,after=spec.after,limit=4,
            include_carousel_children=spec.include_carousel_children,
            include_account_metrics=("followers_count" in spec.account_fields))
        if canonical!=spec:
            raise EpochError("Request spec differs from approved field contract")
        if job["round_no"]!=self.active_round(job["epoch_id"]):
            raise EpochError("Daily round has not opened")
        if job["predecessor_job_id"] is None:
            expected_cursor=None
        else:
            previous=self.db.execute("""SELECT e.cursor_out,p.state
              FROM jobs_v2 p LEFT JOIN page_evidence_v2 e ON e.job_id=p.job_id
              WHERE p.job_id=?""",(job["predecessor_job_id"],)).fetchone()
            if previous is None or previous[1]!="done" or previous[0] is None:
                raise EpochError("Predecessor RAW receipt/cursor proof required")
            expected_cursor=previous[0]
        if spec.after!=expected_cursor:
            raise EpochError("Requested cursor violates predecessor evidence")
        digest=_hash(spec.__dict__)
        self.db.execute("BEGIN IMMEDIATE")
        try:
            row=self.db.execute("SELECT state FROM jobs_v2 WHERE job_id=?",
                                (job_id,)).fetchone()
            if row is None or row[0]!="pending":
                raise EpochError("Daily job lease changed")
            self.db.execute("""INSERT INTO request_bindings_v2(
                job_id,request_id,request_spec_digest,requested_cursor,bound_at)
                VALUES(?,?,?,?,?)""",(job_id,request_id,digest,expected_cursor,stamp))
            self.db.execute("UPDATE jobs_v2 SET state='bound' WHERE job_id=?",
                            (job_id,))
            self.db.execute("COMMIT")
        except BaseException:
            self.db.execute("ROLLBACK")
            raise
        return {"job_id":job_id,"request_id":request_id,
                "request_spec_digest":digest,"after_present":expected_cursor is not None}

    def commit_daily_page(self,job_id,request_id,*,observed_at,ordered_ids,
                          next_cursor,terminal_basis,source_evidence_id,
                          raw_receipt_id,source_digest):
        """Record linked synthetic proof atomically; P4 verifies raw DB receipt."""
        stamp=_utc(observed_at)
        next_cursor=_cursor(next_cursor)
        if not isinstance(ordered_ids,(tuple,list)) or len(ordered_ids)>4:
            raise EpochError("Four-post page upper bound violated")
        ids=list(ordered_ids)
        if any(not isinstance(v,str) or not v.isdecimal() or len(v)>64 for v in ids) or len(set(ids))!=len(ids):
            raise EpochError("Invalid ordered media IDs")
        if terminal_basis not in ("continued","verified_terminal"):
            raise EpochError("Unverified page terminal basis")
        if terminal_basis=="continued" and next_cursor is None:
            raise EpochError("Continued page requires genuine opaque cursor")
        if terminal_basis=="verified_terminal" and (next_cursor is not None or len(ids)>=4):
            raise EpochError("Full page/no-cursor needs manual terminal proof")
        if not all(isinstance(v,str) and v for v in
                   (request_id,source_evidence_id,raw_receipt_id,source_digest)):
            raise EpochError("Durable source and RAW receipt identity required")
        if not re.fullmatch(r"[0-9a-f]{64}",source_digest):
            raise EpochError("Safe source evidence digest required")
        job=self._job_row(job_id)
        if job["phase"]!="daily_front" or job["epoch_kind"]!="daily":
            raise EpochError("Only daily-front page proof supported here")
        bound=self.db.execute("SELECT request_id,requested_cursor FROM request_bindings_v2 "
                              "WHERE job_id=?",(job_id,)).fetchone()
        if bound is None or bound[0]!=request_id:
            raise EpochError("No immutable matching request binding")
        before=self.db.execute("SELECT source_digest,chain_hash FROM page_evidence_v2 "
                               "WHERE job_id=?",(job["predecessor_job_id"],)).fetchone() if job["predecessor_job_id"] else None
        if job["predecessor_job_id"] and before is None:
            raise EpochError("Missing predecessor committed page")
        parent_chain=before[1] if before else "genesis"
        if next_cursor is not None and next_cursor==bound[1]:
            raise EpochError("Cursor self-loop")
        chain=_hash(parent_chain,job_id,request_id,bound[1],next_cursor,ids,
                    source_evidence_id,raw_receipt_id,source_digest)
        existing=self.db.execute("SELECT chain_hash FROM page_evidence_v2 "
                                 "WHERE job_id=?",(job_id,)).fetchone()
        if existing is not None:
            if existing[0]!=chain:
                raise EpochError("Conflicting immutable page proof")
            return {"status":"already_committed","job_id":job_id}
        if job["state"]!="bound":
            raise EpochError("Cannot commit unbound or skipped daily page")
        self.db.execute("BEGIN IMMEDIATE")
        try:
            if next_cursor is not None:
                self.db.execute("""INSERT INTO cursor_seen_v2
                    (epoch_id,account_key,cursor,request_id) VALUES(?,?,?,?)""",
                    (job["epoch_id"],job["account_key"],next_cursor,request_id))
            self.db.execute("""INSERT INTO page_evidence_v2(
                request_id,job_id,cursor_in,cursor_out,observed_at,
                ordered_ids_json,terminal_basis,source_evidence_id,
                raw_receipt_id,source_digest,chain_hash)
                VALUES(?,?,?,?,?,?,?,?,?,?,?)""",
                (request_id,job_id,bound[1],next_cursor,stamp,_json(ids),
                 terminal_basis,source_evidence_id,raw_receipt_id,
                 source_digest,chain))
            self.db.execute("UPDATE jobs_v2 SET state='done' WHERE job_id=?",
                            (job_id,))
            if terminal_basis=="verified_terminal":
                # Not requested: descendant jobs retain their own audit rows
                # but never fabricate query_observation/HTTP receipt rows.
                self.db.execute("""UPDATE jobs_v2
                    SET state='not_required' WHERE epoch_id=? AND account_key=?
                    AND phase='daily_front' AND page_seq>? AND state='pending'""",
                    (job["epoch_id"],job["account_key"],job["page_seq"]))
            self.db.execute("""INSERT INTO cursor_checkpoints(
                epoch_id,account_key,stream,cursor,anchor_post_id,last_receipt,
                chain_hash,updated_at,page_seq,status)
                VALUES(?,?,'daily',?,?,?,?,?,?,?)
                ON CONFLICT(epoch_id,account_key,stream) DO UPDATE SET
                    cursor=excluded.cursor,anchor_post_id=excluded.anchor_post_id,
                    last_receipt=excluded.last_receipt,chain_hash=excluded.chain_hash,
                    updated_at=excluded.updated_at,page_seq=excluded.page_seq,
                    status=excluded.status""",
                (job["epoch_id"],job["account_key"],next_cursor,
                 ids[-1] if ids else None,raw_receipt_id,chain,
                 stamp,job["page_seq"],
                 "terminal" if terminal_basis=="verified_terminal" else "open"))
            self.db.execute("COMMIT")
        except BaseException:
            self.db.execute("ROLLBACK")
            raise
        return {"status":"committed","job_id":job_id,"observed_count":len(ids),
                "terminal":terminal_basis=="verified_terminal"}

    def block_daily_account(self,epoch_id,account_key,*,reason):
        """Explicit exception record; never count blocked pages as successes."""
        if not isinstance(reason,str) or not reason.strip() or len(reason)>128:
            raise EpochError("Explicit nonsecret block reason required")
        row=self.db.execute("SELECT kind FROM collection_epochs WHERE epoch_id=?",
                            (epoch_id,)).fetchone()
        if row is None or row[0]!="daily":
            raise EpochError("Daily epoch required")
        self.db.execute("BEGIN IMMEDIATE")
        try:
            if self.db.execute("""SELECT 1 FROM jobs_v2 WHERE epoch_id=?
                AND account_key=? AND state='bound'""",
                (epoch_id,account_key)).fetchone():
                raise EpochError("Cannot silently discard an already-bound HTTP outcome")
            self.db.execute("""UPDATE jobs_v2 SET state='blocked',
                blocked_reason=? WHERE epoch_id=? AND account_key=?
                AND state='pending'""",(reason,epoch_id,account_key))
            self.db.execute("COMMIT")
        except BaseException:
            self.db.execute("ROLLBACK")
            raise


    def _history_priority_open(self,history_epoch,account_key):
        """Daily fronts are global; manual-review holds are account-scoped."""
        row=self.db.execute("SELECT kind,roster_digest FROM collection_epochs "
            "WHERE epoch_id=?",(history_epoch,)).fetchone()
        if row is None or row[0]!="history":
            raise EpochError("Registered history epoch required")
        daily=self.db.execute("""SELECT epoch_id FROM collection_epochs
            WHERE kind='daily' AND roster_digest=?
            ORDER BY day_kst DESC LIMIT 1""",(row[1],)).fetchone()
        if daily is None:
            return False
        pending=self.db.execute("""SELECT COUNT(*) FROM jobs_v2
             WHERE epoch_id=? AND state IN ('pending','bound')""",
             (daily[0],)).fetchone()[0]
        unresolved=self.db.execute("""SELECT COUNT(*) FROM coverage_ledger
            WHERE epoch_id=? AND account_key=? AND status='needs_review'""",
            (daily[0],account_key)).fetchone()[0]
        # A daily job can be explicitly blocked without a coverage_ledger
        # row. Never treat that exception as successful front evidence for
        # the same account's history, even if it has an older valid cursor.
        # Blocking another account must not starve this verified account.
        blocked=self.db.execute("""SELECT COUNT(*) FROM jobs_v2
            WHERE epoch_id=? AND account_key=? AND phase='daily_front'
              AND state='blocked'""",
            (daily[0],account_key)).fetchone()[0]
        return pending==0 and unresolved==0 and blocked==0

    def schedule_one_history_page(self,history_epoch,account_key,lane,*,at):
        """Allocate at most one boundable history page/account, no HTTP."""
        _utc(at)
        if lane not in ("A","B","C"):
            raise EpochError("Invalid 3-API history lane")
        if not self._history_priority_open(history_epoch,account_key):
            raise EpochError("Newest KST daily front or required review has priority")
        checkpoint=self.history_checkpoint(history_epoch,account_key)
        if checkpoint is None or checkpoint["status"]!="open" or checkpoint["cursor"] is None:
            raise EpochError("No verified resumable history cursor")
        roster=self.db.execute("SELECT roster_digest FROM collection_epochs "
                               "WHERE epoch_id=?",(history_epoch,)).fetchone()[0]
        member=self.db.execute("SELECT ordinal FROM roster_members "
            "WHERE roster_digest=? AND account_key=?",
            (roster,account_key)).fetchone()
        if member is None:
            raise EpochError("History account missing from official roster")
        prior=self.db.execute("""SELECT job_id,state FROM jobs_v2
            WHERE epoch_id=? AND account_key=? AND phase='history'
            ORDER BY page_seq DESC LIMIT 1""",
            (history_epoch,account_key)).fetchone()
        if prior is not None and prior[1]!="done":
            raise EpochError("Unfinished history page must recover before new dispatch")
        page=checkpoint["page_seq"]+1
        if prior is not None and prior[0]!="job-"+_hash(history_epoch,account_key,"history",page-1):
            raise EpochError("History page chain differs from durable checkpoint")
        job_id="job-"+_hash(history_epoch,account_key,"history",page)
        self.db.execute("BEGIN IMMEDIATE")
        try:
            if self.db.execute("SELECT 1 FROM jobs_v2 WHERE job_id=?",
                               (job_id,)).fetchone():
                raise EpochError("History page already exists")
            self.db.execute("""INSERT INTO jobs_v2(
                job_id,epoch_id,account_key,account_ordinal,phase,page_seq,
                round_no,lane,predecessor_job_id,state)
                VALUES(?,?,?,?,?,?,?,?,?,'pending')""",
                (job_id,history_epoch,account_key,member[0],"history",
                 page,page,lane,prior[0] if prior else None))
            self.db.execute("COMMIT")
        except BaseException:
            self.db.execute("ROLLBACK")
            raise
        return {"job_id":job_id,"account_key":account_key,"lane":lane,
                "page_seq":page,"cursor":checkpoint["cursor"]}

    def bind_history(self,job_id,request_id,spec:RequestSpec,*,at):
        """History intent binding does not bypass daily/usage authorization."""
        stamp=_utc(at)
        job=self._job_row(job_id)
        if job["phase"]!="history" or job["epoch_kind"]!="history" or job["state"]!="pending":
            raise EpochError("History job not pending")
        if not self._history_priority_open(job["epoch_id"],job["account_key"]):
            raise EpochError("Newest daily front preempts new history HTTP")
        checkpoint=self.history_checkpoint(job["epoch_id"],job["account_key"])
        member=self.db.execute("SELECT username FROM roster_members WHERE "
            "roster_digest=? AND account_key=?",
            (job["roster_digest"],job["account_key"])).fetchone()
        if (checkpoint is None or checkpoint["status"]!="open" or member is None or
                not isinstance(spec,RequestSpec) or spec.lane!=job["lane"] or
                spec.username!=member[0] or spec.limit!=4 or
                spec.after!=checkpoint["cursor"] or
                job["page_seq"]!=checkpoint["page_seq"]+1):
            raise EpochError("History binding differs from verified persisted cursor")
        canonical=build_request_spec(spec.lane,spec.username,after=spec.after,
            limit=4,include_carousel_children=spec.include_carousel_children,
            include_account_metrics=("followers_count" in spec.account_fields))
        if spec!=canonical:
            raise EpochError("Unsupported history request specification")
        if not isinstance(request_id,str) or not re.fullmatch(r"[A-Za-z0-9._:-]{1,160}",request_id):
            raise EpochError("Invalid history immutable request ID")
        digest=_hash(spec.__dict__)
        self.db.execute("BEGIN IMMEDIATE")
        try:
            self.db.execute("""INSERT INTO request_bindings_v2(
                job_id,request_id,request_spec_digest,requested_cursor,bound_at)
                VALUES(?,?,?,?,?)""",
                (job_id,request_id,digest,spec.after,stamp))
            changed=self.db.execute("UPDATE jobs_v2 SET state='bound' "
                "WHERE job_id=? AND state='pending'",(job_id,)).rowcount
            if changed!=1:
                raise EpochError("History state changed during binding")
            self.db.execute("COMMIT")
        except BaseException:
            self.db.execute("ROLLBACK")
            raise
        return {"job_id":job_id,"request_id":request_id,"after_present":True}

    def commit_history_page(self,job_id,request_id,*,observed_at,ordered_ids,
                            next_cursor,terminal_basis,source_evidence_id,
                            raw_receipt_id,source_digest):
        """Advance ONLY the stored history cursor using an immutable page receipt.

        Already bound pages may finish after KST rollover; fresh history binds
        are blocked until the new daily front is resolved. This does not prove
        the 365-day cutoff, and never fabricates retroactive engagement.
        """
        stamp=_utc(observed_at)
        next_cursor=_cursor(next_cursor)
        if not isinstance(ordered_ids,(tuple,list)) or len(ordered_ids)>4:
            raise EpochError("Fixed history page limit=4")
        ids=list(ordered_ids)
        if any(not isinstance(v,str) or not v.isdecimal() or len(v)>64 for v in ids) or len(set(ids))!=len(ids):
            raise EpochError("Invalid history media IDs")
        if terminal_basis not in ("continued","verified_terminal"):
            raise EpochError("Unsupported history terminal proof")
        if terminal_basis=="continued" and next_cursor is None:
            raise EpochError("History must continue with a verified cursor")
        if terminal_basis=="verified_terminal" and (next_cursor is not None or len(ids)>=4):
            raise EpochError("Ambiguous full history page cannot be terminal")
        if (not all(isinstance(v, str) and v for v in
                    (source_evidence_id, raw_receipt_id, source_digest))
                or not re.fullmatch("[0-9a-f]{64}", source_digest)):
            raise EpochError("History page requires durable sanitized evidence")
        job=self._job_row(job_id)
        if job["phase"]!="history" or job["epoch_kind"]!="history":
            raise EpochError("History page/epoch mismatch")
        bound=self.db.execute("SELECT request_id,requested_cursor FROM request_bindings_v2 "
                              "WHERE job_id=?",(job_id,)).fetchone()
        if bound is None or bound[0]!=request_id:
            raise EpochError("Missing immutable history request binding")
        existing=self.db.execute("""SELECT request_id,cursor_in,cursor_out,
            observed_at,ordered_ids_json,terminal_basis,source_evidence_id,
            raw_receipt_id,source_digest FROM page_evidence_v2 WHERE job_id=?""",
            (job_id,)).fetchone()
        if existing is not None:
            expected=(request_id,bound[1],next_cursor,stamp,_json(ids),
                      terminal_basis,source_evidence_id,raw_receipt_id,source_digest)
            if tuple(existing)!=expected:
                raise EpochError("Conflicting immutable history page replay")
            # Idempotent replay must not hide a lost/tampered checkpoint after
            # a process restart. The page may be older than the current
            # checkpoint, so verify the latest committed page for this account
            # rather than incorrectly requiring cp.page_seq == job.page_seq.
            cp=self.history_checkpoint(job["epoch_id"],job["account_key"])
            if job["state"]!="done" or cp is None or cp["page_seq"]<job["page_seq"]:
                raise EpochError("History replay checkpoint is incomplete")
            latest=self.db.execute("""SELECT e.cursor_out,e.raw_receipt_id,
                e.chain_hash,e.observed_at,e.terminal_basis FROM page_evidence_v2 e
                JOIN jobs_v2 j ON j.job_id=e.job_id
                WHERE j.epoch_id=? AND j.account_key=? AND j.phase='history'
                  AND j.page_seq=? AND j.state='done'""",
                (job["epoch_id"],job["account_key"],cp["page_seq"])).fetchone()
            # A checkpoint must also identify the last durable history page.
            # Otherwise a forged page_seq can point to another valid receipt.
            if (cp["page_seq"] != self.db.execute(
                    "SELECT MAX(page_seq) FROM jobs_v2 WHERE epoch_id=? "
                    "AND account_key=? AND phase='history' AND state='done'",
                    (job["epoch_id"],job["account_key"])
                ).fetchone()[0]):
                raise EpochError("History replay checkpoint sequence differs")
            # A replay must not accept a forged last-media anchor even when
            # cursor, receipt and chain fields still match the latest page.
            latest_ids=self.db.execute("""SELECT e.ordered_ids_json
                FROM page_evidence_v2 e JOIN jobs_v2 j ON j.job_id=e.job_id
                WHERE j.epoch_id=? AND j.account_key=? AND j.phase='history'
                  AND j.page_seq=? AND j.state='done'""",
                (job["epoch_id"],job["account_key"],cp["page_seq"])).fetchone()
            if latest_ids is None:
                raise EpochError("History replay latest media evidence missing")
            anchored_ids=json.loads(latest_ids[0])
            if anchored_ids and cp["anchor_post_id"]!=anchored_ids[-1]:
                raise EpochError("History replay anchor differs from evidence")
            if (latest is None or cp["cursor"]!=latest[0]
                    or cp["last_receipt"]!=latest[1]
                    or cp["chain_hash"]!=latest[2]
                    or cp["updated_at"]!=latest[3]
                    or cp["status"]!=("terminal" if
                        latest[4]=="verified_terminal" else "open")):
                raise EpochError("History replay checkpoint differs from evidence")
            return {"status":"already_committed","job_id":job_id}
        cp=self.history_checkpoint(job["epoch_id"],job["account_key"])
        if cp is None:
            raise EpochError("Lost durable history checkpoint")
        parent_chain=cp["chain_hash"] if cp["page_seq"]==job["page_seq"]-1 else None
        if parent_chain is None and job["page_seq"]>1:
            previous=self.db.execute("SELECT e.chain_hash FROM page_evidence_v2 e "
                "WHERE e.job_id=?",(job["predecessor_job_id"],)).fetchone()
            parent_chain=previous[0] if previous else None
        if parent_chain is None:
            raise EpochError("History cursor chain continuity is unverified")
        chain=_hash(parent_chain,job_id,request_id,bound[1],next_cursor,ids,
                    source_evidence_id,raw_receipt_id,source_digest)
        if job["state"]!="bound" or cp["page_seq"]!=job["page_seq"]-1 or cp["cursor"]!=bound[1]:
            raise EpochError("History cursor changed; do not dispatch or skip")
        if next_cursor is not None and next_cursor==bound[1]:
            raise EpochError("History cursor self-loop")
        self.db.execute("BEGIN IMMEDIATE")
        try:
            if next_cursor is not None:
                self.db.execute("""INSERT INTO cursor_seen_v2(
                    epoch_id,account_key,cursor,request_id) VALUES(?,?,?,?)""",
                    (job["epoch_id"],job["account_key"],next_cursor,request_id))
            self.db.execute("""INSERT INTO page_evidence_v2(
                request_id,job_id,cursor_in,cursor_out,observed_at,ordered_ids_json,
                terminal_basis,source_evidence_id,raw_receipt_id,source_digest,chain_hash)
                VALUES(?,?,?,?,?,?,?,?,?,?,?)""",
                (request_id,job_id,bound[1],next_cursor,stamp,_json(ids),
                 terminal_basis,source_evidence_id,raw_receipt_id,source_digest,chain))
            changed=self.db.execute("UPDATE jobs_v2 SET state='done' "
                "WHERE job_id=? AND state='bound'",(job_id,)).rowcount
            if changed!=1:
                raise EpochError("History job ceased to be bound")
            self.db.execute("""UPDATE cursor_checkpoints SET
                cursor=?,anchor_post_id=?,last_receipt=?,chain_hash=?,
                updated_at=?,page_seq=?,status=?
                WHERE epoch_id=? AND account_key=? AND stream='history'""",
                (next_cursor,ids[-1] if ids else cp["anchor_post_id"],
                 raw_receipt_id,chain,stamp,job["page_seq"],
                 "terminal" if terminal_basis=="verified_terminal" else "open",
                 job["epoch_id"],job["account_key"]))
            self.db.execute("""INSERT INTO coverage_ledger(
                epoch_id,account_key,verified_oldest,terminal_basis,status)
                VALUES(?,?,NULL,?,'partial')
                ON CONFLICT(epoch_id,account_key) DO UPDATE SET
                terminal_basis=excluded.terminal_basis,status='partial'""",
                (job["epoch_id"],job["account_key"],terminal_basis))
            self.db.execute("COMMIT")
        except BaseException:
            self.db.execute("ROLLBACK")
            raise
        return {"status":"committed","job_id":job_id,
                "history_cursor_present":next_cursor is not None,
                "coverage_verified":False}
