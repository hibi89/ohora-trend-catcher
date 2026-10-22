"""Build the explicit P6 serial collector from caller-owned durable ports.

This factory never reads credentials, opens databases, enables Graph HTTP,
starts a scheduler or dispatches a request. All caller ports must be created
outside the factory. It is NOT an approval to call Meta.
"""
from __future__ import annotations

from pathlib import Path

from three_api_capture import CaptureJournal
from three_api_contract_executor import ThreeApiContractExecutor
from three_api_coordinator import Coordinator
from three_api_job_binding import JobRequestBindingStore
from three_api_live_lane_router import ThreeLaneMetaTransport
from three_api_live_serial_step import LiveSerialStep
from three_api_raw_writer import ContractRawWriter
from three_api_verified_page_evidence import VerifiedRawPageEvidenceStore


class SerialBootstrapError(ValueError):
    """Safe construction rejection, without exposing paths or credentials."""


def build_verified_serial_runner(coordinator, capture, raw, transport):
    """Connect verified RAW-backed coordinator and existing serial runner.

    None of the injected HTTP ports are called. The caller must separately
    authorize any later step and explicitly control network/scheduler access.
    """
    if (type(coordinator) is not Coordinator or type(capture) is not CaptureJournal
            or type(raw) is not ContractRawWriter
            or type(transport) is not ThreeLaneMetaTransport):
        raise SerialBootstrapError("Original coordinator/Capture/RAW/three-lane ports required")

    # Never share database files between the independent coordinator, Capture
    # journal and RAW writer: each has a distinct durable commit boundary.
    coordinator_path = Path(coordinator.path).resolve()
    capture_path = Path(capture.path).resolve()
    if coordinator_path == capture_path:
        raise SerialBootstrapError("Coordinator and Capture databases must differ")
    raw_path = next(
        (name for _, db_name, name in raw._db.execute("PRAGMA database_list")
         if db_name == "main"), "",
    )
    if raw_path and Path(raw_path).resolve() in (coordinator_path, capture_path):
        raise SerialBootstrapError("RAW and control databases must differ")

    evidence = VerifiedRawPageEvidenceStore(coordinator, capture, raw)
    bindings = JobRequestBindingStore(coordinator)
    executor = ThreeApiContractExecutor(transport, capture, raw)
    return LiveSerialStep(coordinator, bindings, evidence, executor)
