"""Synthetic-only catch-up page4+ payload projection into isolated RAW mirror.

Both page ID/cursor proof and SHA-256-sealed, allowlisted full post fields must
be durably present before mirror writes. No Meta client, token, production RAW
schema or live scheduling. Cross-DB writes are NOT a distributed transaction:
the inbox survives crashes and reconcile_all() repairs incomplete promotions.

Historical catchup_inbox rows (payload_version=0) are ID-only, not recoverable
as complete post records, and explicitly fail closed.
"""
from __future__ import annotations

import hashlib
import json
from pathlib import Path

from three_api_catchup_offline import (
    OfflineCatchupRunner, OfflineCatchupError, verified_catchup_payload,
)
from three_api_raw_mirror import OfflineRawMirror, OfflineRawMirrorError


class OfflineCatchupPayloadError(ValueError):
    """Sanitized error: no captions, request cursors, credentials or IDs."""


class OfflineCatchupPayloadProjector:
    def __init__(self, runner: OfflineCatchupRunner, mirror: OfflineRawMirror):
        if not isinstance(runner, OfflineCatchupRunner) or not isinstance(mirror, OfflineRawMirror):
            raise OfflineCatchupPayloadError("Synthetic runner and RAW mirror required")
        paths = [runner.ledger.path.resolve(), Path(runner.coordinator.path).resolve(),
                 mirror.path.resolve()]
        if len(set(paths)) != 3 or any(p.name.lower() == "collection.sqlite" for p in paths):
            raise OfflineCatchupPayloadError("Isolated three-database boundary required")
        self.runner = runner
        self.mirror = mirror

    @staticmethod
    def _receipt(account, day, page):
        # Stable, opaque; never depend on tokens or a new capture clock on replay.
        identity = json.dumps((account, day, page), separators=(",", ":")).encode()
        return "catchup." + hashlib.sha256(identity).hexdigest()

    def project_page(self, account, day, page, *, stage_only=False):
        if account not in self.runner.accounts or type(page) is not int or not 1 <= page <= 250:
            raise OfflineCatchupPayloadError("Unknown account or catch-up page")
        row = self.runner.db.execute(
            """SELECT * FROM catchup_inbox
               WHERE account_key=? AND gap_day=? AND page_no=?""",
            (account, day, page),
        ).fetchone()
        if row is None or row["applied"] != 1:
            raise OfflineCatchupPayloadError("Committed catch-up inbox required")
        try:
            posts = verified_catchup_payload(row)
            ids = json.loads(row["ids_json"])
        except (OfflineCatchupError, ValueError, TypeError):
            raise OfflineCatchupPayloadError("Unverified or legacy catch-up content") from None
        proof = self.runner.db.execute(
            """SELECT requested_cursor,next_cursor,media_ids_json FROM page_proof
               WHERE account_key=? AND day=? AND phase='catchup' AND page_no=?""",
            (account, day, page),
        ).fetchone()
        if proof is None:
            raise OfflineCatchupPayloadError("Missing authoritative catch-up cursor proof")
        try:
            if (proof["requested_cursor"] != row["requested_cursor"]
                    or proof["next_cursor"] != row["next_cursor"]
                    or json.loads(proof["media_ids_json"]) != ids
                    or ids != [p["id"] for p in posts]):
                raise OfflineCatchupPayloadError("Cursor or ordered IDs conflict with payload")
        except (ValueError, TypeError):
            raise OfflineCatchupPayloadError("Invalid committed ID proof") from None
        receipt = self._receipt(account, day, page)
        try:
            staged = self.mirror.stage_page(
                receipt, account, row["observed_at"], posts,
            )
            if stage_only:
                return {"status":"staged", "account_key":account,
                        "gap_day":day, "page_no":page, "posts":staged["post_count"]}
            out = self.mirror.promote_page(receipt)
        except OfflineRawMirrorError:
            raise OfflineCatchupPayloadError("Mirror rejected inconsistent catch-up payload") from None
        return {"status":out["status"], "account_key":account,
                "gap_day":day, "page_no":page, "posts":staged["post_count"]}

    def reconcile_all(self):
        """Revalidate all previously committed inbox pages; no fixture reads."""
        outputs = []
        rows = self.runner.db.execute(
            "SELECT account_key,gap_day,page_no FROM catchup_inbox "
            "WHERE applied=1 ORDER BY gap_day,account_key,page_no"
        ).fetchall()
        for row in rows:
            outputs.append(self.project_page(
                row["account_key"], row["gap_day"], row["page_no"]))
        return outputs

    def run_one(self, lane, today, *, now=None, stage_only=False):
        """Optional pipeline facade: repair existing proofs before new fixture reads.

        stage_only represents an interruption after durable inbox stage.
        On projection failure, keep the committed proof for later reconciliation
        and block further pages; never fetch or invent replacement payloads.
        """
        try:
            self.reconcile_all()
        except OfflineCatchupPayloadError:
            return {"status":"payload_projection_blocked"}
        result = self.runner.run_one(lane, today, now=now, stage_only=stage_only)
        if result.get("status") not in ("applied", "reconciled"):
            return result
        try:
            projected = self.project_page(
                result["account_key"],result["gap_day"],result["page_no"])
        except OfflineCatchupPayloadError:
            return {"status":"payload_projection_blocked"}
        return result | {"payload_projection":projected}
