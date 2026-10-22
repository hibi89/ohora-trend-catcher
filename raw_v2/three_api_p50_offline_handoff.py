"""Explicit offline P50 handoff from an existing latest collection day.

Reads dedicated P50 coordinator, Capture, and RAW databases. Never calls Meta.
The history database is the only state this command may advance.
"""
from __future__ import annotations

import argparse
from datetime import date, datetime
import json
from pathlib import Path

from collector import connect
from three_api_capture import CaptureJournal
from three_api_p50_coordinator import P50Coordinator
from three_api_p50_history_handoff import handoff_completed_latest_accounts
from three_api_p50_history_store import P50HistoryStore
from three_api_p50_roster import load_p50_roster
from three_api_raw_writer import ContractRawWriter


def run_offline_handoff(source, workspace, latest_day, capture_ids, cutoff_day):
    accounts, digest = load_p50_roster(source)
    root = Path(workspace).expanduser()
    if root.is_symlink() or root.name.lower() != "p50":
        raise ValueError("Dedicated P50 workspace required")
    root = root.resolve()
    if type(latest_day) is not date or type(cutoff_day) is not date:
        raise ValueError("Explicit latest day and cutoff day required")
    day_root = root / latest_day.isoformat()
    control = day_root / "coordinator.sqlite"
    capture_path = day_root / "capture.sqlite"
    raw_path = root / "p50_raw.sqlite"
    history_path = root / "history.sqlite"
    for path in (control, capture_path, raw_path):
        if path.is_symlink() or not path.is_file():
            raise ValueError("Existing dedicated latest receipts required")
    if history_path.is_symlink():
        raise ValueError("Symlink history database forbidden")
    # Do not initialize a missing latest database: only existing receipts
    # from an explicitly selected collection day can authorize a handoff.
    raw_db = connect(raw_path, accounts, digest)
    try:
        raw = ContractRawWriter(raw_db)
        coordinator = P50Coordinator(control)
        try:
            capture = CaptureJournal(str(capture_path))
            try:
                with P50HistoryStore(history_path) as history:
                    history.initialize([a["key"] for a in accounts], cutoff_day)
                    return handoff_completed_latest_accounts(
                        coordinator, history, capture, raw, accounts, capture_ids)
            finally:
                capture.close()
        finally:
            coordinator.db.close()
    finally:
        raw_db.close()


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", type=Path, required=True)
    parser.add_argument("--workspace", type=Path, required=True)
    parser.add_argument("--latest-day", type=date.fromisoformat, required=True)
    parser.add_argument("--cutoff-day", type=date.fromisoformat, required=True)
    parser.add_argument("--capture-map", type=Path, required=True,
                        help="JSON object mapping P50 account keys to verified tail Capture IDs")
    args = parser.parse_args(argv)
    capture_ids = json.loads(args.capture_map.read_text(encoding="utf-8"))
    result = run_offline_handoff(args.source, args.workspace, args.latest_day,
                                 capture_ids, args.cutoff_day)
    print(json.dumps(result, ensure_ascii=False, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
