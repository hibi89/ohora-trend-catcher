"""Isolated 50-account coordinator seed plan; no Meta calls or DB changes on import.

Preserves the existing three-lane, three-page assignment and all quota gates.
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
        """Seed 50 accounts x 3 pages, preserving original lane order."""
        self.db.execute("BEGIN IMMEDIATE")
        try:
            for account in range(1, 51):
                for page, lane in enumerate(("A", "C", "B"), 1):
                    self.db.execute(
                        """INSERT OR IGNORE INTO jobs
                           (account_ordinal,page,lane,phase) VALUES (?,?,?,?)""",
                        (account, page, lane, "front" if page == 1 else "catchup"),
                    )
            self.db.execute("COMMIT")
        except BaseException:
            self.db.execute("ROLLBACK")
            raise
