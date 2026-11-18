"""Durable, offline-first SQLite implementation of the P6 CapturePort.

This journal never sends HTTP or opens a RAW database. A successful receipt is
returned only after a committed row has been re-read and its SHA-256 verified.
Unknown request outcomes are never automatically retried.
"""
from __future__ import annotations

from datetime import date, datetime, timezone
import json
from pathlib import Path
import sqlite3
from math import ceil

from three_api_response_evidence import (
    SANITIZER_VERSION, UnsafeResponseEvidence, evidence_digest,
    prepare_safe_response,
)
from three_api_usage import parse_usage

from three_api_request_spec import RequestSpec


def _contract():
    from three_api_runtime_contract import (
        CONTRACT_VERSION, CaptureReceipt, CaptureRecord, CursorState,
        FailureEvidence, RequestEnvelope, TransportFailure, canonical_bytes,
        digest_sha256,
    )
    return (CONTRACT_VERSION, CaptureReceipt, CaptureRecord, CursorState,
            FailureEvidence, RequestEnvelope, TransportFailure, canonical_bytes,
            digest_sha256)


def _parse_time(value: str) -> datetime:
    if not isinstance(value, str):
        raise ValueError("Invalid persisted time")
    result = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if result.tzinfo is None or result.utcoffset() is None:
        raise ValueError("Invalid persisted time")
    return result


def _restore_request(data):
    (version, _, _, _, _, RequestEnvelope, _, _, _) = _contract()
    if not isinstance(data, dict):
        raise ValueError("Invalid persisted request")
    values = dict(data)
    spec_data = values.get("request_spec")
    if not isinstance(spec_data, dict):
        raise ValueError("Invalid persisted request spec")
    # JSON turns opt-in account-field tuples into lists. Restore the immutable
    # RequestSpec shape while preserving default v1.0 canonical digests.
    spec_data = dict(spec_data)
    if "account_fields" in spec_data:
        if not isinstance(spec_data["account_fields"], list):
            raise ValueError("Invalid stored account-field selection")
        spec_data["account_fields"] = tuple(spec_data["account_fields"])
    values["request_spec"] = RequestSpec(**spec_data)
    values["day_kst"] = date.fromisoformat(values["day_kst"])
    values["requested_at"] = _parse_time(values["requested_at"])
    result = RequestEnvelope(**values)
    if result.contract_version != version:
        raise ValueError("Unsupported persisted contract")
    return result


def _restore_capture(data):
    (version, _, CaptureRecord, CursorState, _, _, _, _, _) = _contract()
    if not isinstance(data, dict):
        raise ValueError("Invalid persisted capture")
    values = dict(data)
    values["request"] = _restore_request(values["request"])
    cursor = values.get("cursor_state")
    if not isinstance(cursor, dict):
        raise ValueError("Invalid persisted cursor evidence")
    values["cursor_state"] = CursorState(**cursor)
    values["observed_at"] = _parse_time(values["observed_at"])
    if values.get("response_received_at") is not None:
        values["response_received_at"] = _parse_time(values["response_received_at"])
    record = CaptureRecord(**values)
    if record.contract_version != version:
        raise ValueError("Unsupported persisted contract")
    return record


def _restore_failure(data):
    _, _, _, _, FailureEvidence, _, _, _, _ = _contract()
    if not isinstance(data, dict):
        raise ValueError("Invalid persisted failure")
    values = dict(data)
    values["recorded_at"] = _parse_time(values["recorded_at"])
    return FailureEvidence(**values)


class CaptureJournal:
    """Append-only request/capture journal with idempotent durable receipts."""

    def __init__(self, path: str):
        if not isinstance(path, (str, Path)) or not str(path).strip():
            raise ValueError("Explicit SQLite path required")
        candidate = Path(path).expanduser()
        if candidate.name.lower() == "collection.sqlite" or str(path) == ":memory:":
            raise ValueError("Operating RAW database path is not allowed")
        if candidate.is_symlink():
            raise ValueError("Symlinked SQLite path is not allowed")
        self.path = candidate.resolve()
        self._db = sqlite3.connect(self.path, isolation_level="IMMEDIATE", timeout=10)
        self._db.row_factory = sqlite3.Row
        self._db.execute("PRAGMA busy_timeout=10000")
        self._db.execute("PRAGMA foreign_keys=ON")
        self._db.execute("PRAGMA synchronous=FULL")
        self._db.execute("PRAGMA journal_mode=WAL")
        self._db.executescript("""
            CREATE TABLE IF NOT EXISTS intents(
                request_id TEXT PRIMARY KEY, run_id TEXT NOT NULL,
                payload BLOB NOT NULL, digest TEXT NOT NULL,
                outcome TEXT NOT NULL DEFAULT 'pending'
                CHECK(outcome IN ('pending','captured','unknown','failed_no_retry','manual_retry_approved')));
            CREATE INDEX IF NOT EXISTS intents_run ON intents(run_id,outcome);
            CREATE TABLE IF NOT EXISTS captures(
                capture_id TEXT PRIMARY KEY, request_id TEXT NOT NULL UNIQUE,
                run_id TEXT NOT NULL, payload BLOB NOT NULL,
                digest TEXT NOT NULL, captured_at TEXT NOT NULL,
                processed_receipt TEXT,
                FOREIGN KEY(request_id) REFERENCES intents(request_id));
            CREATE INDEX IF NOT EXISTS captures_run ON captures(run_id,processed_receipt);
            CREATE TABLE IF NOT EXISTS failures(
                request_id TEXT PRIMARY KEY, run_id TEXT NOT NULL,
                payload BLOB NOT NULL,
                FOREIGN KEY(request_id) REFERENCES intents(request_id));
            CREATE INDEX IF NOT EXISTS failures_run ON failures(run_id);
            CREATE TABLE IF NOT EXISTS response_evidence(
                evidence_id TEXT PRIMARY KEY,
                request_id TEXT NOT NULL UNIQUE REFERENCES intents(request_id),
                received_at TEXT NOT NULL,
                http_status INTEGER NOT NULL,
                safe_body_json BLOB,
                body_digest TEXT,
                safe_usage_json TEXT NOT NULL,
                sanitizer_version TEXT NOT NULL,
                classification TEXT NOT NULL
                    CHECK(classification IN ('safe','evidence_unavailable')),
                redacted_paths_json TEXT NOT NULL
            );
        """)

    def close(self):
        self._db.close()

    def save_intent(self, request):
        (version, _, _, _, _, RequestEnvelope, _, canonical, digest_fn) = _contract()
        if not isinstance(request, RequestEnvelope) or request.contract_version != version:
            raise ValueError("Validated request envelope required")
        data = canonical(request)
        digest = digest_fn(request)
        with self._db:
            existing = self._db.execute(
                "SELECT payload,digest FROM intents WHERE request_id=?",
                (request.request_id,)).fetchone()
            if existing:
                if existing["digest"] != digest or bytes(existing["payload"]) != data:
                    raise ValueError("Conflicting immutable request intent")
                return False
            self._db.execute(
                "INSERT INTO intents(request_id,run_id,payload,digest) VALUES(?,?,?,?)",
                (request.request_id, request.run_id, data, digest))
        return True

    def save_response_evidence(self, response):
        """Durably store a safe response *before* schema/cursor/usage validation.

        A sanitizer failure stores evidence_unavailable metadata, NEVER the
        unsafe original. No raw usage headers or paging URLs are persisted.
        This method never marks a request successful or authorizes any HTTP.
        """
        from three_api_runtime_contract import ResponseEnvelope
        if not isinstance(response, ResponseEnvelope):
            raise ValueError("Typed response receipt required")
        observed_at = response.received_at.isoformat(timespec="microseconds").replace("+00:00", "Z")
        safe_usage = parse_usage(dict(response.usage_headers))
        metrics = {"max_usage_percent": ceil(safe_usage)} if safe_usage is not None else {}
        try:
            safe_body, redacted_paths = prepare_safe_response(response.payload)
            digest = evidence_digest(safe_body)
            body = json.dumps(safe_body, sort_keys=True, separators=(",", ":"),
                              ensure_ascii=False, allow_nan=False).encode("utf-8")
            classification = "safe"
        except (UnsafeResponseEvidence, TypeError, ValueError):
            body, digest, redacted_paths, classification = (
                None, None, [], "evidence_unavailable")
        evidence_id = "response-" + response.request_id
        record = (
            evidence_id, response.request_id, observed_at, response.http_status,
            body, digest, json.dumps(metrics, sort_keys=True),
            SANITIZER_VERSION, classification,
            json.dumps(redacted_paths, sort_keys=True),
        )
        with self._db:
            intent = self._db.execute(
                "SELECT outcome FROM intents WHERE request_id=?", (response.request_id,)
            ).fetchone()
            if intent is None or intent["outcome"] not in ("pending", "captured"):
                raise ValueError("Response evidence requires durable intent")
            previous = self._db.execute(
                "SELECT * FROM response_evidence WHERE request_id=?",
                (response.request_id,),
            ).fetchone()
            if previous is not None:
                if tuple(previous) != record:
                    raise ValueError("Immutable response evidence conflict")
            else:
                self._db.execute(
                    "INSERT INTO response_evidence VALUES(?,?,?,?,?,?,?,?,?,?)", record)
        return {"evidence_id": evidence_id, "classification": classification,
                "body_digest": digest, "safe_usage": metrics}

    def get_response_evidence(self, request_id):
        """Return safe retained evidence without live headers or credentials."""
        row = self._db.execute(
            "SELECT * FROM response_evidence WHERE request_id=?", (request_id,)
        ).fetchone()
        if row is None:
            return None
        result = dict(row)
        data = bytes(result["safe_body_json"]) if result["safe_body_json"] is not None else None
        result["safe_body"] = json.loads(data.decode("utf-8")) if data is not None else None
        if result["safe_body"] is not None and evidence_digest(result["safe_body"]) != result["body_digest"]:
            raise ValueError("Response evidence digest mismatch")
        del result["safe_body_json"]
        result["safe_usage"] = json.loads(result.pop("safe_usage_json"))
        result["redacted_paths"] = json.loads(result.pop("redacted_paths_json"))
        return result

    def permit_local_reprojection(self, request):
        """Permit a parsed SAFE response to be stored, without any HTTP send.

        This is deliberately NOT an HTTP retry authorization. An unresolved
        outcome without saved response evidence must remain blocked.
        """
        (_, _, _, _, _, RequestEnvelope, _, canonical, digest_fn) = _contract()
        if not isinstance(request, RequestEnvelope):
            raise ValueError("Original validated request required")
        with self._db:
            row = self._db.execute(
                "SELECT payload,digest,outcome FROM intents WHERE request_id=?",
                (request.request_id,)).fetchone()
            evidence = self._db.execute(
                "SELECT classification FROM response_evidence WHERE request_id=?",
                (request.request_id,)).fetchone()
            if (row is None or bytes(row["payload"]) != canonical(request)
                    or row["digest"] != digest_fn(request)
                    or row["outcome"] not in ("pending", "failed_no_retry")
                    or evidence is None or evidence["classification"] != "safe"):
                raise ValueError("No original safe response eligible for local replay")
            self._db.execute(
                "UPDATE intents SET outcome='pending' WHERE request_id=?",
                (request.request_id,))
        return True

    def get_intent_outcome(self, request_id):
        row = self._db.execute(
            "SELECT outcome FROM intents WHERE request_id=?", (request_id,)).fetchone()
        return None if row is None else row["outcome"]

    def get_capture(self, capture_id):
        (_, _, _, _, _, _, _, _, digest_fn) = _contract()
        row = self._db.execute(
            "SELECT payload,digest FROM captures WHERE capture_id=?", (capture_id,)
        ).fetchone()
        if row is None:
            return None
        record = _restore_capture(json.loads(bytes(row["payload"]).decode("utf-8")))
        if record.capture_digest != row["digest"] or digest_fn(record) != row["digest"]:
            raise ValueError("Persisted capture digest mismatch")
        return record

    def get_capture_for_request(self, request_id):
        row = self._db.execute(
            "SELECT capture_id FROM captures WHERE request_id=?", (request_id,)
        ).fetchone()
        return None if row is None else self.get_capture(row["capture_id"])

    def save_capture(self, record):
        (version, CaptureReceipt, CaptureRecord, _, _, _, _, canonical, _) = _contract()
        if not isinstance(record, CaptureRecord) or record.contract_version != version:
            raise ValueError("Validated capture record required")
        data = canonical(record)
        digest = record.capture_digest
        captured_at = datetime.now(timezone.utc).isoformat(timespec="microseconds").replace("+00:00", "Z")
        with self._db:
            intent = self._db.execute(
                "SELECT digest,outcome FROM intents WHERE request_id=?",
                (record.request.request_id,)).fetchone()
            if intent is None:
                raise ValueError("Capture requires durable request intent")
            if intent["digest"] != _contract()[-1](record.request) or intent["outcome"] not in ("pending", "captured"):
                raise ValueError("Capture differs from intent or intent is unresolved")
            existing = self._db.execute(
                "SELECT capture_id,payload,digest,captured_at FROM captures WHERE capture_id=? OR request_id=?",
                (record.capture_id, record.request.request_id)).fetchone()
            if existing:
                if (existing["capture_id"] != record.capture_id or
                    existing["digest"] != digest or bytes(existing["payload"]) != data):
                    raise ValueError("Conflicting immutable capture evidence")
                captured_at = existing["captured_at"]
            else:
                self._db.execute(
                    "INSERT INTO captures(capture_id,request_id,run_id,payload,digest,captured_at) VALUES(?,?,?,?,?,?)",
                    (record.capture_id, record.request.request_id, record.request.run_id,
                     data, digest, captured_at))
            self._db.execute("UPDATE intents SET outcome='captured' WHERE request_id=?",
                             (record.request.request_id,))
        restored = self.get_capture(record.capture_id)
        if restored is None or restored.capture_digest != digest:
            raise ValueError("Capture commit could not be verified")
        return CaptureReceipt(version, record.capture_id, record.request.request_id,
            record.request.account_key, record.request.lane, _parse_time(captured_at),
            digest, f"captures:{record.capture_id}", True)

    def list_pending_captures(self, run_id):
        ids = [row[0] for row in self._db.execute(
            "SELECT capture_id FROM captures WHERE run_id=? AND processed_receipt IS NULL ORDER BY rowid",
            (run_id,))]
        records = [self.get_capture(value) for value in ids]
        if any(record is None for record in records):
            raise ValueError("Pending capture disappeared during read")
        return records

    def list_pending_capture_bytes(self, run_id):
        return [bytes(row[0]) for row in self._db.execute(
            "SELECT payload FROM captures WHERE run_id=? AND processed_receipt IS NULL ORDER BY rowid",
            (run_id,))]

    def confirm_processed(self, capture_id, raw_receipt_id):
        if not isinstance(raw_receipt_id, str) or not raw_receipt_id:
            raise ValueError("Verified RAW receipt required")
        with self._db:
            row = self._db.execute(
                "SELECT processed_receipt FROM captures WHERE capture_id=?", (capture_id,)
            ).fetchone()
            if row is None or (row["processed_receipt"] is not None and
                               row["processed_receipt"] != raw_receipt_id):
                raise ValueError("Unknown capture or conflicting RAW acknowledgement")
            self._db.execute(
                "UPDATE captures SET processed_receipt=? WHERE capture_id=?",
                (raw_receipt_id, capture_id))

    def save_failure(self, failure):
        (_, _, _, _, FailureEvidence, _, _, canonical, digest_fn) = _contract()
        if not isinstance(failure, FailureEvidence):
            raise ValueError("Validated failure evidence required")
        data = canonical(failure)
        outcome = "unknown" if failure.outcome == "outcome_unknown" else "failed_no_retry"
        with self._db:
            intent = self._db.execute(
                "SELECT outcome FROM intents WHERE request_id=?", (failure.request_id,)
            ).fetchone()
            if intent is None or intent["outcome"] == "captured":
                raise ValueError("Failure requires an unresolved durable intent")
            prior = self._db.execute(
                "SELECT payload FROM failures WHERE request_id=?", (failure.request_id,)
            ).fetchone()
            if prior and bytes(prior["payload"]) != data:
                raise ValueError("Conflicting failure evidence")
            if prior is None:
                self._db.execute("INSERT INTO failures(request_id,run_id,payload) VALUES(?,?,?)",
                                 (failure.request_id, failure.run_id, data))
            self._db.execute("UPDATE intents SET outcome=? WHERE request_id=?",
                             (outcome, failure.request_id))
        return digest_fn(failure)

    def get_failure(self, request_id):
        row = self._db.execute(
            "SELECT payload FROM failures WHERE request_id=?", (request_id,)
        ).fetchone()
        return None if row is None else _restore_failure(json.loads(bytes(row[0]).decode("utf-8")))

    def list_unresolved_intents(self, run_id):
        rows = self._db.execute(
            "SELECT payload,digest,outcome FROM intents WHERE run_id=? AND outcome IN ('pending','unknown') ORDER BY rowid",
            (run_id,))
        result = []
        for row in rows:
            request = _restore_request(json.loads(bytes(row["payload"]).decode("utf-8")))
            if _contract()[-1](request) != row["digest"]:
                raise ValueError("Persisted request digest mismatch")
            result.append(request)
        return result

    def mark_outcome_unknown(self, request_id):
        with self._db:
            row = self._db.execute(
                "SELECT outcome FROM intents WHERE request_id=?", (request_id,)
            ).fetchone()
            if row is None or row["outcome"] not in ("pending", "unknown"):
                raise ValueError("Only unresolved requests can become unknown")
            self._db.execute("UPDATE intents SET outcome='unknown' WHERE request_id=?",
                             (request_id,))

    def reconcile_request(self, request_id, resolution):
        if resolution not in ("failed_no_retry", "manual_retry_approved"):
            raise ValueError("Explicit reconciliation decision required")
        with self._db:
            row = self._db.execute(
                "SELECT outcome FROM intents WHERE request_id=?", (request_id,)
            ).fetchone()
            if row is None or row["outcome"] != "unknown":
                raise ValueError("Only unknown outcomes may be reconciled")
            self._db.execute("UPDATE intents SET outcome=? WHERE request_id=?",
                             (resolution, request_id))
