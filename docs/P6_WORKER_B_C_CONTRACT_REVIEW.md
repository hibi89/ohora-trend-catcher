# Worker B integration review — historical pre-freeze snapshot

> **Superseded 2026-10-19.** The offline interface has since been reviewed and frozen as contract v1.0, then connected to the transport, Capture journal, and RAW writer. See [P6 runtime integration](P6_RUNTIME_INTEGRATION.md). The old checklist below describes the pre-integration state and is not current implementation guidance. No live Meta or production DB approval is implied.

Source: C branch feature/p6-c-runtime-20261019, raw_v2/three_api_runtime_contract.py.
Status: **DRAFT, not Astra-approved**.

The existing B writer accepts an interim dictionary envelope and returns a string.
It **does not** satisfy C's RawWriterPort and must not be wired into runtime.

Required changes after C/Astra finalization:
1. Implement RawWriterPort.write_capture(CaptureRecord)->RawWriteReceipt and get_raw_receipt(capture_id).
2. Bind request_id, run_id, account_key, roster_digest, requested_at, immutable observed_at and capture_digest; verify account/roster and request spec identity.
3. Use normalized_posts, account_observation, field_status and carousel_completeness, not guessed data fields.
4. Preserve field status exactly (not_tested/null/absent/returned), and do not infer carousel completeness.
5. Verify durable CaptureReceipt before write; conflict handling must raise ContractError(category=raw_conflict/raw_io).
6. Commit RAW rows and receipt atomically; after crash read receipt and replay only verified capture, never issue HTTP.
7. Add compatibility tests with actual collector schema and audit, cursor invariants, synthetic crash cases.
8. No main merge or production DB access while contract remains draft.

Worker B can continue isolated tests of the provisional writer, but it is **not ready for C integration**.
