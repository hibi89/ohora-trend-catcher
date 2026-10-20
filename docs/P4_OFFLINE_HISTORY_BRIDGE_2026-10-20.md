# P4 synthetic history capture → RAW → epoch bridge

**2026-10-20 KST. Status: offline feature candidate, NOT P3/P4/P5 acceptance.**

This file supplements the frozen P6 design; it does **not** alter or override
`docs/P6_FINAL_COLLECTION_DESIGN_2026-10-20.md`. The three Meta API lanes,
`limit=4`, the exactly two daily rounds and the official 294-account roster
are unchanged. No Meta calls, operating DB mutations, unattended work, or
`main` branch merge are authorized.

## Delivered in branch

- `raw_v2/three_api_history_offline_bridge.py` is an explicit manual,
  **synthetic fixture-only** adapter between the P3 `ThreeApiEpochStore`
  and the isolated P4 `OfflineRawMirror`. It does not use the production
  capture journal, production RAW writer, or HTTP client.
- `stage_capture(job_id, request_id, observed_at, posts, next_cursor,
  terminal_basis)` saves a canonical allowlisted fixture and its immutable
  digest in `offline_history_capture` in the P3 epoch SQLite connection.
  It requires a previously **bound** legitimate history job and stores at
  most 4 posts. It does not advance any checkpoint.
- `reconcile_one(job_id)` reads **only persisted** fixture bytes, validates
  binding/digest, stages and promotes the corresponding immutable page in
  the separate `three_api_raw_mirror.sqlite`, checks RAW receipt identity,
  post observations and parent/carousel-child ordering, then invokes
  `commit_history_page` to advance the saved history checkpoint.
- `reconcile_pending()` explicitly replays stored fixtures in insertion
  order. A repeated identical fixture/RAW receipt/epoch commit is idempotent;
  conflicting fixtures or a tampered persisted source/RAW observation are
  rejected instead of re-fetching or overwriting evidence.
- A parent carousel with child image/video URLs is converted between the
  source's canonical `media_id/item_order` representation and the RAW
  writer's input `id` child representation, retaining the original order.

## Failure boundaries / verification scope

| Crash point | Durable remainder | Recovery |
|---|---|---|
| After `stage_capture` | Bound job, immutable P3 fixture | `reconcile_one` promotes stored posts to RAW then commits epoch |
| After mirror stage | P3 fixture + staged mirror receipt | Replay promotes the same receipt, then commits epoch |
| After mirror promotion | P3 fixture + committed RAW receipt | Replay checks RAW values and commits epoch without another observation |
| After epoch commit | P3 history cursor + RAW receipt + source | Replay returns `already_committed` without moving the cursor |

Tests in `tests/test_three_api_epochs.py` cover two-page continuation and
terminal, duplicate replay, failure injected immediately before the epoch
commit, immutable fixture conflict, staged-source tampering, stored RAW
observation tampering, carousel-child URL/order and SQLite restart from a
file-backed snapshot. The already-present epoch tests cover 294/882 fixed
assignment, daily-front priority, manual-review isolation and duplicate
history allocation safeguards.

## Crucial limitations

1. This bridge verifies **supplied synthetic fixtures only**. It does not
   accept a captured real Meta response or prove real-world 365/730-day
   coverage. It cannot serve as live history authorization.
2. The **production `RequestEnvelope` explicitly rejects history**. A
   P3/P4 contract extension with safe pre-normalization response retention,
   actual transport intent, immutable capture receipt, RAW v2 writer receipt,
   usage controls and unknown-outcome quarantine is still required.
3. The mirror is a synthetic test database, **not** the actual RAW v2 writer.
   Its committed receipt attests test storage only; it does not certify
   production original-response preservation.
4. Epoch and mirror are **separate SQLite transactions**; not atomic across
   files. Only the ordered replay path makes interruption recoverable.
5. No `history` request can be dispatched from this module, either
   implicitly or with surplus budget. It must be invoked manually after
   externally supplied offline fixture evidence.
6. Newest KST daily front priority, affected-account review barriers and
   immutable checkpoints are still enforced by `ThreeApiEpochStore`.
   Already-bound history can be committed after a rollover if verified.
7. 294-account × 882-page complete synthetic end-to-end verification,
   multi-day cutoff tests, cross-file backup/restore, Windows terminal CI
   and P5 acceptance remain unfinished. Do not claim P4 complete based on
   these isolated checks.

## Status gate

Source `644c6c4`, tests `3bb1989`, canonical carousel conversion
`000c53d`, extended regression `12078e9`. The Three API job for the
latest regression is
https://github.com/hibi89/ohora-trend-catcher/actions/runs/39376311588
and full Linux/Windows runtime is
https://github.com/hibi89/ohora-trend-catcher/actions/runs/39376311600 .
Check **terminal job conclusions** before acceptance.
