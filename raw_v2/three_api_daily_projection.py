"""Offline proof projector connecting three-API staging with the KST gap ledger.

This is a crash-recoverable, *idempotent* projection, not a live scheduler.
The source snapshot must be explicitly bound to ONE KST day before the first
lease; no fabricated observation timestamps or implicit day attribution.
Source and target are isolated SQLite DBs, so projection can lag the source
after a crash; running reconcile_all again repairs the gap without re-fetching.
We deliberately refuse evidence predating ordered-post proofs.

No credentials, network calls, or RAW production collection.sqlite access.
"""
from __future__ import annotations

import hashlib
import json
from pathlib import Path

from three_api_daily_gap import DailyGapLedger, GapEvidenceError, _day
from three_api_guarded_executor import GuardedThreeApiExecutor


class DailyProjectionError(ValueError):
    """Sanitized projection failure; never includes IDs or paging cursors."""


def _roster_digest(accounts):
    """Bind precisely the 294 account identities and their fixed ordering."""
    if not isinstance(accounts, (list, tuple)) or len(accounts) != 294:
        raise DailyProjectionError("Official 294-account roster required")
    pairs = []
    for index, account in enumerate(accounts, 1):
        if not isinstance(account, dict) or account.get("ordinal") != index:
            raise DailyProjectionError("Roster ordinal mismatch")
        key, username = account.get("key"), account.get("username")
        if not isinstance(key, str) or not key or not isinstance(username, str) or not username:
            raise DailyProjectionError("Invalid roster identity")
        pairs.append((index, key, username))
    if len({row[1] for row in pairs}) != 294 or len({row[2].casefold() for row in pairs}) != 294:
        raise DailyProjectionError("Duplicate roster identity")
    return hashlib.sha256(json.dumps(
        pairs, ensure_ascii=True, separators=(",", ":"),
    ).encode("ascii")).hexdigest()


def _bind_snapshot(db, day, roster_digest):
    """Write immutable source-day binding only before any staged/leased work."""
    db.execute("BEGIN IMMEDIATE")
    try:
        db.execute("""CREATE TABLE IF NOT EXISTS daily_snapshot_binding (
          binding_id INTEGER PRIMARY KEY CHECK(binding_id=1),
          day_kst TEXT NOT NULL, roster_digest TEXT NOT NULL
        )""")
        row = db.execute("SELECT day_kst,roster_digest FROM daily_snapshot_binding WHERE binding_id=1").fetchone()
        if row is None:
            if db.execute("SELECT COUNT(*) FROM jobs WHERE state!='pending'").fetchone()[0]:
                raise DailyProjectionError("Cannot bind an already-started snapshot")
            if db.execute("SELECT COUNT(*) FROM page_evidence").fetchone()[0]:
                raise DailyProjectionError("Cannot bind existing unassigned page evidence")
            db.execute(
                "INSERT INTO daily_snapshot_binding VALUES (1,?,?)",
                (day, roster_digest),
            )
        elif tuple(row) != (day,roster_digest):
            raise DailyProjectionError("Immutable daily snapshot binding mismatch")
        db.execute("COMMIT")
    except BaseException:
        db.execute("ROLLBACK")
        raise


class DailyEvidenceProjector:
    """Projects ordered, completed three-API staging evidence into a gap ledger."""

    def __init__(self, coordinator, ledger: DailyGapLedger, accounts, day):
        if not isinstance(ledger, DailyGapLedger):
            raise DailyProjectionError("Isolated gap ledger required")
        self.accounts = accounts
        self.day = _day(day)
        self._digest = _roster_digest(accounts)
        self.source = coordinator.db
        self.ledger = ledger
        if Path(coordinator.path).resolve() == ledger.path.resolve():
            raise DailyProjectionError("Source and gap DB must be separate")
        _bind_snapshot(self.source,self.day,self._digest)

    def _verify_binding(self):
        row=self.source.execute(
            "SELECT day_kst,roster_digest FROM daily_snapshot_binding WHERE binding_id=1"
        ).fetchone()
        if row is None or tuple(row) != (self.day,self._digest):
            raise DailyProjectionError("Daily snapshot binding changed")

    def project_account(self, ordinal):
        self._verify_binding()
        if type(ordinal) is not int or not 1 <= ordinal <= 294:
            raise DailyProjectionError("Invalid account ordinal")
        account_key=self.accounts[ordinal-1]["key"]
        self.ledger.begin_day(account_key,self.day)
        projected=0
        for page in range(1,4):
            proof=self.source.execute(
                """SELECT e.job_id,e.requested_cursor,e.response_cursor,
                          e.terminal, j.state,j.page,j.account_ordinal
                   FROM page_evidence e JOIN jobs j ON j.id=e.job_id
                   WHERE e.account_ordinal=? AND e.page=?""",
                (ordinal,page)
            ).fetchone()
            if proof is None:
                break
            if (proof["state"]!="done" or proof["page"]!=page
                    or proof["account_ordinal"]!=ordinal):
                raise DailyProjectionError("Uncommitted source page evidence")
            # Ordered proof must correspond 1:1 with the committed staged payload.
            ordered=self.source.execute(
                """SELECT o.position,o.post_id,s.post_id AS saved_post_id
                   FROM staging_post_order o
                   LEFT JOIN staging_posts s
                     ON s.job_id=o.job_id AND s.post_id=o.post_id
                     AND s.page=? AND s.account_ordinal=?
                   WHERE o.job_id=? ORDER BY o.position""",
                (page,ordinal,proof["job_id"])
            ).fetchall()
            saved_count=self.source.execute(
                "SELECT COUNT(*) FROM staging_posts WHERE job_id=?",
                (proof["job_id"],)
            ).fetchone()[0]
            if (len(ordered)!=saved_count
                    or [o["position"] for o in ordered]!=list(range(1,len(ordered)+1))
                    or any(o["saved_post_id"]!=o["post_id"] for o in ordered)):
                raise DailyProjectionError("Missing or inconsistent ordered staging proof")
            posts=[o["post_id"] for o in ordered]
            # An earlier successful projection is durable. Validate that source
            # still matches before skipping, protecting crash/restart replay.
            prior=self.ledger.db.execute(
                """SELECT requested_cursor,next_cursor,media_ids_json
                   FROM page_proof WHERE account_key=? AND day=?
                     AND phase='front' AND page_no=?""",
                (account_key,self.day,page)
            ).fetchone()
            if prior is not None:
                if (prior["requested_cursor"]!=proof["requested_cursor"]
                        or prior["next_cursor"]!=proof["response_cursor"]
                        or json.loads(prior["media_ids_json"])!=posts):
                    raise DailyProjectionError("Existing projection does not match source")
                continue
            status=self.ledger.status(account_key,self.day)
            if status["front_pages"]!=page-1:
                raise DailyProjectionError("Missing predecessor day projection")
            try:
                self.ledger.record_page(
                    account_key,self.day,phase="front",
                    requested_cursor=proof["requested_cursor"],
                    post_ids=posts,next_cursor=proof["response_cursor"]
                )
            except GapEvidenceError:
                raise DailyProjectionError("Source page cannot be projected safely") from None
            projected+=1
        return {"status":"projected", "account_ordinal":ordinal,
                "new_pages":projected,
                "daily_status":self.ledger.status(account_key,self.day)["status"]}

    def reconcile_all(self):
        """Replay committed source proofs only; never fetches a page."""
        self._verify_binding()
        results=[]
        for row in self.source.execute(
            """SELECT DISTINCT account_ordinal FROM page_evidence
               ORDER BY account_ordinal"""
        ):
            results.append(self.project_account(row["account_ordinal"]))
        return results


class DailyGuardedSnapshot:
    """Optional offline composition of GuardedThreeApiExecutor and daily ledger.

    No live sender is created. The source snapshot is bound before seeding.
    A separate daily snapshot DB is required for each KST day.
    """

    def __init__(self, source_db, ledger_db, accounts, references, environment,
                 day, *, http_client=None):
        source_db=Path(source_db)
        ledger_db=Path(ledger_db)
        if source_db.resolve()==ledger_db.resolve():
            raise DailyProjectionError("Separate staging and gap stores required")
        self.executor=GuardedThreeApiExecutor(
            source_db,accounts,references,environment,http_client=http_client
        )
        try:
            self.ledger=DailyGapLedger(ledger_db)
            try:
                self.projector=DailyEvidenceProjector(
                    self.executor._runner.coordinator,self.ledger,accounts,day
                )
                self.executor.seed()
            except BaseException:
                self.ledger.close()
                raise
        except BaseException:
            self.executor.close()
            raise

    def close(self):
        self.executor.close()
        self.ledger.close()

    def run_one(self, lane, *, now=None):
        """Capture synthetic envelope; project only after committed evidence."""
        result=self.executor.run_one(lane,now=now)
        if result.get("status")!="staged":
            return result
        try:
            projection=self.projector.project_account(result["account_ordinal"])
        except Exception:
            # Source page is already durably committed. Do NOT mark it undone
            # or retry HTTP. Operator must reconcile evidence before resuming.
            self.executor._runner.coordinator.observe_usage(lane,None,now=now)
            return {"status":"projection_blocked","lane":lane}
        return result | {"projection":projection}

    def reconcile_all(self):
        return self.projector.reconcile_all()

    def next_actions(self, day=None):
        # Status decisions only: no HTTP, sleeping, or automatic recovery.
        return self.ledger.next_actions(
            [a["key"] for a in self.projector.accounts],
            self.projector.day if day is None else day,
        )
