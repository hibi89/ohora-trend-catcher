"""Explicit manual ingestion of SUPPLIED OFFLINE synthetic history responses.

Not a Meta transport. No sender, auth, network, timer, scheduler, or operational
DB connector is ever constructed. The P3 history job MUST already be bound
through the frozen daily-front-priority/3-lane/limit=4 policy.

This bridge retains the response in the REAL P1 CaptureJournal, commits the
allowlisted CaptureRecord to the REAL-schema P2 RAW v2 clone and acknowledges
its receipt. P3 cursor movement is a separate read-only receipt verification
step through OfflineEpochContractProjector.project_history_page().
"""
from __future__ import annotations

from three_api_epochs import ThreeApiEpochStore, _hash
from three_api_capture import CaptureJournal
from three_api_raw_writer import ContractRawWriter
from three_api_runtime_contract import (
    RequestEnvelope, ResponseEnvelope, commit_raw_and_confirm,
    persist_capture_verified,
)
from three_api_contract_executor import capture_record_from_response


class OfflineHistoryFixtureError(ValueError):
    """No cursor, media, URL, tokens or body content in the error message."""


class OfflineHistoryFixtureIngress:
    def __init__(self, epochs: ThreeApiEpochStore,
                 capture: CaptureJournal, raw: ContractRawWriter):
        if (not isinstance(epochs, ThreeApiEpochStore)
                or not isinstance(capture, CaptureJournal)
                or not isinstance(raw, ContractRawWriter)):
            raise OfflineHistoryFixtureError(
                "Isolated P3/Capture/RAW v2 stores required"
            )
        self.epochs, self.capture, self.raw = epochs, capture, raw

    def save_supplied_response(self, job_id, request, response):
        """Accept one already-supplied typed synthetic response; NEVER send HTTP.

        CaptureJournal stores safe first-received source evidence BEFORE
        normalizing. P2 uses its existing immutable RAW writer and observation
        version schema. This function NEVER advances the P3 history checkpoint.
        """
        if (not isinstance(request, RequestEnvelope)
                or not isinstance(response, ResponseEnvelope)
                or request.phase != "history"
                or request.history_offline_only is not True):
            raise OfflineHistoryFixtureError("Explicit offline history fixture required")
        try:
            job = self.epochs._job_row(job_id)
            if (job["epoch_kind"]!="history" or job["phase"]!="history"
                    or job["state"] not in ("bound","done")):
                raise OfflineHistoryFixtureError("Bound P3 history job required")
            binding = self.epochs.db.execute(
                "SELECT request_id,requested_cursor,request_spec_digest "
                "FROM request_bindings_v2 WHERE job_id=?", (job_id,)
            ).fetchone()
            member = self.epochs.db.execute(
                "SELECT username FROM roster_members "
                "WHERE roster_digest=? AND account_key=?",
                (job["roster_digest"],job["account_key"])
            ).fetchone()
            if (binding is None or member is None
                    or request.request_id!=binding[0]
                    or request.cursor!=binding[1]
                    or _hash(request.request_spec.__dict__)!=binding[2]
                    or request.roster_digest!=job["roster_digest"]
                    or request.account_key!=job["account_key"]
                    or request.account_ordinal!=job["ordinal"]
                    or request.username!=member[0]
                    or request.lane!=job["lane"]
                    or request.page!=job["page_seq"]
                    or response.request_id!=request.request_id):
                raise OfflineHistoryFixtureError(
                    "Offline history fixture is not bound to P3"
                )
            outcome = self.capture.get_intent_outcome(request.request_id)
            if outcome not in (None,"pending","captured"):
                raise OfflineHistoryFixtureError(
                    "Unresolved or failed request is not replayable"
                )
            self.capture.save_intent(request)
            evidence = self.capture.save_response_evidence(response)
            if evidence["classification"]!="safe":
                raise OfflineHistoryFixtureError(
                    "Cannot retain a safe history response"
                )
            record = capture_record_from_response(request,response)
            persist_capture_verified(self.capture,record)
            # May fail after P1 commit. Only an explicit local replay of this
            # durable record can complete P2 without fetching a new response.
            receipt = commit_raw_and_confirm(self.capture,self.raw,record)
            return {
                "status":receipt.status,
                "capture_id":record.capture_id,
                "raw_receipt_id":receipt.raw_receipt_id,
                "epoch_committed":False,
                "history_transport_enabled":False,
                "unattended_execution_enabled":False,
            }
        except OfflineHistoryFixtureError:
            raise
        except (ValueError,TypeError,LookupError,AttributeError):
            raise OfflineHistoryFixtureError(
                "Offline history fixture proof was rejected"
            ) from None
