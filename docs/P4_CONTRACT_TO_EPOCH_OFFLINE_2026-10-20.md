# P4 — Durable P1/P2 Capture/RAW v2 → P3 daily-front proof

**2026-10-20 KST · Feature branch only · Offline integration candidate, not P3/P4/P5 acceptance**

Source code: `raw_v2/three_api_epoch_contract_projection.py`. Tests:
`tests/test_three_api_epoch_contract_projection.py`. CI:
`.github/workflows/p6-runtime-integration-offline.yml`.

## What is actually wired

The P1/P2 code already has a `CaptureJournal` and a
`ContractRawWriter` for an isolated RAW v2 database. The new
`OfflineEpochContractProjector.project_front_page(job_id)` consumes those
existing durable objects with a caller-owned `ThreeApiEpochStore`.

A bound **P3 daily-front** job may be marked done only if:

1. An immutable stored P3 binding matches the persisted captured request's
   294-account roster digest, KST epoch day, account key/ordinal/username,
   lane A/B/C, page number, opaque input cursor and fixed `limit=4`.
2. The first-retained safe response evidence exists in `CaptureJournal`,
   has a verified body digest, and its received timestamp matches the
   immutable `CaptureRecord.observed_at`. The sanitized source digest must
   agree with `CaptureRecord.cursor_state.source_digest`.
3. The separate RAW v2 connection returns a **committed contract receipt**
   for that exact capture ID and digest, and CaptureJournal carries the
   corresponding processed RAW acknowledgement.
4. RAW `post_observations` rows match the capture's post IDs, observed
   timestamp, like/comment values and *field-presence* flags, with the
   recorded post owned by the expected account.
5. Only after all read-only cross-DB checks pass does the adapter call
   `ThreeApiEpochStore.commit_daily_page` to commit the immutable P3
   page/cursor/checkpoint. A short terminal page marks descendant jobs
   `not_required` rather than fabricating observations.

No network transport, quota selection, retry, scheduler, new capture,
RAW write or synthetic RAW mirror is present in this adapter. A missing
Capture, retained response, RAW receipt, acknowledgement or observation
raises a sanitized error and leaves P3 bound, without progressing its cursor.
A previously bound older KST day's response may finish with its original
epoch after rollover; NEW old-day bindings remain blocked in P3.

### Durable restart sequence

If a real response's safe evidence and capture are already stored but the
RAW commit has not completed, call the **existing explicit**
`commit_raw_and_confirm`/P1-P2 recovery path on an approved isolated DB.
If the RAW commit succeeded but acknowledgement failed, complete recovery
through `ThreeApiContractExecutor.recover(run_id)`. Only *then* run
`project_front_page`. Repeating the projection after an unchanged committed
receipt returns `already_committed` without duplicating observations.

Tests cover: fully committed success + unchanged replay; capture saved
without RAW receipt; RAW committed but its acknowledgement interrupted;
mismatched KST day; and a post-observation value corrupted after commit.
All fixtures and RAW schemas are isolated/in-memory. Neither tests nor
projector perform Meta calls.

## Important limitations / acceptance gates

- This is **front-page only**: P3 page1..3. The production
  `RequestEnvelope` **still explicitly refuses `phase='history'`**.
  The earlier `OfflineHistoryRawBridge` uses a synthetic mirror; it does
  not represent production history support.
- This validates saved RAW *contract receipts* and targeted observation
  rows. It is not a complete three-DB operator audit for all media fields,
  account observations, carousel completeness, catch-up, or historical
  cutoff verification. Further acceptance work is required.
- The existing RAW writer rejects the operating `collection.sqlite`.
  Do not run this against operating data; use only isolated test/clone
  databases after explicit approval. No permissions to touch production,
  call live Meta, run unattended tasks, or merge the draft PR are implied.
- Full 294-account/882-page P3 *synthetic* epoch scheduling was tested in
  `test_three_api_epochs.py`, but this does **not** prove all 882
  CaptureJournal/RAW writes actually occurred.
- Latest Linux/Windows terminal CI success is required before declaring
  even this integration candidate validated. Tests are explicitly included
  in the Runtime workflow's change filters and compile list; the socket-
  blocked `tests/run_offline.py` discovers all test modules.

Next: inspect CI job results, fix any failures, extend actual RAW receipt
verification as necessary, and design separately reviewed history contract
support preserving immutable source and all frozen 3 API × limit 4 × two
front-round rules.
