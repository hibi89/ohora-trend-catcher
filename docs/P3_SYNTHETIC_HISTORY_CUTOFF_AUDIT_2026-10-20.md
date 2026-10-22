# P3 365/730-day cutoff verification — synthetic offline evidence only

**2026-10-20 KST. Candidate diagnostic, NOT production coverage certification, and NOT P3/P4/P5 acceptance.**

## Requirement and implementation

The frozen design states that a fixed-target history window is established by
**fixed anchor + cutoff**, a rooted cursor chain without missing receipts,
**publication-timestamp evidence**, and an actual cutoff crossing or a
*verified* terminal. MIN(published_at) by itself, cursor=NULL by itself, or
replaying yesterday's state are not proof of historical completeness.
Today-observed engagement is not reconstructed engagement from prior dates.

New read-only module: `raw_v2/three_api_history_cutoff_audit.py`, entrypoint:

```python
audit_synthetic_history_cutoff(epochs, mirror, history_epoch, account_key)
```

Inputs MUST be P3 `ThreeApiEpochStore` and isolated, **synthetic-only**
P4 `OfflineRawMirror`. The module does not open files, use credentials,
make HTTP requests, schedule history, or write a coverage status.

It cross-checks:

1. Immutable roster, history target cutoff/anchor and the history root's
   seed cursor against the daily source's immutable RAW-receipted chain.
2. Each completed P3 history job's sequential page number, predecessor,
   bound request/cursor, original synthetic staging digest, P3 page hash
   chain, capture time and committed isolated RAW page receipt/digest.
3. Exact ordered media IDs in the source and immutable P3 page receipt,
   plus mirrored RAW observations with matching account identity, clock,
   engagement field values and presence flags.
4. Every publication timestamp is timezone-aware, not after the fixed
   history anchor or capture instant, and progresses monotonically from
   newer to older across the complete visited chain. Duplicate post IDs
   or missing timestamps reject the proof.
5. There is no unfinished history page, the history checkpoint matches
   the last proven page, and the recorded oldest publication time is at
   or before the fixed cutoff, **OR** a verified terminal page was reached.

A result returns `synthetic_cutoff_verified` and a distinct
`production_coverage_verified=False` field. A valid synthetic proof
reports `coverage_status=cutoff_reached` or `verified_terminal`.
A missing/incomplete proof reports `insufficient_evidence` or
`insufficient_depth`; a missing/malformed publication timestamp is
rejected, never converted to an invented date or zero.

**No coverage_ledger status is promoted by this diagnostic.** The store
may still report `partial` for these fixtures: that is intentional.

## Verification scenarios

Regression source: `tests/test_three_api_history_cutoff_audit.py`.

- Merely possessing the seeded valid historical cursor cannot establish
  a date-window completion.
- Two ordered pages reach a 365-day cutoff with independently verified
  local capture and RAW receipts; duplicate read-only audit causes no
  database modifications.
- Explicit verified terminal before the cutoff has a different reason
  from a cutoff reached by timestamps.
- The same daily root can seed independent 365-day and 730-day target
  epochs. A valid 365-day cutoff is **not** a 730-day proof until
  additional older historical publications and their receipts are verified.
- Missing time, out-of-order publications, an altered RAW observation
  and a newer unfinished history page each prevent successful proof.

CI: `Three API offline tests` (compile + dedicated suite),
`P6 Runtime Integration Offline` (Linux complete socket-blocked suite
and 4 Windows shards). Check terminal conclusions before acceptance.

## Limits, next gates and immutable operational constraints

- Synthetic `OfflineRawMirror` is **not** the operating RAW v2 schema.
  The synthetic history adapter accepts already-supplied fixtures, not
  live Meta responses. Its "verified_terminal" represents the fixture's
  recorded terminal basis, not proof that Meta really returned an empty
  terminal page.
- The seeded daily source's **publication timestamps are not independently
  verified** by this diagnostic. It verifies the supplied synthetic
  history page chronology after that already-rooted source. This is
  **not** a 365/730-day production readiness assertion.
- Production RequestEnvelope still refuses history. Extending it safely
  requires separate authorized Capture/RAW history contracts, actual
  Meta response/receipt validation, and a full multi-DB recovery drill.
- No module in this work downloads media bytes or reconstructs historic
  engagement from the date of publication.
- The selected target remains 294 official accounts, **three** Meta API
  lanes, `limit=4` and exactly **two** daily front rounds. No Meta
  live calls, operating DB/CSV writes, unattended history scheduler or
  `main` merge without explicit user authorization.
