# P50 → n8n → P294 completion checklist

Use this list to track the user's agreed sequence. P50 and P294 use independent RAW databases, coordinators, Capture journals, and collection histories. The frozen P6 design and P6 production data remain unchanged.

## P50 readiness and one-page proof

- [x] Validate the supplied 50-account roster locally (50 rows; US 18, KR 16, JP 16; unique usernames; profile links agree). Keep the normalized roster under ignored local `.tmp`; never commit account identities.
- [x] Validate local A/B/C credential-file structure, unique token/caller pairs, and Graph API version without printing any values. This confirms local format only, not every lane's live permissions.
- [x] Use the existing pinned P50 engine for exactly one explicitly approved A-lane page. Verify HTTP 200, safe usage 0%, four post observations, a durable receipt, Capture acknowledgment, and coordinator completion through read-only checks.
- [x] Correct the n8n adapter's runner result-field mismatch (`status` vs `last_status`) and add an offline regression test; commit and push on the existing P6 work branch.
- [x] Synchronize `HANDOFF.md`, `STATUS.md`, the P6 troubleshooting ledger, and the central Project Book index.

## n8n completion — pending

- [x] Repeat the isolated n8n workflow end-to-end **offline/mock only** against the corrected adapter. Isolated n8n 2.40.7 execution 3 persisted `success`; the pinned launcher separately returned `OFFLINE_PREFLIGHT` / `P50_OFFLINE`, zero attempts/pages, and `live_meta_called=false`.
- [x] Align the n8n launcher pin with P50 engine commit `8548ecb650bb17a7f671829b740609f6a72cac5a`; P50 focused suite: 43 passed, 1 platform skip; n8n adapter/export suite: 13/13 passed. P50 branch advanced by one two-line receipt-ID collision guard after `990e0cd`; latest focused suite remains 43 passed, 1 skip.
- [ ] Identify and configure the intended production P50 n8n runtime. Current default n8n profile has no P50 workflow; the separate P6 custom profile was not found.
- [ ] Decide and explicitly approve a P50 schedule cadence and per-run/page budget. The checked-in P50 workflow currently fires every five minutes; do not activate it because that would exceed the one-page live approval and create recurring Meta calls.
- [ ] Configure the deferred credential-backed failure alert channel and test delivery with a mock event before unattended operation.
- [ ] Obtain approval before each further live collection scope. Preserve the 85% usage stop, cooldown, no-retry behavior, and P50-only storage.
- [ ] Verify scheduled P50 runs, A/B/C lane state, durable receipts, RAW/Capture/coordinator readback, restart behavior, duplicate prevention, and failure alerting without affecting P6 data.

## P294 expansion — paused

- [ ] Keep P294 live activation paused until P50 collection and n8n execution pass their acceptance checks.
- [ ] Reuse the common engine and n8n boundary; do not create a second P50-only collection engine.
- [ ] Before any P294 live run, explicitly approve the operating scope and verify the frozen 294-account roster, lane safety gates, separate stores, cursors, and collection history.

## Later output integration

- [ ] After n8n core completion, implement the existing Google Sheets output specification as a derived, idempotent sync from verified SQLite/RAW receipts. Request Google authentication and spreadsheet ID only when this step begins.

## Current gates

- One P50 live page is verified; broader P50 collection and scheduled n8n execution are **not** verified.
- The current n8n pin is `8548ecb650bb17a7f671829b740609f6a72cac5a`; focused P50 tests pass locally with one platform skip, and the matching P50 GitHub offline/frozen-design checks passed. n8n adapter/export tests pass 13/13. The earlier `9f165b8` engine's four `WinError 32` teardown errors remain historical at that old SHA; do not carry them forward as a current failure or erase the earlier incident. Full n8n-branch CI at `4088bad` failed one Windows test fixture path-alias assertion; corrected locally, pending new-SHA CI.
- No further Meta request, P50 schedule activation, or P294 activation is authorized by the one-page approval.
