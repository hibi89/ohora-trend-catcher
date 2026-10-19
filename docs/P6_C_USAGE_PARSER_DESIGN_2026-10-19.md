# P6-C — Meta usage parser boundary proposal

**Status: proposal only; NOT a frozen contract or live-readiness authorization.**
Inputs checked: `three_api_graph_http._safe_headers`, `three_api_usage.parse_usage` and `observe_headers`, `three_api_coordinator`, A's `MetaTransport.send`, C's `safe_usage_metrics`. No HTTP or DB writes performed.

## Existing evidence and mismatch
- HTTP transport retains exactly `x-app-usage` and `x-business-use-case-usage`, with JSON string bodies, lowercased keys.
- Existing `three_api_usage.parse_usage(headers)` parses these JSON headers and returns the **maximum** of `call_count`, `total_cputime`, `total_time` across supported nested business entries; malformed/missing/out-of-range => `None`.
- Existing `observe_headers` calls coordinator policy; `test_three_api_usage.py` verifies 84 permitted, 88 halted, missing halted. Existing coordinator uses >=85 threshold; this is more conservative than “>85”. **Keep >=85** unless lead explicitly changes the already-tested policy.
- C `safe_usage_metrics` currently expects imaginary scalar header keys (`x-app-usage-call-count` etc.). **Do not pass these to A or claim they reflect Meta.**
- A's `MetaTransport.send` currently always raises `usage_missing/global` for successful HTTP; this is an intentional fail-closed placeholder, not a live-ready path.

## Minimal shared boundary (proposed)
1. Keep `GraphHTTPClient.fetch` and `three_api_usage.parse_usage` as the **single** source parser; do not introduce a second JSON parser in C. Use `parse_usage(result["headers"])` on the volatile HTTP response before creating any success Capture.
2. If `parse_usage` returns `None`, reject success with `TransportFailure(category="usage_missing", scope="global", retryable=False)`; record sanitized failure evidence and block further requests. Do not persist raw headers.
3. If utilization **>=85**, block further requests with `TransportFailure(category="usage_limit", scope="global", retryable=False)` and preserve only sanitized numeric evidence. A must coordinate cooldown through existing policy; a successful HTTP response is not approval for another call.
4. If valid and <85, the adapter may construct `ResponseEnvelope` with the original allowlisted headers **in memory only**, and produce `CaptureRecord.usage_metrics={"max_usage_percent": <finite numeric>}` (or equivalent agreed typed field). C's existing `safe_usage_metrics` signature/implementation must be replaced or wrapped **only after** review; otherwise C's `capture_from_response` will still fail closed. Never synthesize fake header names to satisfy C.
5. Use bounded integer/float percentage only (0–100, finite, no bool), with a single documented percent unit. If contract remains `Mapping[str,int]`, use a conservative integer representation (e.g., ceiling) rather than rounding down. Reject unexpected types. The returned maximum is a **quota signal**, not proof of remaining call count or independent A/B/C quotas.
6. Before wiring A: synthetic tests for case-insensitive keys, app and nested business JSON, multi-entry max, 84 vs 85 vs 86, missing/malformed/unknown-only/out-of-range/NaN/bool, no raw header persistence, no automatic retry, global halt; ensure source failure remains distinct from RAW success.
7. Revisit security boundary: existing `parse_usage` traverses arbitrary nested objects, so A/C should review JSON depth/byte bounds and unsupported extra fields; do not silently broaden accepted shapes. `GraphHTTPClient` already bounds response body but not individual header size; set a bounded header limit at the integration adapter if needed.
8. Existing A `MetaTransport.send` already checks request lane and target caller identity, but successful Business Discovery target id/username must also be verified before capture. Opaque cursor must use existing validator, never `paging.next` URL. These remain separate open freeze gates.

## API handoff (proposed, not implemented)
```python
def evaluate_meta_usage(headers: Mapping[str, str]) -> int:
    # calls existing three_api_usage.parse_usage; returns sanitized max %
    # raises typed usage_missing/global or usage_limit/global
    ...
```
Do **not** make A transport return a success envelope until a reviewed adapter integrates this result with C's `capture_from_response`, and the offline tests above pass.

## Freeze gates
A/C agree exact numeric evidence schema; lead approves >=85 policy and missing-header behavior; B adapter remains separately reviewed; run offline contract and usage suites; explicit separate authorization required for any future live execution.
