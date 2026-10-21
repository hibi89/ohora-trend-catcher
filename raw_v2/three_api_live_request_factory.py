"""Pure request preparation from a coordinator job. Does not send HTTP."""
from __future__ import annotations

from three_api_adapter_guard import validate_job_request
from three_api_contract_executor import request_envelope_id
from three_api_request_spec import build_request_spec
from three_api_runtime_contract import CONTRACT_VERSION, RequestEnvelope, digest_sha256


def prepare_job_request(job, evidence, account, *, run_id, roster_digest,
                        day_kst, requested_at, api_version, caller_id):
    """Build an immutable request; caller supplies all verified metadata."""
    if not isinstance(job, dict) or not isinstance(account, dict):
        raise ValueError("Coordinator job and account required")
    if account.get("ordinal") != job.get("account_ordinal"):
        raise ValueError("Roster ordinal mismatch")
    key = account.get("key", account.get("account_key"))
    username = account.get("username")
    if not isinstance(key, str) or not key or not isinstance(username, str) or not username:
        raise ValueError("Invalid official account")
    cursor = evidence.required_cursor(job["account_ordinal"], job["page"])
    spec = build_request_spec(job["lane"], username, after=cursor, limit=4,
                              include_carousel_children=True,
                              include_account_metrics=True)
    digest = digest_sha256({"api_version": api_version,
                            "target_ig_user_id": caller_id, "spec": spec})
    request_id = request_envelope_id(
        run_id=run_id, roster_digest=roster_digest, account_key=key,
        lane=job["lane"], phase=job["phase"], page=job["page"],
        cursor=cursor, request_spec_digest=digest)
    request = RequestEnvelope(
        CONTRACT_VERSION, request_id, run_id, roster_digest, day_kst,
        job["account_ordinal"], key, username, job["lane"], job["phase"],
        job["page"], cursor, requested_at, spec, digest,
        api_version=api_version, target_ig_user_id=caller_id)
    validate_job_request(job, request, evidence)
    return request
