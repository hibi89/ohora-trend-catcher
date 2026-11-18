# P6 Worker A — integration status (2026-10-19)

Owner: A. Base: `bcdb84b640738fdfceaf833dd831a26d401a2427`. Branch: `feature/p6-a-meta-transport-20261019`.

## Findings
- Existing `GraphHTTPClient.fetch(prepared, token)` accepts injected sender and returns `{status,headers,body}`. It is inert by default, strips unsafe error text, denies redirects, and does not load credentials. Reuse it; do not introduce a second HTTP stack.
- Existing `RequestSpec` fixes page size 4 and validates opaque cursors. No `paging.next` URL should be followed.
- Worker C's `raw_v2/three_api_runtime_contract.py` is present on `feature/p6-c-runtime-20261019` as **v0.2 DRAFT / Astra review pending**, but absent on main. A will not copy/edit C-owned contract.
- C draft exposes `TransportPort.send(RequestEnvelope)`, `CapturePort` intent/capture/failure/reconciliation methods, `capture_from_response`, `CaptureRecord`, `ResponseEnvelope`. Integration must use final C version; no guessed duplicate contract.

## Contract integration questions for C / lead
1. `GraphHTTPClient` currently returns usage headers `x-app-usage` and `x-business-use-case-usage` as JSON strings; draft `safe_usage_metrics` expects `x-app-usage-call-count` / `x-business-use-case-usage-call-count`. Decide single validated parser for actual Meta headers, including >85% and missing/unparseable fail-closed. A can implement after contract freeze.
2. Draft `sanitize_meta_response` retains account fields, but `capture_from_response` expects `account_observation` separately. Ensure target identity (id/username) is verified against requested account before durable success.
3. Draft `_POST_FIELDS` excludes `media_product_type` (needed RAW field coverage review). Confirm explicit not_tested/absent semantics, and whether opt-in is allowed. Do not silently claim field tested.
4. `ResponseEnvelope.next_cursor` must be derived from validated opaque `paging.cursors.after` / existing paging validator; never raw `paging.next`.
5. A will use isolated synthetic SQLite capture only; no production RAW writer, real tokens, or Meta calls.

## Safety and status
- Verified main matched base SHA at initial comparison.
- No live calls, no credentials, no production DB writes.
- This note is not implementation or test evidence. No tests run yet.
- Handoff: lead/C should review issues 1–4 and communicate final contract by updating GitHub. A branch stays independent of B/C ownership.
