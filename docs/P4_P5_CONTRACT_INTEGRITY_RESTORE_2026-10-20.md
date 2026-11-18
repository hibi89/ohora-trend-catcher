# P4 / P5 — durable contract integrity and three-store restore drill

**2026-10-20 KST. Synthetic offline verification only, NOT P4/P5 acceptance.**

## Scope

Source: `raw_v2/three_api_epoch_contract_projection.py`. Regression tests:
`tests/test_three_api_epoch_contract_projection.py`.

The projector has **no transport**, doesn't create captures or RAW data, and
doesn't approve history. The caller must provide an existing bound P3
**daily-front** job, a persisted P1 CaptureJournal and an isolated RAW v2
ContractRawWriter receipt. Only the verified receipt and post evidence may
advance the P3 cursor.

### Newly enforced proof chain

1. P1 retained safe *pre-normalization* response: SHA-256, HTTP success
   status and actual received_at are checked against the corresponding
   immutable P1 CaptureRecord. Re-sanitizing the retained safe response
   must give the **identical** allowlisted CaptureRecord response. This
   catches a coherent but altered response and recomputed checksum that
   would evade validating the two snapshots independently.
2. P2 receipt: same immutable Capture ID/digest and acknowledged RAW
   receipt; P2 RAW rows checked for exact observation clock, engagement
   values including **NULL versus zero and present versus absent**, plus
   recorded post ownership.
3. P2 append-only observation states: query observation identity/quality,
   each post's per-request state references, **all four** versioned
   post components (content, metrics, structure, locator) and field-state
   values are checked against the immutable CaptureRecord.
4. Media evidence: single image/video media ownership and role must match;
   for supplied carousel children, the per-observation P2
   `media_structure_members` records must retain the exact ordered
   child IDs and types, and the underlying media identities must exist.
   Previously seen post_media rows may retain their original order; the
   observation-specific structure is used to verify later reordering.

A failing check leaves P3's bound job **uncommitted**. The projector does
not repair or fabricate source/RAW rows.

## Synthetic fault injection and restart verification

Five original contract projection cases remain; additions test:
- A validly re-digested but altered safe response, and a rewritten 400
  HTTP status cannot be attributed to an already committed successful
  capture.
- Tampered P2 versioned content, modified query quality, missing/wrong
  single-media identity, or a corrupted carousel child order cannot
  advance P3.
- A valid carousel's post checks, child order and RAW media ownership can
  be verified without downloading any media bytes.
- Back up both in-memory RAW and P3 epoch into **isolated temporary files**,
  close P1/P2/P3 handles, reopen the existing P1 capture file and the
  resulting isolated RAW/epoch snapshot files, and project a retained
  capture without ever sending a new HTTP request.
- Crash after RAW commit but before P1 processed ACK: restart keeps P3
  bound; only the existing explicit local `recover()` verifies/acknowledges
  the RAW receipt. Subsequent projection succeeds with zero transport calls.
- Restart after P1 durable capture but before RAW: projection is blocked,
  RAW is not automatically written, and an explicit local
  `commit_raw_and_confirm` is required before a P3 commit.

### CI gate

The revised Runtime workflow executes the entire **socket-blocked**
`tests/run_offline.py` suite on Linux and all four Windows test groups.
At source `985be0f3`, Linux [Runtime #39380369941](https://github.com/hibi89/ohora-trend-catcher/actions/runs/39380369941) passed **635 tests**. Full Windows job conclusions were
not yet terminal at this documentation checkpoint. The frozen design guard
for the same commit passed.

## Known limits and next work

- These test schemas use synthetic fixtures and approved **isolated**
  SQLite files, not the running operational database.
- Raw replay and epoch updates are separate SQLite commits. The drill
  validates recovery when all three isolated backups form a **consistent
  snapshot**, not arbitrary independently timed backups, concurrent writes,
  WAL checkpointing, or real deployment backups.
- Media integrity currently checks P2 state values and media identities;
  this does **not** prove remote URLs still resolve or that image/video
  bytes have been downloaded.
- No live Meta, quota authorization, unattended scheduler, or production
  database modification. P3 production history Capture request remains
  disabled by `RequestEnvelope` and is not enabled here.
- Further gates: real production-approved history Capture/RAW v2 contract,
  365/730-day cutoff evidence, actual 294-account end-to-end capture
  execution, concurrent crash-boundary/restore tests and all Windows
  terminal passes; then P5 operator acceptance.

Frozen design (294 official accounts, 3 API lanes, limit=4, two daily
front rounds) unchanged. Draft PR #6 remains unmerged.
