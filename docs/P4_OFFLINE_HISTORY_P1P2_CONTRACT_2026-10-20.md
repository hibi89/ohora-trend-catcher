# P3/P4/P5 — offline history through P1 CaptureJournal + P2 RAW v2

**2026-10-20 KST · Candidate implementation, NOT production approval or P3–P5 acceptance**

Branch: `codex/p6-readiness-hardening-20261019`. Draft PR #6 remains
unmerged. This document does not override
`docs/P6_FINAL_COLLECTION_DESIGN_2026-10-20.md`.

## Delivered: one historical page from a supplied fixture

1. `RequestEnvelope` now supports `phase="history"` **only when**
   `history_offline_only=True` is supplied explicitly together with
   a real previously bound cursor and fixed `limit=4`. A history envelope
   without the flag, or a front/catchup envelope with the flag, is refused.
   The canonical encoder **omits** the new flag when False, preserving
   byte-for-byte digest compatibility for existing v1.0 front captures.
2. **Both** `MetaTransport.send()` and
   `ThreeApiContractExecutor.execute()` independently **reject history**
   even if its offline fixture flag is True. The production transport
   remains unauthorized. There is no history HTTP sender or scheduler.
3. The explicit no-sender `OfflineHistoryFixtureIngress` checks a
   caller-supplied `RequestEnvelope` and `ResponseEnvelope` against
   a P3 bound history job (roster, account, lane, page, request ID,
   immutable spec digest and input cursor). It uses the existing
   `CaptureJournal.save_intent`, `save_response_evidence`,
   `capture_record_from_response`, `persist_capture_verified` and
   `commit_raw_and_confirm` sequence to retain an allowlisted immutable
   source, commit a real-schema P2 RAW receipt and acknowledge it.
4. P2 `write_capture` still refuses any **bare history dict**, and only
   accepts history from a validated `CaptureRecord` whose immutable
   request explicitly carries `history_offline_only=True`.
5. Separately, `OfflineEpochContractProjector.project_history_page(job_id)`
   applies the same complete, read-only P1 source + P2 receipt, RAW post/
   media and versioned observation-state checks as
   `project_front_page()`. **Only then** does it call
   `ThreeApiEpochStore.commit_history_page` to advance the durable P3
   history cursor. It never writes to P1/P2 or sends HTTP.

### Crash/restart contract

- P1 safe response saved; capture/RAW incomplete: keep P3 bound. A
  manual local replay of retained safe/captured bytes is required.
- P1 capture complete, P2 RAW pending: keep P3 bound; explicit local
  `commit_raw_and_confirm` can resume from the immutable record.
- P2 RAW committed, P1 ACK missing: keep P3 bound; explicit local
  `ThreeApiContractExecutor.recover(run_id)` verifies and confirms
  without transport.
- P1+P2 complete, P3 cursor pending: read-only history projection
  advances P3; replay of the same evidence yields
  `already_committed` and never creates another post observation.
- Existing P3 bound history may complete after next KST daily seed.
  The daily front still preempts **new** history allocation/binding.

Tests: `tests/test_three_api_history_contract_offline.py`.
These fixtures also check hard no-history HTTP boundaries, front-capture
canonical digest compatibility, conflicting response replay, RAW tampering,
bare history dict rejection, and a **two-page** fixture with all three
SQLite stores closed/reopened between pages and newer daily front pending.

## Boundaries that are still unresolved

**This is a genuine P1/P2 real-SQLite-schema history *fixture* path, not a
live history integration.** Synthetic `ResponseEnvelope` data is supplied
by the test caller; it is not obtained via an approved Meta HTTP request.
The P3 daily chain used as the history root in these tests includes
synthetic fixture receipts. This cannot certify live 294-account coverage.

A separate approved future work item must safely authorize and budget real
Meta history transport, connect it to the P3 priority/usage gate, prove live
source authenticity and validate real 365/730-day publication cutoff.
Do not derive historical engagement from present-day observations.
The earlier `OfflineHistoryRawBridge` and cutoff audit are separate
synthetic-only test adapters and are not replacements for the P1/P2 stores.

The RAW test connection is an isolated clone/in-memory schema. The writer
still blocks `collection.sqlite`. Nothing here authorizes mutation of
existing operational DB/CSV, accessing actual Meta credentials, running
unattended jobs, or merging main. Frozen **294 accounts, three API lanes,
limit=4, exactly two daily front rounds** remain unchanged.

## Latest verification checkpoint

Initial history integration tests identified a legitimate P2 refusal of
all history phases. The writer's acceptance was narrowed to explicitly
offline-flagged typed immutable CaptureRecords; no bare history writes are
allowed. The corrected `559ad06d` [RAW offline CI
#39382934084](https://github.com/hibi89/ohora-trend-catcher/actions/runs/39382934084)
ran **713 tests PASS** including P1/P2/P3 history integration. A later
two-page cross-DB/KST rollover regression was added in `3247968b`.
Check the **terminal** [Three API
#39383267996](https://github.com/hibi89/ohora-trend-catcher/actions/runs/39383267996)
and [Runtime Linux/Windows
#39383267994](https://github.com/hibi89/ohora-trend-catcher/actions/runs/39383267994)
before claiming that latest checkpoint accepted.
