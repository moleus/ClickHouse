# E04 — VictoriaMetrics index, postings, cache and physical layout comparison
**Owner lane:** index/layout research. Coordinate with R05 (mark coverage) and R07 (day-covering metadata).
**Depends on:** E06 frozen fixtures; informs E03/E05.
**Goal:** explain *why* VictoriaMetrics scans/decodes fewer or different bytes/IDs for the same label/time selector, and determine which techniques transfer to ClickHouse TimeSeries without unacceptable ingest and HA costs.

## Source-verified entrypoints
- [VM v1.134 indexDB searchMetricIDs](https://github.com/VictoriaMetrics/VictoriaMetrics/blob/v1.134.0/lib/storage/index_db.go#L1689-L1723) checks cached matching metricIDs and then searches index; [SearchTSIDs](https://github.com/VictoriaMetrics/VictoriaMetrics/blob/v1.134.0/lib/storage/index_db.go#L1725-L1755) maps to TSIDs; [filter intersection](https://github.com/VictoriaMetrics/VictoriaMetrics/blob/v1.134.0/lib/storage/index_db.go#L2655-L2702) intersects candidate metricID sets.
- [CH v11 selector read](https://github.com/moleus/ClickHouse/blob/52529f0c711566d696917678bf15e7068772fdde/src/Storages/StorageTimeSeriesSelector.cpp#L1219-L1340) forms TAGS/TimeRanges IDs and then SAMPLES primary-key set analysis. Existing v11 bucketed path does NOT use its unbucketed whole-metric range probe.
- [Source comparison](https://github.com/moleus/ClickHouse/blob/lab/ts-cbo-adaptive-20261103/.github/ts-lab-r03/READ_PATH_SOURCE_DIFFERENCES_20261103.md).

## Audit matrix (for every mechanism record YES/NO/DIFFERENT, code, cost and evidence)
1. Time-indexed inverted postings, composite tag filters, intersection ordering and match selectivity; absent/negative/regex semantics.
2. Time partitions and metadata time locality, ID lifespan/part locality, sorted IDs and SAMPLES marks; measure precision/recall of mark/index pruning.
3. Tag-filter result cache and cache-key invalidation under ingestion, TTL, label changes, backfill and tenant access; distinguish response cache.
4. Storage part/block layout, per-series grouping, compression, time-bucket array, selective decode, seek paths, merge/compaction and write amplification.
5. Whether existing ClickHouse text/bloom/minmax indexes, materialized label columns, projections, ID-prefix range hints, time-partitioned metadata, external postings or sidecars can supply comparable access. Do not assume a postings index reduces physical marks.

## Research procedure
- Trace exact source call chain for VM and CH; run same query with tracing, index cache warm/cold counters, CPU stacks, matched IDs, candidate sets, selected marks, decoded bytes and actual SAMPLES rows.
- Derive lower-bound mark hit model from true ID/mark distribution; compare to measurements. Benchmark two-factor label cardinality × window selectivity, original vs additional index, same fixture/parts, one change per pair.
- Measure full additional-write cost: index update, merge, byte growth, retention/compaction, rebuild/restart and read consistency under concurrent insert.
- Reuse R07 day-covering research and R05 mark studies; create no rival root or duplicate index prototypes.

## Acceptance
- A documented source-level CH↔VM mechanism matrix with specific missing/different optimizations and honest attributions.
- Validated mark/CPU accounting; 1+ source-backed candidate whose *full* native selector p50/p95 and CPU benefit exceeds write/RSS costs, or a proved rejection.
- Exact multi-tenant labels, late writes and TTL semantics; no hidden false negatives or unsafe TAGS deletion.
## Shared execution contract
- Only write to `lab/ts-cbo-adaptive-20261103`. Do not modify `master`, upstream or production, and do not cancel existing jobs. Preserve build caches.
- Before any costly job, read [INDEX](https://github.com/moleus/ClickHouse/blob/lab/ts-cbo-adaptive-20261103/.github/ts-lab/INDEX.md), [AGENT_GUIDE](https://github.com/moleus/ClickHouse/blob/lab/ts-cbo-adaptive-20261103/.github/ts-lab/AGENT_GUIDE.md), [OPTIMIZATION_STACK](https://github.com/moleus/ClickHouse/blob/lab/ts-cbo-adaptive-20261103/.github/ts-lab/OPTIMIZATION_STACK.md), [coordination](https://github.com/moleus/ClickHouse/blob/lab/ts-cbo-adaptive-20261103/.github/ts-lab/RESEARCH_COORDINATION_20261103.md), canonical RNN root claims and current queued/in-progress jobs.
- These are **epic plans**, not RNN experimental root reservations. Existing canonical owners remain responsible for their own claims. Request a disjoint subclaim through the owner or reserve a genuinely new root using the existing create-only rule.
- Only one variable per experiment, except a preregistered factorial. Pin exact source/binary/fixture hashes and full query/DDL. For timing use matched warm-up A/B/B/A, control drift and no observer-overhead mismatch.
- Preserve all results as immutable evidence with `PASS/REJECT/NO_EFFECT/INCONCLUSIVE/INVALID_METHOD/CORRECTNESS_FAIL`; GitHub job SUCCESS alone is not scientific success.
- Protect exact labels/timestamps/numeric values, stale NaN, absent labels, repeated/late samples, counter resets, ACL, memory/OOM. Do not infer production superiority from O1 vs release or multiply separate speedups.
- Source/review work can start in parallel. CI compiles must be budgeted and must not preempt other owners.
