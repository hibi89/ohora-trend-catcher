"""OFFLINE only: validated 3-API front staging -> isolated RAW mirror.

Three independent SQLite stores (coordinator, KST ledger, synthetic mirror)
cannot commit together. Every replay checks immutable source snapshot,
committed page/cursor proof, exact ordered staging bytes and daily ID ledger
before a stable receipt is staged/promoted to the mirror. On interruption the
mirror's existing receipt holds the original observed_at, so replay needs no
HTTP or newly fabricated observation time.

Page-4+ catch-up inbox currently contains IDs, not full posts, and is
deliberately NOT projected as if full payload existed.
"""
from __future__ import annotations

import hashlib
import json
from pathlib import Path

from three_api_daily_projection import DailyEvidenceProjector
from three_api_raw_mirror import OfflineRawMirror, OfflineRawMirrorError


class OfflineRawProjectionError(ValueError):
    """No source page, cursor, payload, user ID or token in exception messages."""


class OfflineRawEvidenceProjector:
    def __init__(self, daily: DailyEvidenceProjector, mirror: OfflineRawMirror):
        if not isinstance(daily, DailyEvidenceProjector) or not isinstance(mirror, OfflineRawMirror):
            raise OfflineRawProjectionError("Verified daily projector and synthetic mirror required")
        paths = [Path(daily.source.execute("PRAGMA database_list").fetchone()[2]).resolve(),
                 daily.ledger.path.resolve(), mirror.path.resolve()]
        if len(set(paths)) != 3:
            raise OfflineRawProjectionError("Coordinator, daily ledger and mirror must be separate")
        if any(path.name.lower() == "collection.sqlite" for path in paths):
            raise OfflineRawProjectionError("Production collection database forbidden")
        self.daily = daily
        self.mirror = mirror

    def _receipt(self, ordinal, page):
        # Ordinal is bound to a specific immutable 294-account roster/day.
        return f"front.{self.daily.day}.{ordinal:03d}.p{page}"

    def _validated_page(self, ordinal, page):
        self.daily._verify_binding()
        if type(ordinal) is not int or not 1 <= ordinal <= 294:
            raise OfflineRawProjectionError("Account ordinal outside immutable snapshot")
        if type(page) is not int or page not in (1, 2, 3):
            raise OfflineRawProjectionError("Only verified front pages 1-3 supported")
        account = self.daily.accounts[ordinal - 1]["key"]
        source = self.daily.source
        evidence = source.execute(
            """SELECT e.job_id,e.receipt,e.requested_cursor,e.response_cursor,
                      j.state,j.account_ordinal,j.page
               FROM page_evidence e JOIN jobs j ON j.id=e.job_id
               WHERE e.account_ordinal=? AND e.page=?""",
            (ordinal,page)
        ).fetchone()
        if (evidence is None or evidence["state"] != "done"
                or evidence["account_ordinal"] != ordinal
                or evidence["page"] != page
                or not isinstance(evidence["receipt"], str)
                or not evidence["receipt"].startswith("sha256:")):
            raise OfflineRawProjectionError("Completed source receipt required")
        proof = self.daily.ledger.db.execute(
            """SELECT requested_cursor,next_cursor,media_ids_json FROM page_proof
               WHERE account_key=? AND day=? AND phase='front' AND page_no=?""",
            (account,self.daily.day,page)
        ).fetchone()
        if (proof is None or proof["requested_cursor"] != evidence["requested_cursor"]
                or proof["next_cursor"] != evidence["response_cursor"]):
            raise OfflineRawProjectionError("Verified daily cursor proof required")
        ordered = source.execute(
            """SELECT o.position,o.post_id,s.post_id AS stored_id,s.payload_json
               FROM staging_post_order o LEFT JOIN staging_posts s
               ON s.job_id=o.job_id AND s.account_ordinal=?
                 AND s.page=? AND s.post_id=o.post_id
               WHERE o.job_id=? ORDER BY o.position""",
            (ordinal,page,evidence["job_id"])
        ).fetchall()
        count = source.execute(
            "SELECT COUNT(*) FROM staging_posts WHERE job_id=?",
            (evidence["job_id"],)
        ).fetchone()[0]
        if (len(ordered) != count or len(ordered) > 4
                or [x["position"] for x in ordered] != list(range(1,len(ordered)+1))
                or any(x["stored_id"] != x["post_id"] for x in ordered)):
            raise OfflineRawProjectionError("Missing ordered page payload evidence")
        try:
            posts = [json.loads(x["payload_json"]) for x in ordered]
            expected_ids = json.loads(proof["media_ids_json"])
            if (expected_ids != [x["post_id"] for x in ordered]
                    or any(type(p) is not dict or p.get("id") != x["post_id"]
                           for p,x in zip(posts,ordered))):
                raise OfflineRawProjectionError("Daily IDs differ from original staging")
            original = json.dumps(
                {"job_id":evidence["job_id"],"posts":posts},
                sort_keys=True,separators=(",",":"),ensure_ascii=False,allow_nan=False
            )
            digest = "sha256:" + hashlib.sha256(original.encode("utf-8")).hexdigest()
            if digest != evidence["receipt"]:
                raise OfflineRawProjectionError("Staging receipt integrity mismatch")
        except (ValueError, TypeError, KeyError):
            raise OfflineRawProjectionError("Invalid or inconsistent staged payload") from None
        return account, posts

    def project_page(self, ordinal, page, *, observed_at=None, stage_only=False):
        """Replay is idempotent; first projection requires explicit observed_at."""
        account, posts = self._validated_page(ordinal,page)
        request_id = self._receipt(ordinal,page)
        existing = self.mirror.db.execute(
            "SELECT observed_at FROM page_receipts WHERE request_id=?", (request_id,)
        ).fetchone()
        if observed_at is None:
            if existing is None:
                raise OfflineRawProjectionError("Verified observation time not supplied")
            observed_at = existing["observed_at"]
        try:
            staged = self.mirror.stage_page(request_id,account,observed_at,posts)
            if stage_only:
                return {"status":"staged", "account_ordinal":ordinal,
                        "page":page, "records":staged["post_count"]}
            committed = self.mirror.promote_page(request_id)
        except OfflineRawMirrorError:
            raise OfflineRawProjectionError("Isolated RAW mirror refused inconsistent evidence") from None
        return {"status":committed["status"], "account_ordinal":ordinal,
                "page":page,"records":staged["post_count"]}

    def reconcile_all(self, observed_at_by_page=None):
        """Re-project existing committed pages; never fetch or invent timestamps.

        The map is {(account_ordinal,page): timezone-aware capture time} for
        pages not previously staged. Existing mirror receipts keep their own
        observation time across restarts. Gap ID proof must exist first.
        """
        self.daily._verify_binding()
        if observed_at_by_page is None:
            observed_at_by_page = {}
        if not isinstance(observed_at_by_page, dict):
            raise OfflineRawProjectionError("Explicit observation-time map required")
        outputs = []
        rows = self.daily.source.execute(
            "SELECT account_ordinal,page FROM page_evidence ORDER BY page,account_ordinal"
        ).fetchall()
        for row in rows:
            key = (row["account_ordinal"],row["page"])
            outputs.append(self.project_page(
                *key,observed_at=observed_at_by_page.get(key)
            ))
        return outputs
