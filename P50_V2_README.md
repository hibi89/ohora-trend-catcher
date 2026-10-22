# P50 v2 — standalone 50-account collector

Branch: `feature/p50-v2-standalone-collector-20261023`

## Goal
50 accounts, 8 posts per lane A/B/C per cycle, up to 20 cycles (480 posts/account maximum), cutoff 365 days. This is a separate pilot from the frozen 294-account collector.

## Files
- `raw_v2/p50_cycle_engine_v2.py`: SQLite cursor, cycle, lane and receipt state.
- `raw_v2/p50_v2_collector.py`: opt-in Meta Business Discovery HTTP collector, isolated RAW pages and post-ID index.
- `tests/test_p50_cycle_v2.py`: offline planner tests.

## Offline check
```powershell
python -m unittest discover -s tests -p test_p50_cycle_v2.py -v
python raw_v2/p50_v2_collector.py --source "<P50_CSV>" --workspace "$env:LOCALAPPDATA\OHORA_P50_V2\p50_v2" --credentials-dir "<ENV_DIRECTORY>"
```

## Live: do not execute until validation
The live entrypoint requires `--live --confirm-owner-approval`, `--max-requests`, and operator-verified quota usage (e.g. `--verified-usage-a 1 --verified-usage-b 1 --verified-usage-c 1`). Usage values MUST come from a current, verified quota response, never a guess.

**Known limitations, blocking production use:**
1. No automatic safe recovery/reconciliation of a reserved HTTP attempt. A crash intentionally blocks replay; operator must inspect RAW and Meta response evidence.
2. Graph `media.limit(8)` has not been validated live against this Meta account setup.
3. Usage header parsing and quota gate propagation have not been end-to-end tested.
4. Post IDs are indexed for deduplication, but pagination and 365-day cutoff semantics need live verification.
5. The first-pass planner does not yet implement dynamic reassignment of unused capacity when a lane is unavailable.
6. The collector does not yet persist an immutable request fingerprint or enforce a single writer lock across processes.
7. The repository tests were added but have not been executed in the user's Windows environment.

Do not claim the 50-account live collection is complete or use this code for unattended production runs.
