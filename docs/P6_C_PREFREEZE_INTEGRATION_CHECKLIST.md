# P6-C pre-freeze integration checklist (2026-10-19)

Status: **OPEN / contract v0.2-draft / no interface freeze**. This is an integration issue log, not an implementation or authorization.

Source: Worker A `docs/P6_WORKER_A.md` on `feature/p6-a-meta-transport-20261019`, C contract, B writer.

| ID | Integration discrepancy | Minimum safe resolution | Owner / gate |
|---|---|---|---|
| A1 | Actual Meta `x-app-usage` / `x-business-use-case-usage` contain JSON; C `safe_usage_metrics` currently expects non-real `x-app-usage-call-count` and `x-business-use-case-usage-call-count` numeric headers. | Reuse A's existing tested HTTP/usage validator and define a validated numeric usage adapter; missing/unparseable must fail closed, and high usage (>85%) must block. **Do not interpret raw JSON as a scalar or silently accept unsupported header formats.** | A/C + lead approval |
| A2 | Account identity is not proven by C's separate `account_observation` alone. | Verify returned target IG id/username against requested target before successful durable capture; preserve exact request digest. | A/C |
| A3 | `media_product_type` is used in RAW schema but absent from C persisted post allowlist. | Agree whether field is requested and tested; only explicitly allowlist after evidence and tests. Preserve `not_tested` vs `absent`. | A/B/C |
| A4 | Cursor is opaque, not a `paging.next` URL. | Reuse existing cursor validator; accept only validated `paging.cursors.after`, persist source digest, never follow paging URL. | A/C |
| B1 | B `write_capture(db, capture: dict)->str` vs C `write_capture(CaptureRecord)->RawWriteReceipt`; `get_raw_receipt` missing. | Adapt at integration boundary; do not rewrite B writer. Verify canonical digest mapping, receipt lookup, atomic replay, and idempotency. | B/C |
| B2 | B currently derives missing like/comment as `absent`, without requested-field evidence. | Adapter must preserve `not_tested` for unrequested fields; validate RAW field_observations with synthetic tests before freeze. | B/C |

Contract test command: `python -m unittest discover -s tests -p 'test_three_api_runtime_contract.py' -v`.

No live Meta calls, tokens, operational DB, unattended runs, main merge or contract freeze authorized.
