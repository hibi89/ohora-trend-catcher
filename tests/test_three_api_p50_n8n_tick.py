"""Synthetic storage/runner tests for the P50 n8n boundary only."""
from datetime import datetime, timezone
import json
from pathlib import Path
import sqlite3
import tempfile
import unittest

from raw_v2 import three_api_p50_n8n_tick as adapter


DAY = "2026-10-22"
NOW = datetime(2026, 10, 22, 1, 0, tzinfo=timezone.utc)


def _db(path, script):
    path.parent.mkdir(parents=True, exist_ok=True)
    db = sqlite3.connect(path)
    try:
        db.executescript(script)
    finally:
        db.close()


def _seed(root, *, raw_ids=(), captures=(), done=0):
    raw = root / "p50_raw.sqlite"
    control = root / DAY / "coordinator.sqlite"
    capture = root / DAY / "capture.sqlite"
    _db(raw, """
        CREATE TABLE three_api_raw_receipts(capture_id TEXT PRIMARY KEY);
        CREATE TABLE three_api_contract_receipts(capture_id TEXT PRIMARY KEY);
        CREATE TABLE post_observations(post_id TEXT);
    """)
    db = sqlite3.connect(raw)
    try:
        for capture_id in raw_ids:
            db.execute("INSERT INTO three_api_raw_receipts VALUES(?)", (capture_id,))
            db.execute("INSERT INTO three_api_contract_receipts VALUES(?)", (capture_id,))
        db.commit()
    finally:
        db.close()
    _db(control, "CREATE TABLE jobs(id INTEGER PRIMARY KEY, state TEXT NOT NULL);")
    db = sqlite3.connect(control)
    try:
        for _ in range(done):
            db.execute("INSERT INTO jobs(state) VALUES('done')")
        db.commit()
    finally:
        db.close()
    _db(capture, "CREATE TABLE captures(capture_id TEXT PRIMARY KEY, processed_receipt TEXT);")
    db = sqlite3.connect(capture)
    try:
        for capture_id, receipt in captures:
            db.execute("INSERT INTO captures VALUES(?,?)", (capture_id, receipt))
        db.commit()
    finally:
        db.close()


def _seed_initialized_empty(root):
    _db(root / "p50_raw.sqlite", "CREATE TABLE post_observations(post_id TEXT);")
    _db(root / DAY / "coordinator.sqlite",
        "CREATE TABLE jobs(id INTEGER PRIMARY KEY, state TEXT NOT NULL);")
    _db(root / DAY / "capture.sqlite",
        "CREATE TABLE captures(capture_id TEXT PRIMARY KEY, processed_receipt TEXT);")


class P50N8nTickTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.base = Path(self.temp.name)
        self.workspace = self.base / "p50"
        self.source = self.base / "roster.csv"
        self.called = []

    def tearDown(self):
        self.temp.cleanup()

    def _runner(self, result, effect=None):
        def run(*args, **kwargs):
            self.called.append((args, kwargs))
            if effect:
                effect()
            return result
        return run

    def _loader(self, *_args, **_kwargs):
        return {"P6_META_TOKEN_A": "synthetic-secret"}

    def test_offline_invokes_existing_pilot_without_creating_workspace(self):
        run = self._runner({"mode": "P50_OFFLINE"})
        result = adapter.tick(self.source, self.workspace, pilot_runner=run,
                              environment_loader=self._loader)
        self.assertEqual(result["action"], "OFFLINE_PREFLIGHT")
        self.assertFalse(result["live_meta_called"])
        self.assertEqual(self.called[0][1]["max_pages"], 1)
        self.assertFalse(self.workspace.exists())

    def test_live_gate_without_approval_never_loads_credentials_or_calls_runner(self):
        result = adapter.tick(self.source, self.workspace, live=True, approved=False,
                              pilot_runner=lambda *_a, **_k: self.fail("runner called"),
                              environment_loader=lambda *_a, **_k: self.fail("loader called"))
        self.assertEqual(result["action"], "WAIT_EXPLICIT_LIVE_APPROVAL")
        self.assertEqual(self.called, [])

    def test_committed_page_requires_and_verifies_raw_capture_coordinator_receipts(self):
        def write_result():
            _seed(self.workspace, raw_ids=("cap-1",),
                  captures=(("cap-1", "rawv2:cap-1"),), done=1)
        result = adapter.tick(
            self.source, self.workspace, live=True, approved=True,
            credentials_dir=self.base / "credentials", environment={}, now=NOW,
            pilot_runner=self._runner({"last_status": "page_committed",
                                       "committed_pages": 1, "recovered_pages": 0,
                                       "account_failures": 0, "attempted_steps": 1}, write_result),
            environment_loader=self._loader)
        self.assertEqual(result["action"], "LIVE_TICK_COMPLETE", result)
        self.assertTrue(result["live_meta_called"])
        self.assertEqual(result["storage_verification"]["status"], "verified")
        self.assertEqual(result["storage_verification"]["receipt_delta"], 1)
        self.assertNotIn("synthetic-secret", json.dumps(result))

    def test_initialized_empty_raw_can_be_used_as_first_live_baseline(self):
        _seed_initialized_empty(self.workspace)
        def write_first_page():
            db = sqlite3.connect(self.workspace / "p50_raw.sqlite")
            try:
                db.execute("CREATE TABLE three_api_raw_receipts(capture_id TEXT PRIMARY KEY)")
                db.execute("CREATE TABLE three_api_contract_receipts(capture_id TEXT PRIMARY KEY)")
                db.execute("INSERT INTO three_api_raw_receipts VALUES('cap-first')")
                db.execute("INSERT INTO three_api_contract_receipts VALUES('cap-first')")
                db.execute("INSERT INTO post_observations VALUES('post-first')")
                db.commit()
            finally:
                db.close()
            db = sqlite3.connect(self.workspace / DAY / "coordinator.sqlite")
            try:
                db.execute("INSERT INTO jobs(state) VALUES('done')")
                db.commit()
            finally:
                db.close()
            db = sqlite3.connect(self.workspace / DAY / "capture.sqlite")
            try:
                db.execute("INSERT INTO captures VALUES('cap-first','rawv2:cap-first')")
                db.commit()
            finally:
                db.close()
        result = adapter.tick(
            self.source, self.workspace, live=True, approved=True,
            credentials_dir=self.base / "credentials", environment={}, now=NOW,
            pilot_runner=self._runner({"last_status": "page_committed",
                                       "committed_pages": 1, "recovered_pages": 0,
                                       "account_failures": 0, "attempted_steps": 1}, write_first_page),
            environment_loader=self._loader)
        self.assertEqual(result["action"], "LIVE_TICK_COMPLETE", result)
        self.assertEqual(result["storage_verification"]["receipt_delta"], 1)

    def test_recovered_capture_can_be_acknowledged_without_new_capture_row(self):
        _seed(self.workspace, captures=(("cap-old", None),))
        def finish_recovery():
            db = sqlite3.connect(self.workspace / "p50_raw.sqlite")
            try:
                db.execute("INSERT INTO three_api_raw_receipts VALUES('cap-old')")
                db.execute("INSERT INTO three_api_contract_receipts VALUES('cap-old')")
                db.commit()
            finally:
                db.close()
            db = sqlite3.connect(self.workspace / DAY / "coordinator.sqlite")
            try:
                db.execute("INSERT INTO jobs(state) VALUES('done')")
                db.commit()
            finally:
                db.close()
            db = sqlite3.connect(self.workspace / DAY / "capture.sqlite")
            try:
                db.execute("UPDATE captures SET processed_receipt='rawv2:cap-old' WHERE capture_id='cap-old'")
                db.commit()
            finally:
                db.close()
        result = adapter.tick(
            self.source, self.workspace, live=True, approved=True,
            credentials_dir=self.base / "credentials", environment={}, now=NOW,
            pilot_runner=self._runner({"last_status": "recovered", "committed_pages": 0,
                                       "recovered_pages": 1, "account_failures": 0,
                                       "attempted_steps": 1}, finish_recovery),
            environment_loader=self._loader)
        self.assertEqual(result["action"], "LIVE_TICK_COMPLETE", result)
        self.assertEqual(result["storage_verification"]["done_job_delta"], 1)

    def test_missing_receipt_delta_fails_closed(self):
        result = adapter.tick(
            self.source, self.workspace, live=True, approved=True,
            credentials_dir=self.base / "credentials", environment={}, now=NOW,
            pilot_runner=self._runner({"last_status": "page_committed",
                                       "committed_pages": 1, "recovered_pages": 0,
                                       "account_failures": 0, "attempted_steps": 1}),
            environment_loader=self._loader)
        self.assertEqual(result["action"], "MANUAL_REVIEW")
        self.assertEqual(result["storage_verification"]["status"], "receipt_mismatch")

    def test_no_ready_work_is_not_reported_as_a_live_meta_call(self):
        _seed(self.workspace)
        result = adapter.tick(
            self.source, self.workspace, live=True, approved=True,
            credentials_dir=self.base / "credentials", environment={}, now=NOW,
            pilot_runner=self._runner({"last_status": "no_ready_work", "committed_pages": 0,
                                       "recovered_pages": 0, "account_failures": 0,
                                       "attempted_steps": 1}),
            environment_loader=self._loader)
        self.assertFalse(result["live_meta_called"])
        self.assertEqual(result["storage_verification"]["status"], "verified")

    def test_runner_exception_never_claims_no_live_call(self):
        def fail(*_args, **_kwargs):
            raise RuntimeError("synthetic failure")
        result = adapter.tick(
            self.source, self.workspace, live=True, approved=True,
            credentials_dir=self.base / "credentials", environment={}, now=NOW,
            pilot_runner=fail, environment_loader=self._loader)
        self.assertEqual(result["action"], "MANUAL_REVIEW")
        self.assertIsNone(result["live_meta_called"])
        self.assertNotIn("synthetic failure", json.dumps(result))


if __name__ == "__main__":
    unittest.main()
