# R44 — inline singleton TimeSeries bucket state

**Status: REJECT UNCONDITIONAL. 160-case native semantic PASS, 1.33m sparse A/B/B/A shows no proven speedup, project tracked query memory +8.86%, and dense rate query CPU +32.91%. Do not promote.**

## Completed native results (2026-11-04)

[Immutable R44 source-matched 1.33m + dense evidence](../ts-lab/evidence/R44-native-1330k-dense-local-20261104.json), [R44 build 41805775727](https://github.com/moleus/ClickHouse/actions/runs/41805775727), [160 native output/errors/plan PASS 41807197978](https://github.com/moleus/ClickHouse/actions/runs/41807197978).

R43/R44/R44/R43, 4 fresh servers under 4CPU/4GiB/2.85GB, exact same 1.33m/14d RAW_UUID physical parts and complete fixture-generated oracle for every native result, two warmups+four measured requests per query each phase.

| Native query | R43 query memory | R44 | R43 wall | R44 wall | R43 drift |
|---|---:|---:|---:|---:|---:|
| Whole by(stage) | 839.531MB | 838.654MB | 7295.75ms | 7199.25ms | 8.60% |
| Whole without | 839.532MB | 838.654MB | 7876.25ms | 7499.75ms | 8.31% |
| stage-only | 53.506MB | 53.506MB | 730.75ms | 616.75ms | **51.11%** |
| project-only | 189.404MB | **206.181MB (+8.86%)** | 1305.25ms | 1283.75ms | **20.57%** |

Sparse wall/CPU differences are not robust relative to control drift, and project query memory regressed. **Reject unconditional R44 promotion.**

Dense negative control: `avg by(stage)(rate(dense_counter[5m]))` over 8192 series ×256 samples/series at 15s; every one of 532 points matches independent analytic rate 0.2 ±1e-9. Four fresh R43/R44/R44/R43 phases: wall **87.25→115.75ms (+32.66%)**, CPU **85.55→113.70ms (+32.91%)**; baseline CPU control drift13.41%; query memory unchanged at38.675MB. This confirms a major dense regression for the tested workload. Do not confuse floating-point bitwise variation with failure of the 1e-9 analytic oracle.

R45 investigates an exclusive variant-based compact state, so `Map` and `SingleSample` never occupy separate members simultaneously; its value must be measured directly, and R44's regression must not be inherited.

## Mechanism

A single-sample sparse time series normally allocates a TimeSeries bucket-map even after R34 reduces the initial capacity to two cells. Most CI series have **exactly one sample**. R44 holds this first `(bucket_index, timestamp, value)` triple inline within the aggregate state. It uses neither an allocated bucket hash map nor an allocated sample bucket until a second sample arrives. On any second sample it replays the original first sample into R34's unmodified hash map and processes all later inputs with the original implementation.

The original aggregate functions and math are unchanged. For a singleton on finalization, R44 creates the same original `Traits::Bucket` on the stack from that one sample, then calls the same sliding aggregator and null/stale-window logic at each evaluation step. Dense states continue through the exact original hash-map path.

Merge of partial states copies a singleton when the destination has no data, or merges a temporary one-sample original Bucket into the hash map after a spill. Serialization writes exactly the same format version, bucket count, one-key entry and Bucket payload as an original one-bucket map. Deserialization uses the original full hash-map implementation; there is no new wire format or feature flag.

## Provenance and falsifiers

- Source pin: `52529f0c711566d696917678bf15e7068772fdde`, LLVM22.1.8 Release -O2 jemalloc.
- [R44 helper](apply_inline_single.py) validates the exact R34 patched header by reconstructing and hashing the original Git blob. It refuses an unexpected source.
- [R44 isolated CI build](../workflows/ts-lab-r44-inlined-single-sample-o2-20261104.yml), [native 160-case guard](guard_suite.py).
- [Canonical root](../ts-lab/claims/roots/R44.json) and [primary claim](../ts-lab/claims/R44-inlined-single-sample-timeseries-state-v1.json).
- Baseline: R43 built under the identical pinned source, same source-level compiler and same 2-cell grower.
- Candidate changes only `AggregateFunctionTimeseriesBase.h` after R43. **No selector, planner, tag collector, index, stored schema or output format changes.**

Required checks: 160 native PromQL semantic/plan cases against R43 including multi-sample series, NaN, infinity, staleness and mixed-metric errors; exact full 1.33m/14d sparse native output oracle and A/B/B/A wall/CPU/tracked-memory/RSS; dense 8192×256 counter rate oracle; special sample-merging and serialization-compatibility controls. If any correctness, dense regression, capacity or source-pin gate fails, the patch remains rejected or inconclusive.

R44 is **not** a verified breakthrough until its binary and repeated native measurements pass.
