# P50 → n8n → P294 completion checklist

Use this list to track the user's agreed sequence. P50 and P294 use independent RAW databases, coordinators, Capture journals, and collection histories. The frozen P6 design and P6 production data remain unchanged.

## Latest n8n/P50 integration evidence — 2026-10-22

### Current recheck against P50 v3

- [x] Re-read Simulation v2.0, P50 v3, official Issue #15, and PR #16; v3 is the current design target and is explicitly design-only.
- [x] Re-fetch origin: n8n branch base `4e1469225a17d28285b09132fdb7175fbee49e1e`; P50 owner tip `e0ecd043b70c2f8f49936bc279e40d544232c862`.
- [x] Re-run the focused n8n adapter/export/handoff suite: 19 passed + 2 guard subtests. P50 engine serial-lock suite and Node classifier/E2E evidence remain as recorded below/P6-098–100.
- [x] Compare P50 history changes to n8n boundary: history internals have advanced, but the pilot CLI still exposes the old latest runner only. Keep reviewed `10ebaf8` pin and do not import engine internals into n8n.
- [ ] P50 `latest/history/status/recover` owner CLI, receipt contract, and Windows/offline verification are still required before n8n history integration.
- [ ] GitHub status for P50 `e0ecd04` was not returned by combined-status query; do not claim it green.
- [x] Diagnosed pushed n8n commit `3e59672` CI: Three API, C Contract, Frozen Guard PASS; Runtime Linux and Windows failed only because P6-101's detail lacked a quick-index table row. Added matching P6-101/P6-102 rows; local handoff guard 4 PASS +2 subtests.
- [x] Push this guard repair and verify the new CI: Three API, Runtime Linux+all Windows shards, C Contract, and Frozen Design Guard all SUCCESS on branch HEAD `fa89ef7`.

- [x] P2 result routing is fail-visible: `manual_review` and partial `attention` go through an IF node to `StopAndError`; clean progress/waiting remain recorded in n8n execution history. Focused tests 19 PASS + 2 handoff subtests PASS; pinned P50 serial-lock suite 8/8 PASS; direct Node classifier cases 5/5 PASS. `serial_busy` stays a no-call waiting outcome.
- [ ] No n8n production/instance execution was repeated after this edit. The workflow remains inactive, the user deferred the credential-backed alert channel, and no n8n schedule is authorized to run Meta calls.
- [ ] Latest P50 source tip `c4944f1` keeps the n8n `run_p50`/status/receipt boundary modules unchanged from tested pin `10ebaf8`, but the additional history modules are not integrated with the n8n tick. Keep the exact pin unchanged until an offline contract test against the candidate SHA and green relevant CI.
- [ ] Upstream P50 CI run `39727639343` has OPEN blockers: Ubuntu offline-runtime failed three handoff-link/index assertions; Windows `remaining` failed those and 17 temporary SQLite cleanup errors (`WinError 32`). P50 owner must repair/retest that branch. Do not change its source from this n8n branch.

## P50 readiness and one-page proof

- [x] Validate the supplied 50-account roster locally (50 rows; US 18, KR 16, JP 16; unique usernames; profile links agree). Keep the normalized roster under ignored local `.tmp`; never commit account identities.
- [x] Validate local A/B/C credential-file structure, unique token/caller pairs, and Graph API version without printing any values. This confirms local format only, not every lane's live permissions.
- [x] Use the existing pinned P50 engine for exactly one explicitly approved A-lane page. Verify HTTP 200, safe usage 0%, four post observations, a durable receipt, Capture acknowledgment, and coordinator completion through read-only checks.
- [x] Correct the n8n adapter's runner result-field mismatch (`status` vs `last_status`) and add an offline regression test; commit and push on the existing P6 work branch.
- [x] Synchronize `HANDOFF.md`, `STATUS.md`, the P6 troubleshooting ledger, and the central Project Book index.

## n8n completion — pending

- [x] Repeat the isolated n8n workflow end-to-end **offline/mock only** against the corrected adapter. Isolated n8n 2.40.7 execution 3 persisted `success`; the pinned launcher separately returned `OFFLINE_PREFLIGHT` / `P50_OFFLINE`, zero attempts/pages, and `live_meta_called=false`.
- [x] Align the n8n launcher pin with P50 engine commit `10ebaf895e228cd1ed09cc349d9fcb5ddcd139fb`; focused engine suite: 47 passed, 1 platform skip; n8n adapter/export suite: 13/13 passed. This SHA adds verified latest-tail history anchoring with durable evidence checks; P50 engine execution, rather than n8n, owns that feature.
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
- The current n8n pin is `10ebaf895e228cd1ed09cc349d9fcb5ddcd139fb`; focused P50 tests pass locally with one platform skip. Matching P50 engine CI was still in progress when checked; n8n adapter/export tests pass 13/13. The earlier `9f165b8` engine's four `WinError 32` teardown errors remain historical at that old SHA; do not carry them forward as a current failure or erase the earlier incident. Full n8n-branch CI at `4088bad` failed one Windows test fixture path-alias assertion; corrected locally at `6d096d8`, whose CI is pending.
- No further Meta request, P50 schedule activation, or P294 activation is authorized by the one-page approval.
