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

from datetime import date, datetime, timezone
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
                if existing[0]!=294 or list(read)!=rows:
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
            elif row!=("daily",day,roster_digest):
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

    def ready_daily(self,epoch_id,lane):
        """Show only current round jobs with committed page predecessors."""
        if lane not in ("A","B","C"):
            raise EpochError("Unknown API lane")
        epoch=self.db.execute("SELECT kind FROM collection_epochs WHERE epoch_id=?",
                              (epoch_id,)).fetchone()
        if epoch is None or epoch[0]!="daily":
            raise EpochError("Registered daily epoch required")
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
                    source!=( "daily",target[1])):
                raise EpochError("Daily/history roster lineage mismatch")
            existing=self.history_checkpoint(history_epoch,account_key)
            if existing is not None:
                self.db.execute("COMMIT")
                return {"status":"already_initialized",**existing}
            rows=self.db.execute("""SELECT e.job_id,e.cursor_out,e.ordered_ids_json,
                   e.raw_receipt_id,e.chain_hash,e.terminal_basis,j.page_seq
                   FROM page_evidence_v2 e JOIN jobs_v2 j ON j.job_id=e.job_id
                   WHERE j.epoch_id=? AND j.account_key=? AND j.state='done'
                   ORDER BY j.page_seq DESC LIMIT 1""",
                   (daily_epoch,account_key)).fetchone()
            if rows is None:
                raise EpochError("Verified daily page receipt required")
            _,cursor,ids_json,receipt,chain,terminal,page=rows
            ids=json.loads(ids_json)
            anchor=ids[-1] if ids else None
            state="terminal" if cursor is None else "open"
            self.db.execute("""INSERT INTO cursor_checkpoints(
                epoch_id,account_key,stream,cursor,anchor_post_id,last_receipt,
                chain_hash,updated_at,page_seq,status)
                VALUES(?,?,'history',?,?,?,?,?,0,?)""",
                (history_epoch,account_key,cursor,anchor,receipt,chain,stamp,state))
            self.db.execute("COMMIT")
            return {"status":"initialized","cursor":cursor,"history_state":state,
                    "daily_page_seq":page}
        except BaseException:
            self.db.execute("ROLLBACK")
            raise
