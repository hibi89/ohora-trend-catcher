"""P50 fixture dispatcher checks: deterministic serial resume without HTTP."""
from datetime import date, datetime, timezone
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch
import sys
import tempfile
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "raw_v2"))
from three_api_p50_history_fixture_runner import run_offline_history_fixture_dispatch
from three_api_p50_history_store import P50HistoryStore
from three_api_request_spec import build_request_spec


class HistoryFixtureDispatcherTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.path = Path(self.temp.name) / "p50" / "history.sqlite"
        self.keys = ([f"P50_US_{i:03d}" for i in range(1, 19)] +
                     [f"P50_KR_{i:03d}" for i in range(1, 17)] +
                     [f"P50_JP_{i:03d}" for i in range(1, 17)])
        self.names = {key: f"nail_{i}" for i, key in enumerate(self.keys)}
        with P50HistoryStore(self.path) as store:
            store.initialize(self.keys, date(2025, 10, 22))
            for i in range(2):
                store.anchor_verified_latest_tail(
                    account_key=self.keys[i], next_cursor=f"tail-{i}",
                    capture_id=f"latest-cap-{i}", raw_receipt_id=f"latest-raw-{i}",
                    raw_receipt_verified=True, capture_verified=True)
        self.receipts = []

    def fixture(self, i, cursor=None):
        key = self.keys[i]
        cursor = cursor if cursor is not None else f"tail-{i}"
        request = SimpleNamespace(
            account_key=key, cursor=cursor, username=self.names[key],
            phase="history", lane="A", history_offline_only=True,
            request_spec=build_request_spec("A", self.names[key],
                                            after=cursor, limit=4),
            request_id=f"req-{i}-{cursor}")
        return {"request": request,
                "response": SimpleNamespace(next_cursor=f"next-{i}",
                                         request_id=request.request_id, http_status=200),
                "oldest_post_at": datetime(2026, 1, 1, tzinfo=timezone.utc),
                "terminal": False}

    def persist(self, store, journal, writer, pages, *, max_pages):
        self.assertEqual(max_pages, 1)
        page = pages[0]
        req = page["request"]
        i = self.keys.index(req.account_key)
        decision = store.commit_verified_page(
            account_key=req.account_key, expected_cursor=req.cursor,
            next_cursor=page["response"].next_cursor,
            oldest_post_at=page["oldest_post_at"], terminal=False,
            capture_id=f"history-cap-{i}", raw_receipt_id=f"history-raw-{i}",
            raw_receipt_verified=True, capture_verified=True)
        self.receipts.append(req.account_key)
        return {"pages_committed": 1, "completed": [{
            "account_key": req.account_key, "state": decision["state"]}]}

    def dispatch(self, store, fixtures, *, max_pages=50):
        return run_offline_history_fixture_dispatch(
            store, None, None, self.keys, self.names,
            latest_complete_keys=set(self.keys),
            fixture_pages=fixtures, max_pages=max_pages)

    def test_missing_fixture_does_not_block_other_account_then_restart(self):
        p1 = self.fixture(1)
        with patch("three_api_p50_history_fixture_runner.ingest_offline_history_batch",
                   side_effect=self.persist):
            with P50HistoryStore(self.path) as store:
                first = self.dispatch(
                    store, {(self.keys[1], "tail-1"): p1})
                self.assertEqual(first["pages_committed"], 1)
                self.assertEqual(first["status"], "awaiting_fixture")
                self.assertIn(self.keys[0], first["deferred_accounts"])
            with P50HistoryStore(self.path) as reopened:
                p0 = self.fixture(0)
                second = self.dispatch(
                    reopened, {(self.keys[0], "tail-0"): p0,
                               (self.keys[1], "tail-1"): p1})
                self.assertEqual(second["pages_committed"], 1)
                self.assertEqual(reopened.read(self.keys[0])["cursor"], "next-0")
                self.assertEqual(reopened.read(self.keys[1])["cursor"], "next-1")
                self.assertEqual(reopened.read(self.keys[0])["pages_verified"], 1)
                self.assertEqual(reopened.read(self.keys[1])["pages_verified"], 1)
        self.assertEqual(self.receipts, [self.keys[1], self.keys[0]])

    def test_page_budget_one_and_restart_without_duplicate(self):
        pages = {(self.keys[i], f"tail-{i}"): self.fixture(i)
                 for i in range(2)}
        with patch("three_api_p50_history_fixture_runner.ingest_offline_history_batch",
                   side_effect=self.persist):
            with P50HistoryStore(self.path) as store:
                first = self.dispatch(store, pages, max_pages=1)
                self.assertEqual(first["status"], "step_budget_exhausted")
                self.assertEqual(first["pages_committed"], 1)
            with P50HistoryStore(self.path) as restarted:
                second = self.dispatch(restarted, pages, max_pages=1)
                self.assertEqual(second["pages_committed"], 1)
                self.assertEqual(self.receipts, self.keys[:2])

    def test_mismatched_request_does_not_commit_page(self):
        bad = self.fixture(0, cursor="stale")
        pages = {(self.keys[0], "tail-0"): bad}
        with patch("three_api_p50_history_fixture_runner.ingest_offline_history_batch"
                   ) as ingress:
            with P50HistoryStore(self.path) as store:
                with self.assertRaisesRegex(ValueError, "index differs"):
                    self.dispatch(store, pages)
                self.assertEqual(store.read(self.keys[0])["pages_verified"], 0)
                ingress.assert_not_called()

    def test_bad_later_fixture_preflight_prevents_earlier_writes(self):
        first = self.fixture(0)
        second = self.fixture(1)
        second["response"].http_status = 400
        pages = {
            (self.keys[0], "tail-0"): first,
            (self.keys[1], "tail-1"): second,
        }
        with patch("three_api_p50_history_fixture_runner.ingest_offline_history_batch"
                   ) as ingress:
            with P50HistoryStore(self.path) as store:
                with self.assertRaisesRegex(ValueError, "fixture response"):
                    self.dispatch(store, pages)
                self.assertEqual(store.read(self.keys[0])["pages_verified"], 0)
                self.assertEqual(store.read(self.keys[1])["pages_verified"], 0)
                ingress.assert_not_called()

    def test_repeated_cursor_rejected_before_any_account_commits(self):
        first = self.fixture(0)
        second = self.fixture(1)
        second["response"].next_cursor = "tail-1"
        pages = {(self.keys[0], "tail-0"): first,
                 (self.keys[1], "tail-1"): second}
        with patch("three_api_p50_history_fixture_runner.ingest_offline_history_batch"
                   ) as ingress:
            with P50HistoryStore(self.path) as store:
                with self.assertRaisesRegex(ValueError, "fixture boundary"):
                    self.dispatch(store, pages)
                self.assertEqual(store.read(self.keys[0])["pages_verified"], 0)
                self.assertEqual(store.read(self.keys[1])["pages_verified"], 0)
                ingress.assert_not_called()

    def test_invalid_page_budget_rejected_without_store(self):
        for limit in (0, 51, True, "10"):
            with self.subTest(limit=limit), self.assertRaisesRegex(
                    ValueError, "page budget"):
                run_offline_history_fixture_dispatch(
                    None, None, None, self.keys, self.names,
                    latest_complete_keys=set(), fixture_pages={},
                    max_pages=limit)


if __name__ == "__main__":
    unittest.main()
