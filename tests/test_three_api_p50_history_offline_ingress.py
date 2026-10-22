"""Offline ingress preflight must not mutate durable stores on invalid input."""
from pathlib import Path
from types import SimpleNamespace
import sys
import unittest
from unittest.mock import patch
from datetime import date, datetime, timezone
import tempfile

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "raw_v2"))
from three_api_p50_history_offline_ingress import ingest_offline_history_response


class OfflineHistoryIngressTests(unittest.TestCase):
    def test_non_success_response_never_touches_capture(self):
        class ForbiddenCapture:
            def __getattr__(self, name):
                raise AssertionError("Capture accessed before preflight")
        request = SimpleNamespace(
            phase="history", history_offline_only=True, request_id="req-1")
        for status in (199, 400, 429, 500):
            response = SimpleNamespace(request_id="req-1", http_status=status)
            with self.subTest(status=status), self.assertRaises(ValueError):
                ingest_offline_history_response(
                    ForbiddenCapture(), object(), request, response)

    def test_unanchored_history_never_touches_capture(self):
        from three_api_p50_history_offline_ingress import (
            ingest_and_commit_offline_history_page)
        store = SimpleNamespace(read=lambda _: {
            "state": "ready", "cursor": None})
        request = SimpleNamespace(
            phase="history", account_key="P50_US_001", cursor=None)
        with self.assertRaises(ValueError):
            ingest_and_commit_offline_history_page(
                store, object(), object(), request, object(),
                oldest_post_at=None)

    def test_mismatched_history_cursor_never_touches_capture(self):
        from three_api_p50_history_offline_ingress import (
            ingest_and_commit_offline_history_page)
        store = SimpleNamespace(read=lambda _: {
            "state": "continuation_ready", "cursor": "expected"})
        request = SimpleNamespace(
            phase="history", account_key="P50_US_001", cursor="stale")
        with self.assertRaises(ValueError):
            ingest_and_commit_offline_history_page(
                store, object(), object(), request, object(),
                oldest_post_at=None)

    def test_invalid_completion_metadata_never_touches_capture(self):
        from datetime import datetime
        from three_api_p50_history_offline_ingress import (
            ingest_and_commit_offline_history_page)
        store = SimpleNamespace(read=lambda _: {
            "state": "continuation_ready", "cursor": "expected"})
        request = SimpleNamespace(
            phase="history", account_key="P50_US_001", cursor="expected")
        for metadata in (
            {"oldest_post_at": None, "terminal": "yes"},
            {"oldest_post_at": datetime(2026, 10, 22), "terminal": False},
        ):
            with self.subTest(metadata=metadata), self.assertRaises(ValueError):
                ingest_and_commit_offline_history_page(
                    store, object(), object(), request, object(), **metadata)

    def test_ingest_then_commit_order_and_receipt_failure(self):
        from three_api_p50_history_offline_ingress import (
            ingest_and_commit_offline_history_page)
        request = SimpleNamespace(
            phase="history", history_offline_only=True,
            account_key="P50_US_001", cursor="cursor-1", request_id="req-1")
        store = SimpleNamespace(read=lambda _: {
            "state": "continuation_ready", "cursor": "cursor-1"})
        capture = SimpleNamespace(get_capture_for_request=lambda _: SimpleNamespace(
            capture_id="cap-1"))
        events = []
        def ingest(*args):
            events.append("raw")
            return "receipt-1"
        def commit(*args, **kwargs):
            events.append("cursor")
            self.assertEqual(kwargs["capture_id"], "cap-1")
            return {"state": "continuation_ready"}
        with patch("three_api_p50_history_offline_ingress.ingest_offline_history_response",
                   side_effect=ingest), patch(
                   "three_api_p50_history_commit.commit_persisted_history_page",
                   side_effect=commit):
            result = ingest_and_commit_offline_history_page(
                store, capture, object(), request, object(),
                oldest_post_at=datetime(2026, 1, 1, tzinfo=timezone.utc))
        self.assertEqual(events, ["raw", "cursor"])
        self.assertEqual(result["history"]["state"], "continuation_ready")

        events.clear()
        def fail_raw(*args):
            events.append("raw")
            raise OSError("RAW unavailable")
        with patch("three_api_p50_history_offline_ingress.ingest_offline_history_response",
                   side_effect=fail_raw), patch(
                   "three_api_p50_history_commit.commit_persisted_history_page",
                   side_effect=commit):
            with self.assertRaises(OSError):
                ingest_and_commit_offline_history_page(
                    store, capture, object(), request, object(),
                    oldest_post_at=datetime(2026, 1, 1, tzinfo=timezone.utc))
        self.assertEqual(events, ["raw"])

    def test_sqlite_cursor_persists_only_after_verified_commit(self):
        from three_api_p50_history_store import P50HistoryStore
        from three_api_p50_history_offline_ingress import (
            ingest_and_commit_offline_history_page)
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "p50" / "history.sqlite"
            keys = [f"P50_US_{i:03d}" for i in range(1, 51)]
            with P50HistoryStore(path) as store:
                store.initialize(keys, date(2025, 10, 22))
                store.anchor_verified_latest_tail(
                    account_key=keys[0], next_cursor="tail",
                    capture_id="latest-cap", raw_receipt_id="latest-raw",
                    raw_receipt_verified=True, capture_verified=True)
                request = SimpleNamespace(
                    phase="history", history_offline_only=True,
                    account_key=keys[0], cursor="tail", request_id="req")
                capture = SimpleNamespace(
                    get_capture_for_request=lambda _: SimpleNamespace(
                        capture_id="history-cap"))
                def commit_real(store_arg, *_args, **kwargs):
                    self.assertEqual(kwargs["capture_id"], "history-cap")
                    return store_arg.commit_verified_page(
                        account_key=keys[0], expected_cursor="tail",
                        next_cursor="next-page",
                        oldest_post_at=datetime(2026, 1, 1, tzinfo=timezone.utc),
                        terminal=False, capture_id="history-cap",
                        raw_receipt_id="history-raw",
                        raw_receipt_verified=True, capture_verified=True)
                with patch(
                    "three_api_p50_history_offline_ingress.ingest_offline_history_response",
                    return_value="raw-ok"), patch(
                    "three_api_p50_history_commit.commit_persisted_history_page",
                    side_effect=commit_real):
                    result = ingest_and_commit_offline_history_page(
                        store, capture, object(), request, object(),
                        oldest_post_at=datetime(2026, 1, 1, tzinfo=timezone.utc))
                self.assertEqual(result["history"]["state"], "continuation_ready")
            with P50HistoryStore(path) as reopened:
                self.assertEqual(reopened.read(keys[0])["cursor"], "next-page")
                self.assertEqual(reopened.read(keys[0])["pages_verified"], 1)
                self.assertEqual(reopened.read(keys[1])["pages_verified"], 0)

    def test_real_capture_raw_sqlite_history_pipeline_no_transport(self):
        """Exercise actual durable ports, not mocked receipt verification."""
        import sqlite3
        from three_api_capture import CaptureJournal
        from three_api_raw_writer import ContractRawWriter
        from three_api_request_spec import build_request_spec
        from three_api_runtime_contract import (
            CONTRACT_VERSION, RequestEnvelope, ResponseEnvelope, digest_sha256)
        from test_three_api_raw_writer import SCHEMA
        from three_api_p50_history_store import P50HistoryStore
        from three_api_p50_history_offline_ingress import (
            ingest_and_commit_offline_history_page)
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            history_path = root / "p50" / "history.sqlite"
            keys = [f"P50_US_{i:03d}" for i in range(1, 51)]
            journal = CaptureJournal(str(root / "capture.sqlite"))
            self.addCleanup(journal.close)
            raw_path = root / "raw.sqlite"
            raw_db = sqlite3.connect(raw_path, isolation_level=None)
            self.addCleanup(raw_db.close)
            raw_db.executescript(SCHEMA)
            raw_db.execute("INSERT INTO accounts VALUES(?)", (keys[0],))
            raw = ContractRawWriter(raw_db)
            spec = build_request_spec("A", "nailartist", after="tail", limit=4)
            request = RequestEnvelope(
                contract_version=CONTRACT_VERSION,
                request_id="req-" + "a" * 64, run_id="offline-history",
                roster_digest="b" * 64, day_kst=date(2026, 10, 22),
                account_ordinal=1, account_key=keys[0],
                username="nailartist", lane="A", phase="history", page=4,
                cursor="tail", requested_at=datetime(2026, 10, 22, tzinfo=timezone.utc),
                request_spec=spec, api_version="v26.0",
                target_ig_user_id="17840000000000000",
                request_spec_digest=digest_sha256({
                    "api_version": "v26.0",
                    "target_ig_user_id": "17840000000000000", "spec": spec}),
                history_offline_only=True)
            response = ResponseEnvelope(
                CONTRACT_VERSION, request.request_id, 200,
                {"business_discovery": {
                    "id": "17840000000000099", "username": "nailartist",
                    "media": {"data": [{
                        "id": "1234567890", "media_type": "IMAGE",
                        "timestamp": "2026-01-01T10:00:00+0000",
                        "caption": "synthetic nail design",
                        "permalink": "https://www.instagram.com/p/SYNTHETIC/",
                    }], "paging": {"cursors": {"after": "next"}}}}},
                {"x-app-usage": '{"call_count":20}'},
                datetime(2026, 10, 22, tzinfo=timezone.utc), "next")
            with P50HistoryStore(history_path) as store:
                store.initialize(keys, date(2025, 10, 22))
                store.anchor_verified_latest_tail(
                    account_key=keys[0], next_cursor="tail",
                    capture_id="latest-cap", raw_receipt_id="latest-raw",
                    raw_receipt_verified=True, capture_verified=True)
                result = ingest_and_commit_offline_history_page(
                    store, journal, raw, request, response,
                    oldest_post_at=datetime(2026, 1, 1, 10, tzinfo=timezone.utc),
                    terminal=False)
                self.assertEqual(result["history"]["state"], "continuation_ready")
                self.assertEqual(store.read(keys[0])["cursor"], "next")
                self.assertEqual(store.read(keys[0])["pages_verified"], 1)
                self.assertIsNotNone(journal.get_capture_for_request(request.request_id))
                self.assertEqual(raw_db.execute(
                    "SELECT COUNT(*) FROM three_api_contract_receipts").fetchone()[0], 1)
            # Restart all three durable ports, then ingest the next page
            # without replaying the first response or opening a transport.
            journal.close()
            raw_db.close()
            journal2 = CaptureJournal(str(root / "capture.sqlite"))
            raw_db2 = sqlite3.connect(raw_path, isolation_level=None)
            try:
                from dataclasses import replace
                from three_api_p50_history_batch import ingest_offline_history_batch
                spec2 = build_request_spec("A", "nailartist", after="next", limit=4)
                request2 = replace(
                    request, request_id="req-" + "c" * 64, cursor="next",
                    page=5, request_spec=spec2,
                    request_spec_digest=digest_sha256({
                        "api_version": "v26.0",
                        "target_ig_user_id": "17840000000000000",
                        "spec": spec2}))
                payload2 = {"business_discovery": {
                    "id": "17840000000000099", "username": "nailartist",
                    "media": {"data": [{
                        "id": "1234567891", "media_type": "IMAGE",
                        "timestamp": "2025-10-01T10:00:00+0000",
                        "caption": "older synthetic nail",
                        "permalink": "https://www.instagram.com/p/SYNTHETIC2/",
                    }]}}}
                response2 = replace(response, request_id=request2.request_id,
                                    payload=payload2, next_cursor=None)
                with P50HistoryStore(history_path) as reopened:
                    self.assertEqual(reopened.read(keys[0])["cursor"], "next")
                    result2 = ingest_offline_history_batch(
                        reopened, journal2, ContractRawWriter(raw_db2),
                        [{"request": request2, "response": response2,
                          "oldest_post_at": datetime(
                              2025, 10, 1, 10, tzinfo=timezone.utc),
                          "terminal": True}])
                    self.assertEqual(result2["pages_committed"], 1)
                    self.assertEqual(reopened.read(keys[0])["pages_verified"], 2)
                    self.assertEqual(reopened.read(keys[0])["state"], "window_covered")
                    self.assertEqual(raw_db2.execute(
                        "SELECT COUNT(*) FROM three_api_contract_receipts"
                    ).fetchone()[0], 2)
                    self.assertEqual(raw_db2.execute(
                        "SELECT COUNT(*) FROM post_observations"
                    ).fetchone()[0], 2)
            finally:
                journal2.close()
                raw_db2.close()

    def test_fifty_accounts_real_capture_raw_sqlite_batch(self):
        """50 real Capture and RAW receipts; durable independent SQLite cursors."""
        import sqlite3
        from three_api_capture import CaptureJournal
        from three_api_raw_writer import ContractRawWriter
        from three_api_request_spec import build_request_spec
        from three_api_runtime_contract import (
            CONTRACT_VERSION, RequestEnvelope, ResponseEnvelope, digest_sha256)
        from test_three_api_raw_writer import SCHEMA
        from three_api_p50_history_store import P50HistoryStore
        from three_api_p50_history_batch import ingest_offline_history_batch
        keys = ([f"P50_US_{i:03d}" for i in range(1, 19)] +
                [f"P50_KR_{i:03d}" for i in range(1, 17)] +
                [f"P50_JP_{i:03d}" for i in range(1, 17)])
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            journal = CaptureJournal(str(root / "capture.sqlite"))
            db = sqlite3.connect(root / "raw.sqlite", isolation_level=None)
            try:
                db.executescript(SCHEMA)
                db.executemany("INSERT INTO accounts VALUES(?)",
                               [(key,) for key in keys])
                raw = ContractRawWriter(db)
                pages = []
                with P50HistoryStore(root / "p50" / "history.sqlite") as store:
                    store.initialize(keys, date(2025, 10, 22))
                    for i, key in enumerate(keys, 1):
                        store.anchor_verified_latest_tail(
                            account_key=key, next_cursor=f"tail-{i}",
                            capture_id=f"latest-capture-{i}",
                            raw_receipt_id=f"latest-raw-{i}",
                            raw_receipt_verified=True, capture_verified=True)
                        username = f"nail_{i}"
                        spec = build_request_spec(
                            "A", username, after=f"tail-{i}", limit=4)
                        request = RequestEnvelope(
                            contract_version=CONTRACT_VERSION,
                            request_id="req-" + f"{i:064x}",
                            run_id="offline-fifty", roster_digest="b" * 64,
                            day_kst=date(2026, 10, 22),
                            account_ordinal=i, account_key=key,
                            username=username, lane="A", phase="history",
                            page=4, cursor=f"tail-{i}",
                            requested_at=datetime(2026, 10, 22, tzinfo=timezone.utc),
                            request_spec=spec, api_version="v26.0",
                            target_ig_user_id="17840000000000000",
                            request_spec_digest=digest_sha256({
                                "api_version": "v26.0",
                                "target_ig_user_id": "17840000000000000",
                                "spec": spec}),
                            history_offline_only=True)
                        response = ResponseEnvelope(
                            CONTRACT_VERSION, request.request_id, 200,
                            {"business_discovery": {
                                "id": str(17840000000000000 + i),
                                "username": username,
                                "media": {"data": [{
                                    "id": str(900000000000 + i),
                                    "media_type": "IMAGE",
                                    "timestamp": "2026-01-01T10:00:00+0000",
                                    "caption": "synthetic nails",
                                    "permalink": f"https://www.instagram.com/p/SYNTHETIC{i}/",
                                }]}}},
                            {"x-app-usage": '{"call_count":20}'},
                            datetime(2026, 10, 22, tzinfo=timezone.utc),
                            None)
                        pages.append({
                            "request": request, "response": response,
                            "oldest_post_at": datetime(
                                2026, 1, 1, 10, tzinfo=timezone.utc),
                            "terminal": True})
                    result = ingest_offline_history_batch(
                        store, journal, raw, pages, max_pages=50)
                    self.assertEqual(result["pages_committed"], 50)
                    self.assertEqual(result["network_requests"], 0)
                    self.assertEqual(db.execute(
                        "SELECT COUNT(*) FROM three_api_contract_receipts"
                    ).fetchone()[0], 50)
                    self.assertEqual(db.execute(
                        "SELECT COUNT(*) FROM post_observations"
                    ).fetchone()[0], 50)
                    for key in keys:
                        self.assertEqual(store.read(key)["pages_verified"], 1)
                journal.close()
                db.close()
                with P50HistoryStore(root / "p50" / "history.sqlite") as reopened:
                    self.assertTrue(all(
                        reopened.read(key)["pages_verified"] == 1
                        for key in keys))
                check = sqlite3.connect(root / "raw.sqlite")
                try:
                    self.assertEqual(check.execute(
                        "SELECT COUNT(*) FROM three_api_contract_receipts"
                    ).fetchone()[0], 50)
                finally:
                    check.close()
            finally:
                journal.close()
                db.close()

    def test_terminal_cursor_contradiction_never_writes_raw(self):
        from three_api_p50_history_offline_ingress import (
            ingest_and_commit_offline_history_page)
        store = SimpleNamespace(read=lambda _: {
            "state": "continuation_ready", "cursor": "tail"})
        request = SimpleNamespace(
            phase="history", history_offline_only=True,
            account_key="P50_US_001", cursor="tail")
        response = SimpleNamespace(next_cursor="unexpected-next")
        with self.assertRaisesRegex(ValueError, "Terminal history"):
            ingest_and_commit_offline_history_page(
                store, object(), object(), request, response,
                oldest_post_at=None, terminal=True)

    def test_wrong_response_identity_never_touches_capture(self):
        request = SimpleNamespace(
            phase="history", history_offline_only=True, request_id="req-1")
        response = SimpleNamespace(request_id="req-other", http_status=200)
        with self.assertRaises(ValueError):
            ingest_offline_history_response(object(), object(), request, response)


if __name__ == "__main__":
    unittest.main()
