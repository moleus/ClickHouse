# E03 — Native TimeSeries cost-based planning and statistics
**Owner lane:** native query planner/estimator lead (coordinate with current CBO experiments and R05/R07).
**Depends on:** E06 oracle; E04 physical access cost data.
**Unblocks:** E01 compatible planner candidate.

## Critical verified architecture
- [Pinned selector source](https://github.com/moleus/ClickHouse/blob/52529f0c711566d696917678bf15e7068772fdde/src/Storages/StorageTimeSeriesSelector.cpp#L1167-L1340): native selector *forces* `join_algorithm=full_sorting_merge` and `optimize_read_in_order` for a nested TAGS/TimeRanges ID query, followed by SAMPLES ID set. This limits generic CBO freedom.
- [CBO checkpoint](https://github.com/moleus/ClickHouse/blob/lab/ts-cbo-adaptive-20261103/.github/ts-lab-cbo/RESEARCH_CHECKPOINT_20261103.md): `use_statistics` changed JOIN side and accelerated **diagnostic SQL** ~359.5→123.25ms; not proof of a native gain.
- [TimeRanges ON/OFF 1/3/7/14d](https://github.com/moleus/ClickHouse/actions/runs/41601352971): selective/broad/time-window crossover and OOM; no universal removal.
- [Planner cost-model sketch](https://github.com/moleus/ClickHouse/blob/lab/ts-cbo-adaptive-20261103/.github/ts-lab/TIMERANGES_ADAPTIVE_COST_MODEL.md).

## Work packages
1. Map `PromQL parser -> converter AST -> timeSeriesSelector -> inner TAGS/TimeRanges -> ID set -> SAMPLES -> grid/aggregation`. For each node show which context/settings, statistics and plan alternatives the actual pinned analyzer can use. Include source links and EXPLAIN AST/PLAN/PIPELINE.
2. Evaluate NDV/height/selectivity for `Map` labels, dedicated promoted `tags_to_columns` columns, correlated labels, regex/negative matchers, active IDs per window, compressed bytes and mark coverage. Distinguish statistics of stale metadata from live series.
3. Enumerate safe plan variants: metadata-first sorted merge; hash/semi join when bounded; TimeRanges join-or-skip; whole-metric ID-range optimization; SAMPLES-first if justified; direct label aggregation; recent table; original TAGS SQL rewrite. Verify ordering, memory and index effects.
4. Model predicted CPU/latency using **candidate count, tag scan, selected marks, parts, bucket-width, decoded bytes, join/materialization/group/grid work, and peak intermediate bytes**. Compare prediction vs actual on held-out workloads. A statistic does not itself guarantee low CPU.
5. Implement smallest opt-in native plan hook/setting only if an isolated SQL/EXPLAIN/correctness test proves a meaningful upper bound. Keep safe default/fallback with memory cap and logging of reason for plan selection.
6. Use A/B/B/A native PromQL on wide/narrow, 1/3/7/14d, mixed filters, multiple clients and concurrent ingestion; test planning-time overhead and no broad OOM.

## Acceptance
- Per-source decision map: available to SQL CBO / exposed to native / inaccessible / missing.
- Selectivity estimate-vs-actual and planned-vs-measured cost tables on independent held-out fixtures.
- Exact full PromQL equivalence plus safe no-stats fallback. Either a validated adaptive plan or documented impossibility/weak cost-benefit bound.
- No unconditional `TimeRanges` disable and no per-metric handcoded shortcuts.
## Shared execution contract
- Only write to `lab/ts-cbo-adaptive-20261103`. Do not modify `master`, upstream or production, and do not cancel existing jobs. Preserve build caches.
- Before any costly job, read [INDEX](https://github.com/moleus/ClickHouse/blob/lab/ts-cbo-adaptive-20261103/.github/ts-lab/INDEX.md), [AGENT_GUIDE](https://github.com/moleus/ClickHouse/blob/lab/ts-cbo-adaptive-20261103/.github/ts-lab/AGENT_GUIDE.md), [OPTIMIZATION_STACK](https://github.com/moleus/ClickHouse/blob/lab/ts-cbo-adaptive-20261103/.github/ts-lab/OPTIMIZATION_STACK.md), [coordination](https://github.com/moleus/ClickHouse/blob/lab/ts-cbo-adaptive-20261103/.github/ts-lab/RESEARCH_COORDINATION_20261103.md), canonical RNN root claims and current queued/in-progress jobs.
- These are **epic plans**, not RNN experimental root reservations. Existing canonical owners remain responsible for their own claims. Request a disjoint subclaim through the owner or reserve a genuinely new root using the existing create-only rule.
- Only one variable per experiment, except a preregistered factorial. Pin exact source/binary/fixture hashes and full query/DDL. For timing use matched warm-up A/B/B/A, control drift and no observer-overhead mismatch.
- Preserve all results as immutable evidence with `PASS/REJECT/NO_EFFECT/INCONCLUSIVE/INVALID_METHOD/CORRECTNESS_FAIL`; GitHub job SUCCESS alone is not scientific success.
- Protect exact labels/timestamps/numeric values, stale NaN, absent labels, repeated/late samples, counter resets, ACL, memory/OOM. Do not infer production superiority from O1 vs release or multiply separate speedups.
- Source/review work can start in parallel. CI compiles must be budgeted and must not preempt other owners.

## 2026-11-03 R06 execution checkpoint: evidence and next gate

**Owner/claim:** `chatgpt-e03-order-aware-20261103` / [R06 canonical root](../../claims/roots/R06.json). This is a source+plan diagnostic, not an adaptive native CBO implementation.

- [Source-level planning audit](E03-R06-order-aware-native-planner-source-audit-20261103.md): selector forces `full_sorting_merge` and `optimize_read_in_order` for its inner ID query; `timeSeriesStoreTags` has query-context side effects. Do not assume SQL stats automatically fix native PromQL.
- [Independent reduction of original 1.33m native 2×2 factorial](../../../ts-lab-r06/HISTORICAL_FACTORIAL_REDUCTION_20261103.md): projection saved ~16.6–16.8% read rows but merge JOIN wall worsened 32.7–36.2%. Stage/project interaction +357/+142 ms; concurrent operator work is NOT wall time. Not optimized-release parity.
- [Actual inner ID SELECT/EXPLAIN capture](../../../ts-lab-r06/NATIVE_ID_PLAN_CAPTURE_20261103.md), [CI 41673047398](https://github.com/moleus/ClickHouse/actions/runs/41673047398): exact source/binary pin, 8192 synthetic IDs, 8/8 output parity; confirmed standalone `FullSortingMergeJoin` vs hash with matched settings. A one-mark projection was considered but not chosen. Preserve first [INVALID_METHOD 41670929674](../../evidence/R06-nested-plan-invalid-method-41670929674.json) and wrongly configured standalone [41672454144](https://github.com/moleus/ClickHouse/actions/runs/41672454144).
- [JOIN cardinality estimates falsified](../../../ts-lab-r06/CARDINALITY_ESTIMATE_AUDIT_20261103.md), [CI 41673788133](https://github.com/moleus/ClickHouse/actions/runs/41673788133), [immutable evidence](../../evidence/R06-native-join-cardinality-41673788133.json): `EXPLAIN` estimated TAGS left 1350 for both stage/project but exact counts are 216/410; TimeRanges right estimate 892 vs actual8192 (underestimate 9.18×). This **invalidates estimate-only hash admission on this fixture**, not all CBO estimates.

**Next experiment:** measure full-query native operators and true cardinality on held-out 1.33m/14d and 400-node/mixed-cadence fixtures. Estimate a conservative right-hand build-row upper bound and sort-work/peak per selected physical plan. Do not enable new hash/projection choice until E06/R08 oracle and 1/4/8-client CPU/RSS/OOM gates. No C++ modification or optimized release result yet.

## 2026-11-03 R30 experimental membership JOIN: SQL stage PASS; native C++ unmeasured

A separate canonical question [R30](../../claims/roots/R30.json) avoids mixing physical join strictness with R06 optimizer estimation or R05 SAMPLES index pruning. [R30 source/semantics proof](../../../ts-lab-r30/ALL_VS_ANY_TIME_RANGES_PROOF_20261103.md): `AggregatingMergeTree` TimeRanges appends an ID/time range per INSERT and merges in background. The source emits `LEFT ALL JOIN` but consumers use the result as an ID membership set. `LEFT ANY JOIN` is a proposed one-site change that suppresses output duplicates without skipping right-side scans.

[Full immutable premerge evidence](../../evidence/R30-any-sql-premerge-41714502297.json), [CI #41714502297](https://github.com/moleus/ClickHouse/actions/runs/41714502297) PASS. With 4096 stable IDs written 16 times before merges, metadata joins return 16x fewer output rows for ANY than ALL; 3 selectors have exact ID-set and full SAMPLES array/hash equality. Direct ID SQL A/B/B/A for project: wall 73.75→50.5ms (-31.5%), CPU 94.259→70.044ms (-25.7%), query tracker 26.72→14.27MB (-46.6%), unchanged rows/bytes read. Physical plan remains FullSortingMergeJoin, though optimizer prints logical-right after join-side rewriting. First [failure run](../../evidence/R30-any-sql-invalid-method-41713474075.json) was INVALID_METHOD due missing standalone SELECT alias.

**Decision:** valid SQL-stage gain on unmerged repeated-ID workload, **not** evidence of native C++ speedup, production-like O2/jemalloc gain or universal improvement. [One-site dry-run C++ patch](../../../ts-lab-r30/apply_any_join_cpp.py) exists but is not compiled/applied. [TimeRanges-only FINAL merge negative control](https://github.com/moleus/ClickHouse/actions/runs/41715251318) **PASS**: 66,560 physical rows/18 parts become 5120/1; JOIN multiplicity ALL:ANY falls 16×→1× on all three selectors, exact IDs and SAMPLES unchanged. Premerge ALL→ANY wall84.25→55.5ms, postmerge wall23→20.5ms (small, not stable-proven); [immutable evidence](../../evidence/R30-any-sql-postmerge-41715251318.json). [1/2/4/8/16 duplicated-row knee study](https://github.com/moleus/ClickHouse/actions/runs/41716983848) retried after an explicit per-table merge-stop fix. First [CI 41716512958](https://github.com/moleus/ClickHouse/actions/runs/41716512958) is [INVALID_METHOD_PARTIAL](../../evidence/R30-duplication-curve-invalid-41716512958.json): valid k1/2/4, then k8 unexpectedly merged. No full-curve conclusion pending corrected CI. E01's [R29 O2 jemalloc stock build](https://github.com/moleus/ClickHouse/actions/runs/41685467438) must not be cancelled or duplicated. Native modified binary must match stock and pass E06/R08 parity plus multi-client RSS/OOM gates before promotion.
