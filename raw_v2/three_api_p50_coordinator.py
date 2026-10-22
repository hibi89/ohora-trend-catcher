"""Isolated 50-account coordinator seed plan; no Meta calls or DB changes on import.

Balances each account's first page across A/B/C; preserves all quota gates.
Use only with a dedicated P50 coordinator database, never the P6/294 store.
"""
from __future__ import annotations

from pathlib import Path

from three_api_coordinator import Coordinator


class P50Coordinator(Coordinator):
    def __init__(self, path):
        target = Path(path)
        if "p50" not in {part.lower() for part in target.parts}:
            raise ValueError("P50 coordinator requires a dedicated p50 directory")
        super().__init__(target)

    def seed(self):
        """Seed 50 accounts × 3 sequential pages, balanced across three APIs.

        Account 1 uses A/C/B, account 2 B/A/C, account 3 C/B/A.
        Each account's later pages depend on predecessor RAW evidence.
        Existing stores with another assignment fail closed, not overwritten.
        """
        patterns = (("A", "C", "B"), ("B", "A", "C"), ("C", "B", "A"))
        expected = [
            (account, page, patterns[(account - 1) % 3][page - 1],
             "front" if page == 1 else "catchup")
            for account in range(1, 51)
            for page in range(1, 4)
        ]
        self.db.execute("BEGIN IMMEDIATE")
        try:
            for account, page, lane, phase in expected:
                self.db.execute(
                    """INSERT OR IGNORE INTO jobs
                       (account_ordinal,page,lane,phase) VALUES (?,?,?,?)""",
                    (account, page, lane, phase),
                )
            actual = [
                tuple(row) for row in self.db.execute(
                    "SELECT account_ordinal,page,lane,phase FROM jobs "
                    "ORDER BY account_ordinal,page"
                ).fetchall()
            ]
            if actual != expected:
                raise ValueError("P50 job assignment mismatch; preserve original store")
            self.db.execute("COMMIT")
        except BaseException:
            self.db.execute("ROLLBACK")
            raise
