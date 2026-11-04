# R44 — inline singleton TimeSeries bucket state

**Status: C++ research candidate, not yet native-validated or performance-measured. Do not promote.**

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
