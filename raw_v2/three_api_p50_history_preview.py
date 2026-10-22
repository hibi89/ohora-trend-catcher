"""Read-only P50 history planning CLI. No Meta transport or credential loading.

History is never implicitly dispatched: operator supplies independently
verified latest-complete keys. The original P50 latest collector is unchanged.
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path
import sqlite3

from three_api_p50_roster import load_p50_roster
from three_api_p50_history_plan import plan_next_history_page


class ReadOnlyHistoryStore:
    def __init__(self, path):
        path = Path(path)
        if path.name != "history.sqlite" or path.parent.name.lower() != "p50":
            raise ValueError("Dedicated P50 history store required")
        if not path.is_file() or path.is_symlink():
            raise ValueError("Existing non-symlink history store required")
        self.db = sqlite3.connect(f"file:{path.as_posix()}?mode=ro", uri=True)
        self.db.row_factory = sqlite3.Row

    def read(self, account_key):
        row = self.db.execute(
            "SELECT * FROM history_accounts WHERE account_key=?", (account_key,)).fetchone()
        return dict(row) if row else None

    def close(self):
        self.db.close()


def preview_history(source, workspace, *, latest_complete_keys, max_pages=1):
    accounts, _ = load_p50_roster(source)
    root = Path(workspace).expanduser()
    if root.name.lower() != "p50" or root.is_symlink():
        raise ValueError("Dedicated P50 workspace required")
    store = ReadOnlyHistoryStore(root / "history.sqlite")
    try:
        return plan_next_history_page(
            store, [account["key"] for account in accounts],
            latest_complete_keys=latest_complete_keys, max_pages=max_pages)
    finally:
        store.close()


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", type=Path, required=True)
    parser.add_argument("--workspace", type=Path, required=True)
    parser.add_argument("--latest-complete-keys-json", type=Path, required=True)
    parser.add_argument("--max-pages", type=int, default=1)
    args = parser.parse_args(argv)
    # Explicitly supplied evidence, never inferred from a possibly stale daily job.
    keys = json.loads(args.latest_complete_keys_json.read_text(encoding="utf-8"))
    if not isinstance(keys, list) or any(not isinstance(k, str) for k in keys):
        parser.error("Latest-complete evidence must be a JSON array of account keys")
    result = preview_history(args.source, args.workspace,
                             latest_complete_keys=keys, max_pages=args.max_pages)
    print(json.dumps(result, ensure_ascii=False, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
