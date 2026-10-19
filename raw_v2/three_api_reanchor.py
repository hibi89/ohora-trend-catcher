"""Offline re-anchoring after a catch-up cursor becomes unusable.

An operator explicitly opens a new proof chain from the CURRENT head; no
attempt is made to infer a new cursor from the old one. The old daily front
and catch-up proofs are immutable. A gap is verified only by finding the
EXACT original anchor ID in a contiguous new-page replay.

This records ID evidence only; it does not store full posts in RAW production.
No Meta requests, scheduling, or auto-retry are implemented.
"""
from __future__ import annotations

import json

from three_api_daily_gap import (
    DailyGapLedger, GapEvidenceError, PAGE_SIZE, _day, _cursor, _posts,
)

MAX_REANCHOR_PAGES=250


class ReanchorEvidenceStore:
    def __init__(self, ledger: DailyGapLedger):
        if not isinstance(ledger,DailyGapLedger):
            raise GapEvidenceError("Isolated daily ledger required")
        self.ledger=ledger
        self.db=ledger.db
        self.db.executescript("""
            CREATE TABLE IF NOT EXISTS reanchor_attempt (
              account_key TEXT NOT NULL, gap_day TEXT NOT NULL,
              replay_day TEXT NOT NULL, anchor_id TEXT NOT NULL,
              state TEXT NOT NULL CHECK(state IN ('active','verified','needs_review')),
              page_count INTEGER NOT NULL DEFAULT 0,
              next_cursor TEXT,
              PRIMARY KEY(account_key,gap_day,replay_day));
            CREATE TABLE IF NOT EXISTS reanchor_page_proof (
              account_key TEXT NOT NULL, gap_day TEXT NOT NULL,
              replay_day TEXT NOT NULL, page_no INTEGER NOT NULL,
              requested_cursor TEXT, next_cursor TEXT,
              ids_json TEXT NOT NULL, anchor_reached INTEGER NOT NULL,
              PRIMARY KEY(account_key,gap_day,replay_day,page_no));
        """)

    def state(self, account_key, gap_day, replay_day):
        self.ledger._account(account_key)
        gap_day=_day(gap_day);replay_day=_day(replay_day)
        row=self.db.execute(
            "SELECT state,page_count,next_cursor FROM reanchor_attempt "
            "WHERE account_key=? AND gap_day=? AND replay_day=?",
            (account_key,gap_day,replay_day)).fetchone()
        return None if row is None else {
            "status":row["state"],"page_count":row["page_count"],
            "next_cursor_available":row["next_cursor"] is not None,
        }

    def begin(self, account_key, gap_day, replay_day):
        """Manual declaration: old cursor is unavailable; replay from head.

        Safe to rerun for the same ACTIVE attempt; never overwrites old proof.
        """
        self.ledger._account(account_key)
        gap_day=_day(gap_day);replay_day=_day(replay_day)
        if replay_day < gap_day:
            raise GapEvidenceError("Replay must not precede the original gap")
        self.db.execute("BEGIN IMMEDIATE")
        try:
            gap=self.db.execute(
                "SELECT status,anchor_id FROM daily_window "
                "WHERE account_key=? AND day=?",(account_key,gap_day)).fetchone()
            if (gap is None or gap["status"] not in ("catchup_pending","needs_review")
                    or gap["anchor_id"] is None):
                raise GapEvidenceError("No outstanding anchored gap")
            previous=self.db.execute(
                "SELECT replay_day,state FROM reanchor_attempt "
                "WHERE account_key=? AND gap_day=? ORDER BY replay_day DESC LIMIT 1",
                (account_key,gap_day)).fetchone()
            if previous is not None:
                if previous["replay_day"] == replay_day and previous["state"]=="active":
                    self.db.execute("COMMIT")
                    return self.state(account_key,gap_day,replay_day)
                if previous["state"]=="active":
                    raise GapEvidenceError("An existing replay must be reviewed first")
                if previous["replay_day"] >= replay_day:
                    raise GapEvidenceError("Replay day must advance after review")
            self.db.execute(
                "INSERT INTO reanchor_attempt "
                "(account_key,gap_day,replay_day,anchor_id,state) "
                "VALUES (?,?,?,?,'active')",
                (account_key,gap_day,replay_day,gap["anchor_id"]))
            self.db.execute("COMMIT")
        except BaseException:
            self.db.execute("ROLLBACK")
            raise
        return self.state(account_key,gap_day,replay_day)

    def record_page(self, account_key, gap_day, replay_day, *,
                    requested_cursor=None, post_ids, next_cursor=None):
        self.ledger._account(account_key)
        gap_day=_day(gap_day);replay_day=_day(replay_day)
        requested_cursor=_cursor(requested_cursor)
        next_cursor=_cursor(next_cursor)
        ids=_posts(post_ids)
        if next_cursor and not ids:
            raise GapEvidenceError("Empty page cannot advance")
        if next_cursor and next_cursor==requested_cursor:
            raise GapEvidenceError("Unchanged paging cursor")
        self.db.execute("BEGIN IMMEDIATE")
        try:
            attempt=self.db.execute(
                "SELECT * FROM reanchor_attempt WHERE account_key=? AND gap_day=? "
                "AND replay_day=?",(account_key,gap_day,replay_day)).fetchone()
            if attempt is None or attempt["state"]!="active":
                raise GapEvidenceError("No active re-anchor attempt")
            expected=attempt["next_cursor"] if attempt["page_count"] else None
            if requested_cursor!=expected:
                raise GapEvidenceError("Replay cursor does not match predecessor")
            seen=list(self.db.execute(
                "SELECT ids_json,requested_cursor,next_cursor FROM reanchor_page_proof "
                "WHERE account_key=? AND gap_day=? AND replay_day=?",
                (account_key,gap_day,replay_day)))
            existing_ids={i for proof in seen for i in json.loads(proof["ids_json"])}
            if any(i in existing_ids for i in ids):
                raise GapEvidenceError("Repeated media ID during replay")
            seen_cursors={c for proof in seen for c in
                          (proof["requested_cursor"],proof["next_cursor"]) if c}
            if next_cursor in seen_cursors:
                raise GapEvidenceError("Replay cursor cycle")
            number=attempt["page_count"]+1
            if number>MAX_REANCHOR_PAGES:
                raise GapEvidenceError("Manual review required: replay page limit")
            found=attempt["anchor_id"] in ids
            state=("verified" if found else
                   "needs_review" if not next_cursor or number==MAX_REANCHOR_PAGES
                   else "active")
            self.db.execute(
                "INSERT INTO reanchor_page_proof VALUES (?,?,?,?,?,?,?,?)",
                (account_key,gap_day,replay_day,number,
                 requested_cursor,next_cursor,json.dumps(ids,separators=(",",":")),
                 int(found)))
            self.db.execute(
                "UPDATE reanchor_attempt SET state=?,page_count=?,next_cursor=? "
                "WHERE account_key=? AND gap_day=? AND replay_day=?",
                (state,number,next_cursor if state=="active" else None,
                 account_key,gap_day,replay_day))
            if state=="verified":
                updated=self.db.execute(
                    "UPDATE daily_window SET status='verified' "
                    "WHERE account_key=? AND day=? "
                    "AND status IN ('catchup_pending','needs_review') "
                    "AND anchor_id=?",
                    (account_key,gap_day,attempt["anchor_id"]))
                if updated.rowcount!=1:
                    raise GapEvidenceError("Original anchor changed during replay")
            self.db.execute("COMMIT")
        except BaseException:
            self.db.execute("ROLLBACK")
            raise
        return self.state(account_key,gap_day,replay_day)
