"""Synthetic GraphHTTP integration for P50 manual history: never uses sockets."""
from contextlib import closing
from datetime import date, datetime, timezone
import csv
from pathlib import Path
import tempfile
import unittest
import sqlite3
from types import SimpleNamespace

import sys
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "raw_v2"))

from collector import connect
from three_api_capture import CaptureJournal
from three_api_credentials import CredentialReference
from three_api_graph_http import GraphHTTPClient
from three_api_live_lane_router import ThreeLaneMetaTransport
from three_api_p50_coordinator import P50Coordinator
from three_api_p50_history_store import P50HistoryStore
from three_api_p50_history_live_runner import history_step, _setup_bindings, _reserve, _final_history_status, _history_completion_status
from three_api_p50_roster import load_p50_roster
from three_api_raw_writer import ContractRawWriter
from three_api_transport import MetaTransport
from test_three_api_serial_bootstrap import MockResponse


class HistoryFinalStatusTests(unittest.TestCase):
    def test_durable_hold_overrides_success_at_budget_boundary(self):
        with closing(sqlite3.connect(":memory:")) as db:
            db.execute("CREATE TABLE lanes(lane TEXT, halted INTEGER, reason TEXT)")
            db.executemany("INSERT INTO lanes VALUES (?,?,?)",
                           [("A", 1, "missing_usage"), ("B", 0, None), ("C", 0, None)])
            coordinator = SimpleNamespace(db=db)
            self.assertEqual(_final_history_status([{"status": "recovered"}],
                                                   coordinator), "quota_hold")
            self.assertEqual(_final_history_status([{"status": "no_ready_work"}],
                                                   coordinator), "quota_hold")
            self.assertEqual(_final_history_status([{"status": "manual_review"}],
                                                   coordinator), "manual_review")
            for blocked in ("blocked_missing_cursor", "blocked_unverified_page",
                            "blocked_unknown_http", "account_blocked"):
                self.assertEqual(_final_history_status([{"status": blocked}],
                                                       coordinator), blocked)

    def test_completion_requires_fifty_verified_accounts(self):
        with closing(sqlite3.connect(":memory:")) as db:
            db.execute("CREATE TABLE lanes(lane TEXT, halted INTEGER, reason TEXT)")
            db.executemany("INSERT INTO lanes VALUES (?,?)",
                           [("A", 0, None), ("B", 0, None), ("C", 0, None)])
            coordinator = SimpleNamespace(db=db)
            counts = {"window_covered": 1, "continuation_ready": 49}
            self.assertEqual(_history_completion_status(
                [{"status": "recovered"}], coordinator, counts, 50),
                ("recovered", 1))
            self.assertEqual(_history_completion_status(
                [{"status": "no_ready_work"}], coordinator,
                {"window_covered": 1, "blocked_missing_cursor": 49}, 50),
                ("manual_review", 1))
            self.assertEqual(_history_completion_status(
                [{"status": "recovered"}], coordinator,
                {"window_covered": 49, "terminal_verified": 1}, 50),
                ("completed", 50))
            db.execute("UPDATE lanes SET halted=1,reason='missing_usage' WHERE lane='A'")
            self.assertEqual(_history_completion_status(
                [{"status": "recovered"}], coordinator,
                {"window_covered": 50}, 50),
                ("quota_hold", 50))

    def test_independent_usage_hold_does_not_mask_progress(self):
        with closing(sqlite3.connect(":memory:")) as db:
            db.execute("CREATE TABLE lanes(lane TEXT, halted INTEGER, reason TEXT)")
            db.executemany("INSERT INTO lanes VALUES (?,?,?)",
                           [("A", 1, "usage_limit"), ("B", 0, None), ("C", 0, None)])
            coordinator = SimpleNamespace(db=db)
            self.assertEqual(_final_history_status([{"status": "recovered"}],
                                                   coordinator), "recovered")
            db.execute("UPDATE lanes SET halted=1,reason='usage_limit' WHERE lane!='A'")
            self.assertEqual(_final_history_status([{"status": "recovered"}],
                                                   coordinator), "quota_hold")

    def test_unheld_lane_does_not_invent_quota_hold(self):
        with closing(sqlite3.connect(":memory:")) as db:
            db.execute("CREATE TABLE lanes(lane TEXT, halted INTEGER, reason TEXT)")
            db.executemany("INSERT INTO lanes VALUES (?,?)",
                           [("A", 0), ("B", 0), ("C", 0)])
            coordinator = SimpleNamespace(db=db)
            self.assertEqual(_final_history_status([{"status": "recovered"}],
                                                   coordinator), "recovered")


class HistoryHTTPIntegration(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.root = Path(temp.name)
        csvfile = self.root / "p50.csv"
        with csvfile.open("w", encoding="utf-8", newline="") as fp:
            writer = csv.DictWriter(fp, fieldnames=("account_key", "username", "country"))
            writer.writeheader()
            for country, count in (("US", 18), ("KR", 16), ("JP", 16)):
                for n in range(1, count + 1):
                    writer.writerow({"account_key": f"P50_{country}_{n:03d}",
                                     "username": f"nail_{country.lower()}_{n}",
                                     "country": country})
        self.accounts, self.digest = load_p50_roster(csvfile)
        home = self.root / "p50"
        home.mkdir()
        raw_db = connect(home / "p50_raw.sqlite", self.accounts, self.digest)
        raw_db.isolation_level = None
        self.addCleanup(raw_db.close)
        self.raw = ContractRawWriter(raw_db)
        self.coordinator = P50Coordinator(home / "2026-10-22" / "coordinator.sqlite")
        self.addCleanup(self.coordinator.db.close)
        self.coordinator.seed()
        self.capture = CaptureJournal(str(home / "history_capture.sqlite"))
        self.addCleanup(self.capture.close)
        self.store = P50HistoryStore(home / "history.sqlite")
        self.addCleanup(self.store.close)
        self.store.initialize([a["key"] for a in self.accounts], date(2025, 10, 22))
        _setup_bindings(self.store)
        self.now = datetime(2026, 10, 22, 12, tzinfo=timezone.utc)
        self.calls = 0
        self.names = []
        self.next_cursors = []
        self.post_count = 1
        self.fail_first = False
        self.unknown_first = False
        self.invalid_cursor_first = False
        self.usage_percent = 20
        self.post_date = '2026-10-21T10:00:00+0000'

        def sender(prepared, timeout):
            self.calls += 1
            if self.names:
                username = self.names.pop(0)
            else:
                username = self.accounts[self.calls - 1]["username"]
            next_cursor = self.next_cursors.pop(0) if self.next_cursors else "cursor" + str(self.calls)
            if self.unknown_first and self.calls == 1:
                raise OSError("synthetic interrupted HTTP outcome")
            response = MockResponse()
            response.headers = (
                {"x-app-usage": '{"call_count":' + str(self.usage_percent) + '}'}
                if self.usage_percent is not None else {})
            if self.invalid_cursor_first and self.calls == 1:
                response.status = 400
                response.payload = {"error": {
                    "code": 100, "message": "Invalid cursor supplied"}}
                return response
            if self.fail_first and self.calls == 1:
                response.status = 400
                response.payload = {"error": {"code": 110, "error_subcode": 2207013}}
                return response
            response.payload["business_discovery"]["username"] = username
            post = response.payload["business_discovery"]["media"]["data"][0]
            post["username"] = username
            post["id"] = str(1000000000 + self.calls * 10)
            post["timestamp"] = self.post_date
            for extra in range(1, self.post_count):
                repeated = dict(post)
                repeated["id"] = str(1000000000 + self.calls * 10 + extra)
                response.payload["business_discovery"]["media"]["data"].append(repeated)
            if next_cursor is not None:
                response.payload["business_discovery"]["media"]["paging"] = {
                    "cursors": {"after": next_cursor}}
            return response

        lanes = {}
        self.caller_ids = {}
        for lane, caller in (("A", "1001"), ("B", "1002"), ("C", "1003")):
            self.caller_ids[lane] = caller
            client = GraphHTTPClient(sender=sender, approved=True)
            lanes[lane] = MetaTransport(
                client=client,
                credentials=CredentialReference(lane, "TOKEN_" + lane, "ID_" + lane),
                caller_ig_id=caller, token="synthetic-" + lane, clock=lambda: self.now)
        self.transport = ThreeLaneMetaTransport(lanes)

    def anchor(self, n):
        account = self.accounts[n - 1]
        self.store.anchor_verified_latest_tail(
            account_key=account["key"], next_cursor="tail" + str(n),
            capture_id="latest-cap-" + str(n),
            raw_receipt_id="latest-raw-" + str(n),
            raw_receipt_verified=True, capture_verified=True)

    def step(self):
        return history_step(
            self.store, self.coordinator, self.capture, self.raw, self.transport,
            self.accounts, self.digest, self.caller_ids, "v26.0", now=self.now)

    def test_first_page_writes_raw_then_history_cursor(self):
        self.anchor(1)
        self.names = ["nail_us_1"]
        result = self.step()
        self.assertEqual(result["status"], "recovered")
        self.assertEqual(self.calls, 1)
        row = self.store.read(self.accounts[0]["key"])
        self.assertEqual(row["state"], "continuation_ready")
        self.assertEqual(row["cursor"], "cursor1")
        self.assertEqual(row["pages_verified"], 1)
        self.assertEqual(self.raw._db.execute(
            "SELECT COUNT(*) FROM three_api_contract_receipts").fetchone()[0], 1)

    def test_two_accounts_have_independent_cursors(self):
        self.anchor(1)
        self.anchor(2)
        self.names = ["nail_us_1", "nail_us_2", "nail_us_1"]
        for _ in range(3):
            self.assertEqual(self.step()["status"], "recovered")
        self.assertEqual(self.calls, 3)
        self.assertEqual(self.store.read(self.accounts[0]["key"])["pages_verified"], 2)
        self.assertEqual(self.store.read(self.accounts[1]["key"])["pages_verified"], 1)
        self.assertEqual(self.store.read(self.accounts[1]["key"])["cursor"], "cursor2")

    def test_terminal_page_is_excluded(self):
        self.anchor(1)
        self.names = ["nail_us_1"]
        self.next_cursors = [None]
        self.assertEqual(self.step()["state"], "terminal_verified")
        self.assertEqual(self.step()["status"], "no_ready_work")
        self.assertEqual(self.calls, 1)

    def test_raw_before_checkpoint_crash_recovers_without_http(self):
        self.anchor(1)
        self.names = ["nail_us_1"]
        original = self.store.commit_verified_page
        def crash_once(**kw):
            self.store.commit_verified_page = original
            raise RuntimeError("synthetic crash after RAW")
        self.store.commit_verified_page = crash_once
        self.assertEqual(self.step()["status"], "manual_review")
        self.assertEqual(self.calls, 1)
        self.assertEqual(self.store.read(self.accounts[0]["key"])["pages_verified"], 0)
        result = self.step()
        self.assertEqual(result["status"], "recovered")
        self.assertEqual(self.calls, 1)
        self.assertEqual(self.store.read(self.accounts[0]["key"])["pages_verified"], 1)

    def test_full_process_restart_keeps_raw_and_recovers_without_duplicate_http(self):
        self.anchor(1)
        self.names = ["nail_us_1"]
        actual_commit = self.store.commit_verified_page

        def crash_after_raw(**kwargs):
            self.store.commit_verified_page = actual_commit
            raise RuntimeError("synthetic power loss after durable RAW")
        self.store.commit_verified_page = crash_after_raw
        self.assertEqual(self.step()["status"], "manual_review")
        self.assertEqual(self.calls, 1)
        self.assertEqual(self.store.read(self.accounts[0]["key"])["cursor"], "tail1")
        binding = self.store.db.execute(
            "SELECT request_id FROM history_requests WHERE account_key=?",
            (self.accounts[0]["key"],)).fetchone()[0]
        capture_id = "cap-" + binding
        self.assertIsNotNone(self.raw.get_raw_receipt(capture_id))
        raw_file = self.root / "p50" / "p50_raw.sqlite"
        capture_file = self.root / "p50" / "history_capture.sqlite"
        history_file = self.store.path
        self.store.close()
        self.capture.close()
        self.raw._db.close()

        reopened_raw = connect(raw_file, self.accounts, self.digest)
        reopened_raw.isolation_level = None
        self.addCleanup(reopened_raw.close)
        self.raw = ContractRawWriter(reopened_raw)
        self.capture = CaptureJournal(str(capture_file))
        self.addCleanup(self.capture.close)
        self.store = P50HistoryStore(history_file)
        self.addCleanup(self.store.close)
        self.assertEqual(self.store.read(self.accounts[0]["key"])["cursor"], "tail1")
        self.assertIsNotNone(self.raw.get_raw_receipt(capture_id))
        self.assertEqual(self.step()["status"], "recovered")
        self.assertEqual(self.calls, 1, "Replaying durable Capture must never send HTTP again")
        self.assertEqual(self.store.read(self.accounts[0]["key"])["pages_verified"], 1)
        self.assertEqual(self.raw._db.execute(
            "SELECT COUNT(*) FROM three_api_contract_receipts").fetchone()[0], 1)
        self.assertEqual(self.raw._db.execute(
            "SELECT COUNT(*) FROM post_observations").fetchone()[0], 1)

    def test_bound_missing_capture_blocks_without_resend(self):
        self.anchor(1)
        account = self.accounts[0]
        row = self.store.read(account["key"])
        _reserve(self.store, account, row, "A", "req-missing", 4)
        self.assertEqual(self.step()["status"], "blocked_unknown_http")
        self.assertEqual(self.calls, 0)
        self.assertEqual(self.store.read(account["key"])["state"], "blocked_unknown_http")

    def test_pending_unknown_request_does_not_bypass_held_other_lane(self):
        self.anchor(1)
        self.anchor(2)
        account = self.accounts[0]
        row = self.store.read(account["key"])
        _reserve(self.store, account, row, "A", "req-uncertain", 4)
        self.coordinator.db.execute(
            "UPDATE lanes SET halted=1,reason='missing_usage' WHERE lane='B'")
        self.assertEqual(self.step()["status"], "blocked_unknown_http")
        self.assertEqual(self.calls, 0)
        self.assertEqual(self.step()["status"], "quota_hold")
        self.assertEqual(self.calls, 0)
        self.assertEqual(self.store.read(self.accounts[1]["key"])["pages_verified"], 0)
        lane = self.coordinator.db.execute(
            "SELECT halted,reason FROM lanes WHERE lane='B'").fetchone()
        self.assertEqual((lane["halted"], lane["reason"]), (1, "missing_usage"))

    def test_independent_app_failover_uses_b_when_a_usage_limited(self):
        self.anchor(1)
        self.names = ["nail_us_1"]
        self.coordinator.db.execute(
            "UPDATE lanes SET halted=1,reason='usage_limit' WHERE lane='A'")
        result = self.step()
        self.assertEqual(result["status"], "recovered")
        self.assertEqual(result["lane"], "B")
        self.assertEqual(self.calls, 1)
        held = self.coordinator.db.execute(
            "SELECT halted,reason FROM lanes WHERE lane='A'").fetchone()
        self.assertEqual((held["halted"], held["reason"]), (1, "usage_limit"))

    def test_all_independent_apps_usage_limited_stops(self):
        self.anchor(1)
        self.coordinator.db.execute(
            "UPDATE lanes SET halted=1,reason='usage_limit'")
        self.assertEqual(self.step()["status"], "quota_hold")
        self.assertEqual(self.calls, 0)

    def test_crossing_365_day_window_stops_without_more_http(self):
        self.anchor(1)
        self.names = ["nail_us_1"]
        self.post_date = "2025-10-20T12:00:00+0000"
        result = self.step()
        self.assertEqual(result["state"], "window_covered")
        self.assertEqual(self.store.read(self.accounts[0]["key"])["cursor"], None)
        self.assertEqual(self.store.read(self.accounts[0]["key"])["pages_verified"], 1)
        self.assertEqual(self.step()["status"], "no_ready_work")
        self.assertEqual(self.calls, 1)

    def test_reopen_real_sqlite_resumes_from_verified_cursor(self):
        self.anchor(1)
        self.names = ["nail_us_1", "nail_us_1"]
        self.assertEqual(self.step()["status"], "recovered")
        first_cursor = self.store.read(self.accounts[0]["key"])["cursor"]
        history_path = self.store.path
        self.store.close()
        self.store = P50HistoryStore(history_path)
        self.addCleanup(self.store.close)  # A reopened connection must close on Windows.
        self.assertEqual(self.store.read(self.accounts[0]["key"])["cursor"], first_cursor)
        self.assertEqual(self.step()["status"], "recovered")
        self.assertEqual(self.store.read(self.accounts[0]["key"])["pages_verified"], 2)
        self.assertEqual(self.calls, 2)

    def test_full_page_without_cursor_is_blocked_not_terminal(self):
        self.anchor(1)
        self.names = ["nail_us_1"]
        self.next_cursors = [None]
        self.post_count = 4
        result = self.step()
        self.assertEqual(result["status"], "blocked_missing_cursor")
        self.assertEqual(self.store.read(self.accounts[0]["key"])["state"],
                         "blocked_missing_cursor")
        self.assertEqual(self.store.read(self.accounts[0]["key"])["pages_verified"], 0)
        self.assertEqual(self.calls, 1)

    def test_reconcile_committed_page_lane_hold_after_crash(self):
        self.anchor(1)
        self.names = ["nail_us_1", "nail_us_1"]
        self.assertEqual(self.step()["status"], "recovered")
        request_id = self.store.db.execute(
            "SELECT request_id FROM history_requests WHERE account_key=?",
            (self.accounts[0]["key"],)).fetchone()[0]
        self.coordinator.db.execute(
            "UPDATE lanes SET halted=1,reason=?,ready_at=? WHERE lane='A'",
            ("history_pending:" + request_id, "2026-10-22T22:00:00+00:00"))
        self.assertEqual(self.step()["status"], "recovered")
        self.assertEqual(self.calls, 2)
        self.assertEqual(self.store.read(self.accounts[0]["key"])["pages_verified"], 2)

    def test_one_account_http_400_does_not_stall_other_safe_account(self):
        self.anchor(1)
        self.anchor(2)
        self.fail_first = True
        self.names = ["nail_us_1", "nail_us_2"]
        self.assertEqual(self.step()["status"], "account_blocked")
        self.assertEqual(self.step()["status"], "recovered")
        self.assertEqual(self.calls, 2)
        self.assertEqual(self.store.read(self.accounts[0]["key"])["state"],
                         "blocked_unverified_page")
        self.assertEqual(self.store.read(self.accounts[1]["key"])["pages_verified"], 1)

    def test_usage_at_85_preserves_raw_and_stops_followup_requests(self):
        self.anchor(1)
        self.anchor(2)
        self.names = ["nail_us_1"]
        self.usage_percent = 85
        first = self.step()
        self.assertEqual(first["status"], "recovered")
        self.assertEqual(first["usage_percent"], 85)
        self.assertEqual(self.store.read(self.accounts[0]["key"])["pages_verified"], 1)
        self.assertEqual(self.raw._db.execute(
            "SELECT COUNT(*) FROM three_api_contract_receipts").fetchone()[0], 1)
        self.usage_percent = 40
        followup = self.step()
        self.assertEqual(followup["status"], "recovered")
        self.assertEqual(followup["lane"], "B")
        self.assertEqual(self.calls, 2)
        self.assertEqual(self.store.read(self.accounts[1]["key"])["pages_verified"], 1)
        held = self.coordinator.db.execute(
            "SELECT halted,reason FROM lanes WHERE lane='A'").fetchone()
        self.assertEqual((held["halted"], held["reason"]), (1, "usage_limit"))

    def test_missing_usage_stops_and_keeps_cursor_without_resend(self):
        self.anchor(1)
        self.anchor(2)
        self.usage_percent = None
        self.names = ["nail_us_1"]
        first = self.step()
        self.assertEqual(first["status"], "manual_review")
        self.assertEqual(self.store.read(self.accounts[0]["key"])["cursor"], "tail1")
        self.assertEqual(self.store.read(self.accounts[0]["key"])["pages_verified"], 0)
        self.assertEqual(self.step()["status"], "quota_hold")
        self.assertEqual(self.calls, 1)
        # Missing usage correctly prevents any successful RAW contract table
        # from being created. The stable observations table must stay empty.
        self.assertEqual(self.raw._db.execute(
            "SELECT COUNT(*) FROM post_observations").fetchone()[0], 0)

    def test_quota_hold_remains_after_cooldown_without_verified_release(self):
        """65 minutes elapsed is not permission to reset a usage-limit hold."""
        from datetime import timedelta
        self.anchor(1)
        self.anchor(2)
        self.usage_percent = 85
        self.names = ["nail_us_1"]
        self.assertEqual(self.step()["status"], "recovered")
        first_cursor = self.store.read(self.accounts[0]["key"])["cursor"]
        self.now += timedelta(minutes=66)
        self.usage_percent = 40
        followup = self.step()
        self.assertEqual(followup["status"], "recovered")
        self.assertEqual(followup["lane"], "B")
        self.assertEqual(self.calls, 2)
        self.assertEqual(self.store.read(self.accounts[0]["key"])["cursor"], first_cursor)
        self.assertEqual(self.store.read(self.accounts[1]["key"])["pages_verified"], 1)
        lane = self.coordinator.db.execute(
            "SELECT halted,reason FROM lanes WHERE lane='A'").fetchone()
        self.assertEqual(lane["halted"], 1)
        self.assertEqual(lane["reason"], "usage_limit")

    def test_missing_usage_remains_held_after_cooldown_without_new_http(self):
        """Missing Meta usage evidence must not turn into a time-based retry."""
        from datetime import timedelta
        self.anchor(1)
        self.anchor(2)
        self.usage_percent = None
        self.names = ["nail_us_1"]
        self.assertEqual(self.step()["status"], "manual_review")
        self.now += timedelta(minutes=66)
        self.assertEqual(self.step()["status"], "quota_hold")
        self.assertEqual(self.calls, 1)
        self.assertEqual(self.store.read(self.accounts[0]["key"])["pages_verified"], 0)
        self.assertEqual(self.store.read(self.accounts[1]["key"])["pages_verified"], 0)

    def test_unknown_http_result_blocks_without_automatic_retry(self):
        self.anchor(1)
        self.anchor(2)
        self.unknown_first = True
        self.names = ["nail_us_1"]
        first = self.step()
        self.assertEqual(first["status"], "manual_review")
        self.assertEqual(self.store.read(self.accounts[0]["key"])["cursor"], "tail1")
        self.assertEqual(self.store.read(self.accounts[0]["key"])["state"], "blocked_unknown_http")
        self.assertEqual(self.step()["status"], "quota_hold")
        self.assertEqual(self.calls, 1)

    def test_invalid_cursor_is_isolated_no_guess_or_retry(self):
        self.anchor(1)
        self.anchor(2)
        self.invalid_cursor_first = True
        self.names = ["nail_us_1", "nail_us_2"]
        first = self.step()
        self.assertEqual(first["status"], "account_blocked")
        self.assertEqual(self.store.read(self.accounts[0]["key"])["cursor"], "tail1")
        self.assertEqual(self.store.read(self.accounts[0]["key"])["pages_verified"], 0)
        self.assertEqual(self.step()["status"], "recovered")
        self.assertEqual(self.calls, 2)
        self.assertEqual(self.store.read(self.accounts[1]["key"])["pages_verified"], 1)

    def test_fair_round_of_fifty_pages(self):
        for ordinal in range(1, 51):
            self.anchor(ordinal)
        results = [self.step() for _ in range(50)]
        self.assertEqual([r["status"] for r in results], ["recovered"] * 50)
        self.assertEqual(self.calls, 50)
        self.assertTrue(all(self.store.read(a["key"])["pages_verified"] >= 1
                            for a in self.accounts))


if __name__ == "__main__":
    unittest.main()
