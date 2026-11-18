# P6-C pre-freeze integration checklist (2026-10-19)

Status: **CLOSED for offline integration / contract v1.0 frozen (2026-10-19)**. This log is retained as a discrepancy record. See [P6 runtime integration](P6_RUNTIME_INTEGRATION.md) for the current implementation and remaining live gates.

Source: Worker A `docs/P6_WORKER_A.md` on `feature/p6-a-meta-transport-20261019`, C contract, B writer.

| ID | Integration discrepancy | Minimum safe resolution | Owner / gate |
|---|---|---|---|
| A1 | Meta usage headers contain JSON; raw headers must not be persisted. | Resolved: delegate to `three_api_usage.parse_usage`; missing/malformed and >=85% fail closed. | Verified by offline tests |
| A2 | Response identity must match the requested account. | Resolved: both sanitized response identity and account observation ID/username are checked before capture persistence. | Verified by offline tests |
| A3 | `media_product_type` is not present in the contract allowlist. | Resolved by omission: this runtime does not request or persist the field. Add only with a separate contract revision and evidence. | Deliberate scope |
| A4 | Cursor is opaque; do not call `paging.next`. | Resolved: validated cursor only, source digest recorded, no `next` URL request path. | Verified by offline tests |
| B1 | Dictionary/string writer did not implement `RawWriterPort`. | Resolved: `ContractRawWriter` adapts typed captures, writes rows and contract receipt atomically, and verifies receipt on recovery. | Verified on isolated RAW schema |
| B2 | Field states must distinguish absent/null/not-tested/returned. | Resolved: contract validates states and adapter preserves them in RAW `field_observations`. | Verified by offline tests |

Contract and executor test commands are listed in [P6 runtime integration](P6_RUNTIME_INTEGRATION.md). Contract freeze covers the offline port boundary only. No live Meta calls, tokens, operational DB, unattended runs, or main merge are authorized by this freeze.
