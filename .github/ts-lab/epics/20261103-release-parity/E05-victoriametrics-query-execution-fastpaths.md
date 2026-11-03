# E05 — VictoriaMetrics query execution, rollups, caches and CPU fast paths
**Owner lane:** execution/CPU research. Coordinate with existing R12/R17/R20 and CBO path owners.
**Depends on:** E06 oracle, E03 planner interface, E04 indexing cost.
**Goal:** compare execution optimizations beyond fast label lookup, and implement only safe transferable improvements in ClickHouse native TimeSeries.

## Source entrypoints to audit
- [VM promql/eval.go](https://github.com/VictoriaMetrics/VictoriaMetrics/blob/v1.134.0/app/vmselect/promql/eval.go): rollup execution, response caching and query-time optimization settings including label filter pushdown to binary operations. Follow callees, not only comments or flags.
- [VM storage index](https://github.com/VictoriaMetrics/VictoriaMetrics/blob/v1.134.0/lib/storage/index_db.go), [CH native converter](https://github.com/moleus/ClickHouse/blob/52529f0c711566d696917678bf15e7068772fdde/src/Storages/TimeSeries/PrometheusQueryToSQL/fromSelector.cpp), [CH selector](https://github.com/moleus/ClickHouse/blob/52529f0c711566d696917678bf15e7068772fdde/src/Storages/StorageTimeSeriesSelector.cpp).
- Prior [R07 SQL vs native](https://github.com/moleus/ClickHouse/actions/runs/41634426255): ~2x on selected pod aggregates with same selected marks; suggests compute/label handling, **not** physical mark pruning.

## Mechanism audit (evaluate each with source+CPU evidence)
1. Push label matchers and metric selection before sample fetch; reorder conjuncts by candidate cardinality.
2. Cache matching IDs, parsed query plans and rollups separately; determine cache hit rate, invalidation, per-tenant safety and cold-cache performance.
3. Fuse per-series sample decode, time-window aggregation and final grouping; avoid grid expansion/materialization when output allows a streaming reduction. Preserve `avg by(label)(avg_over_time(series[]))` semantics: average **per series first**, then group; cannot simply average all raw points.
4. Reuse per-series ID→group mappings and decoded labels across timestamps; eliminate repeated `Map`, hash, dictionary and tag serialization; measure CPU and allocator traffic.
5. Specialized safe algorithms for rate/counter resets, last_over_time and aggregate stages; preserve stale NaN, ordinary NaN, boundary inclusivity, late/duplicate points and PromQL grouping.
6. Vectorization, branch reduction, allocator/reuse, adaptive threads and avoiding oversubscription at high concurrent clients.
7. VM rollup cache/pushdown vs ClickHouse SQL partial aggregation; distinguish final output size from internal CPU.

## Method
- Profile CPU and true wall critical path with low-overhead controls; attribute per operator and isolate sample decode, hash/tag registration, aggregation/grid, join and serialization.
- Derive an upper bound (Amdahl) from observed exclusive CPU before any C++ prototype. Test pure SQL or function-level narrow oracle first.
- Safe native candidate with full differential oracle on 1.33m high-churn and R01 multisample; 1/4/8-client with finite CPU/RSS and steady write; A/B/B/A vs exact optimized stock baseline.
- Rank implementation impact by saved CPU-seconds per request and actual throughput, not microbenchmarks alone.

## Acceptance
- Source and profiling comparison matrix VM vs CH for each fast path, including which already exists in CH.
- At least one reusable native implementation with correct outputs and measured end-to-end benefit, **or** documented negative cost/semantic proof for each evaluated lead.
- Full correctness (not a limited gauge-only shortcut), no unbounded memory, no regression for broad/regex selectors.
## Shared execution contract
- Only write to `lab/ts-cbo-adaptive-20261103`. Do not modify `master`, upstream or production, and do not cancel existing jobs. Preserve build caches.
- Before any costly job, read [INDEX](https://github.com/moleus/ClickHouse/blob/lab/ts-cbo-adaptive-20261103/.github/ts-lab/INDEX.md), [AGENT_GUIDE](https://github.com/moleus/ClickHouse/blob/lab/ts-cbo-adaptive-20261103/.github/ts-lab/AGENT_GUIDE.md), [OPTIMIZATION_STACK](https://github.com/moleus/ClickHouse/blob/lab/ts-cbo-adaptive-20261103/.github/ts-lab/OPTIMIZATION_STACK.md), [coordination](https://github.com/moleus/ClickHouse/blob/lab/ts-cbo-adaptive-20261103/.github/ts-lab/RESEARCH_COORDINATION_20261103.md), canonical RNN root claims and current queued/in-progress jobs.
- These are **epic plans**, not RNN experimental root reservations. Existing canonical owners remain responsible for their own claims. Request a disjoint subclaim through the owner or reserve a genuinely new root using the existing create-only rule.
- Only one variable per experiment, except a preregistered factorial. Pin exact source/binary/fixture hashes and full query/DDL. For timing use matched warm-up A/B/B/A, control drift and no observer-overhead mismatch.
- Preserve all results as immutable evidence with `PASS/REJECT/NO_EFFECT/INCONCLUSIVE/INVALID_METHOD/CORRECTNESS_FAIL`; GitHub job SUCCESS alone is not scientific success.
- Protect exact labels/timestamps/numeric values, stale NaN, absent labels, repeated/late samples, counter resets, ACL, memory/OOM. Do not infer production superiority from O1 vs release or multiply separate speedups.
- Source/review work can start in parallel. CI compiles must be budgeted and must not preempt other owners.
