"""Offline historical fixture -> durable Capture/RAW -> SQLite cursor integration."""
from dataclasses import replace
from datetime import date, datetime, timezone
from pathlib import Path
import sqlite3
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "raw_v2"))
sys.path.insert(0, str(ROOT / "tests"))

from test_three_api_contract_executor import make_request, make_response
from test_three_api_raw_writer import SCHEMA
from three_api_capture import CaptureJournal
from three_api_raw_writer import ContractRawWriter
from three_api_p50_history_store import P50HistoryStore
from three_api_p50_history_offline_ingress import (
    ingest_and_commit_offline_history_page, ingest_offline_history_response)
from three_api_request_spec import build_request_spec
from three_api_runtime_contract import digest_sha256


class HistoricalFixtureE2E(unittest.TestCase):
    def test_fixture_is_durable_and_cursor_survives_restart(self):
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            keys = [f"P50_US_{i:03d}" for i in range(1, 51)]
            history_path = base / "p50" / "history.sqlite"
            journal = CaptureJournal(str(base / "capture.sqlite"))
            raw_db = sqlite3.connect(":memory:", isolation_level=None)
            raw_db.executescript(SCHEMA)
            raw_db.execute("INSERT INTO accounts VALUES(?)", (keys[0],))
            writer = ContractRawWriter(raw_db)
            try:
                with P50HistoryStore(history_path) as store:
                    store.initialize(keys, date(2025, 10, 22))
                    store.anchor_verified_latest_tail(
                        account_key=keys[0], next_cursor="tail-1",
                        capture_id="latest-cap", raw_receipt_id="latest-raw",
                        raw_receipt_verified=True, capture_verified=True)
                    original = make_request()
                    spec = build_request_spec("A", original.username,
                                              after="tail-1", limit=4)
                    request = replace(
                        original, account_key=keys[0], phase="history", page=4,
                        cursor="tail-1", history_offline_only=True,
                        request_id="req-" + "h" * 64,
                        request_spec=spec,
                        request_spec_digest=digest_sha256({
                            "api_version": original.api_version,
                            "target_ig_user_id": original.target_ig_user_id,
                            "spec": spec}))
                    response = make_response(request)
                    # Durable Capture and RAW survive a journal restart before cursor commit.
                    first_receipt = ingest_offline_history_response(
                        journal, writer, request, response)
                    self.assertIsNotNone(first_receipt)
                    self.assertEqual(store.read(keys[0])["pages_verified"], 0)
                    journal.close()
                    journal = CaptureJournal(str(base / "capture.sqlite"))
                    with self.assertRaisesRegex(ValueError, "differs from persisted Capture"):
                        ingest_and_commit_offline_history_page(
                            store, journal, writer, request, response,
                            oldest_post_at=datetime(2025, 10, 1, tzinfo=timezone.utc),
                            terminal=True)
                    self.assertEqual(store.read(keys[0])["pages_verified"], 0)
                    self.assertEqual(store.read(keys[0])["cursor"], "tail-1")
                    with self.assertRaisesRegex(ValueError, "terminal boolean"):
                        from three_api_p50_history_commit import commit_persisted_history_page
                        commit_persisted_history_page(
                            store, journal, writer, account_key=keys[0],
                            capture_id="cap-" + request.request_id,
                            oldest_post_at=datetime(2026, 10, 19, 10, tzinfo=timezone.utc),
                            terminal="true")

                    with self.assertRaisesRegex(ValueError, "terminal flag"):
                        ingest_and_commit_offline_history_page(
                            store, journal, writer, request, response,
                            oldest_post_at=datetime(2026, 10, 19, 10, tzinfo=timezone.utc),
                            terminal=1)
                    self.assertEqual(store.read(keys[0])["pages_verified"], 0)
                    from three_api_p50_history_commit import commit_persisted_history_page
                    # A stale Capture must not be able to advance a different active cursor.
                    original_read = store.read
                    def stale_read(key):
                        row = original_read(key)
                        if key == keys[0]:
                            return dict(row, cursor="newer-cursor")
                        return row
                    from unittest.mock import patch
                    with patch.object(store, "read", side_effect=stale_read):
                        with self.assertRaisesRegex(ValueError, "active account cursor"):
                            commit_persisted_history_page(
                                store, journal, writer, account_key=keys[0],
                                capture_id="cap-" + request.request_id,
                                oldest_post_at=datetime(2026, 10, 19, 10, tzinfo=timezone.utc),
                                terminal=True)
                    self.assertEqual(store.read(keys[0])["cursor"], "tail-1")
                    from dataclasses import replace as replace_request
                    from three_api_p50_history_commit import commit_persisted_history_page
                    from types import SimpleNamespace
                    persisted = journal.get_capture("cap-" + request.request_id)
                    unsafe_request = replace_request(persisted.request, history_offline_only=False)
                    unsafe_record = replace_request(persisted, request=unsafe_request)
                    with patch.object(journal, "get_capture", return_value=unsafe_record):
                        with self.assertRaisesRegex(ValueError, "offline history Capture"):
                            commit_persisted_history_page(
                                store, journal, writer, account_key=keys[0],
                                capture_id=persisted.capture_id,
                                oldest_post_at=datetime(2026, 10, 19, 10, tzinfo=timezone.utc),
                                terminal=True)
                    self.assertEqual(store.read(keys[0])["pages_verified"], 0)
                    result = ingest_and_commit_offline_history_page(
                        store, journal, writer, request, response,
                        oldest_post_at=datetime(2026, 10, 19, 10, tzinfo=timezone.utc),
                        terminal=True)
                    self.assertEqual(result["history"]["state"], "terminal_verified")
                    self.assertEqual(store.read(keys[0])["pages_verified"], 1)
                    self.assertEqual(raw_db.execute(
                        "SELECT COUNT(*) FROM three_api_contract_receipts").fetchone()[0], 1)
                    self.assertEqual(journal.get_intent_outcome(request.request_id),
                                     "captured")
                    self.assertIsNotNone(writer.get_raw_receipt("cap-" + request.request_id))
                    self.assertEqual(raw_db.execute(
                        "SELECT COUNT(*) FROM post_observations").fetchone()[0], 1)
                with P50HistoryStore(history_path) as restarted:
                    self.assertEqual(restarted.read(keys[0])["pages_verified"], 1)
                    self.assertEqual(restarted.read(keys[0])["state"], "terminal_verified")
                    self.assertIsNone(restarted.read(keys[0])["cursor"])
            finally:
                journal.close()
                raw_db.close()


if __name__ == "__main__":
    unittest.main()
