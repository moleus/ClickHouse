# E03 / R06 — Native TimeSeries order-aware planner: source audit and decision gate

Date: 2026-11-03. Status: **SOURCE_PROOF / NO_PERFORMANCE_CLAIM**. Owner token: `chatgpt-e03-order-aware-20261103`. Claim: [R06](../../claims/R06-native-order-aware-plan-cost-v1.json). Canonical root: [R06.json](../../claims/roots/R06.json).

## 1. Exact baseline and proven constraints

Pinned ClickHouse source: `52529f0c711566d696917678bf15e7068772fdde` (v11 TimeSeries). Historical A/B/C/D native factorial: [run 41589798782](https://github.com/moleus/ClickHouse/actions/runs/41589798782); underlying 1.33m-series, 14-day Jenkins fixture SHA256 `8f3a36846f80d6be5a8604d142e934399f2687094766c57bf105e43977f52858`. Historical binary uses unusual O1 lab build, **not release parity**. No new CI run or source patch is claimed in this document.

1. [`StorageTimeSeriesSelector.cpp:1167-1174`](https://github.com/moleus/ClickHouse/blob/52529f0c711566d696917678bf15e7068772fdde/src/Storages/StorageTimeSeriesSelector.cpp#L1167-L1174) overrides **the nested selector context**, setting `join_algorithm=full_sorting_merge` and `optimize_read_in_order=true`. Generic statistics/CBO cannot freely replace this path unless source changes or an existing laboratory override is explicitly exercised.
2. [`:486-572`](https://github.com/moleus/ClickHouse/blob/52529f0c711566d696917678bf15e7068772fdde/src/Storages/StorageTimeSeriesSelector.cpp#L486-L572) creates a `LEFT ALL JOIN` to TimeRanges with a filtered right subquery, then filters `matched=1` (with an ID-range exception when enabled). Time filtering is not a purely optional SQL optimization: skipping it can increase downstream ID set and sample work.
3. [`:618-672`](https://github.com/moleus/ClickHouse/blob/52529f0c711566d696917678bf15e7068772fdde/src/Storages/StorageTimeSeriesSelector.cpp#L618-L672) creates `timeSeriesStoreTags(id, tags, ...)` in the TAGS projection. [Function implementation](https://github.com/moleus/ClickHouse/blob/52529f0c711566d696917678bf15e7068772fdde/src/Functions/TimeSeries/timeSeriesStoreTags.cpp#L29-L45) is **stateful**; execution records ID-to-tags in per-query collector. A rewrite must preserve registration for all emitted series. SQL row equivalence alone is insufficient.
4. [`:1274-1353`](https://github.com/moleus/ClickHouse/blob/52529f0c711566d696917678bf15e7068772fdde/src/Storages/StorageTimeSeriesSelector.cpp#L1274-L1353) selects RecentSamples conditionally, applies internal read thresholds, emits TAGS selector, and builds SAMPLES filter. In v11 bucketed layout the SAMPLES primary-key index uses an ID set; the whole-metric range probe is only for unbucketed rows. **Do not count an unbucketed fastpath as already active for v11.**
5. [`:1009-1157`](https://github.com/moleus/ClickHouse/blob/52529f0c711566d696917678bf15e7068772fdde/src/Storages/StorageTimeSeriesSelector.cpp#L1009-L1157) retains exact `id IN <set>` filtering even when a whole-metric range serves primary-key index analysis. Correctness depends on exact filtering after the advisory range.
6. [`:319-393`](https://github.com/moleus/ClickHouse/blob/52529f0c711566d696917678bf15e7068772fdde/src/Storages/StorageTimeSeriesSelector.cpp#L319-L393) translates EQ/NE/RE/NRE; finite literal regex alternations can become IN/NOT IN, other regexes are anchored. Any selectivity estimator must support negative/regex/absent-label rules, not just equality NDV.

**Decision map** (meaning: exposed to the already-generated nested SQL optimizer, not proof it affects native execution):

| Decision | Fixed by selector source | Potential generic CBO input | Missing evidence / safe next action |
|---|---|---|---|
| Choice of JOIN algorithm for TimeRanges | **YES**: forced merge | Statistics can affect other SQL JOINs; no evidence they override forced native choice | Lab-only override, native operator verification |
| In-order read | **YES**: enabled in selector context | Projection optimizer can still choose a different read path | Cost of lost downstream ID ordering must be explicit |
| TAGS matcher filters | Generated AST predicates, per-tag promoted column if configured | Column statistics may estimate promoted columns | Correlated labels, dynamic Map, negative/regex estimate need measured accuracy |
| TimeRanges join or skip | Join generated when table exists and range selective | No automatic native skip path shown | Keep as conservative default; only consider opt-in semantic gate |
| SAMPLES access by ID set | **YES** for bucketed v11 | PK/parts/mark selection, independent of CBO join statistics | Actual selected marks and decoded bytes belong to R05; consume its outputs |
| `timeSeriesStoreTags` | Side-effectful in per-query context | Ordinary optimizer knows a function call, not the full downstream collector semantics | Validate complete output labels and group values under any rewrite |
| RecentSamples vs SAMPLES | TTL and setting gate in selector | No cost-based choice shown | R04 owns recent TTL experiment |
| Physical data layout | Table DDL, merges and buckets | Projection/parts/mark statistics may inform reads | R07 owns new metadata layout; E04 owns index parity |

## 2. Source and historical native evidence: physical order dominates naive row savings

[Native projection × JOIN factorial 41589798782](https://github.com/moleus/ClickHouse/actions/runs/41589798782), recorded in [CBO research checkpoint](../../../../ts-lab-cbo/RESEARCH_CHECKPOINT_20261103.md) and [INDEX](../../INDEX.md):

| Selector, 7d | OFF + merge | AUTO + merge | OFF + hash | AUTO + hash |
|---|---:|---:|---:|---:|
| Stage wall p50, ms | 818.25 | 1114.50 | 1608.50 | 1547.50 |
| Project wall p50, ms | 541.50 | 718.75 | 661.75 | 697.00 |

*Historical paired phase-median summary, not new observations. 2×2 factorial, same binary and fixture, exact native output checks; all four variant drift gates <30% per run's method. Do not extrapolate the O1 magnitude to optimized release.*

Stage AUTO reduces read rows 3,945,712 → 3,282,160 (16.8% lower) but AUTO+merge **increases** wall time 818.25 → 1114.5 ms (36.2% higher). Project AUTO reads 3,990,000 → 3,326,448 (16.6% lower) but AUTO+merge wall rises 541.5 → 718.75 ms (32.7% higher). Representative native processor profile for project adds MergeSorting 267ms, MergingSorted 124ms, PartialSorting 99ms of processor work; do not sum them as query wall time. Stage representative AUTO+merge adds MergeSorting 507ms, MergingSorted 319ms and PartialSorting 126ms. That is direct evidence of a **sort/order loss** on the tested shape, not evidence to disable all projections.

[TimeRanges ON/OFF native A/B/B/A 41601352971](https://github.com/moleus/ClickHouse/actions/runs/41601352971), with [full model](../../TIMERANGES_ADAPTIVE_COST_MODEL.md): stage14d OFF 518.516 vs ON 539.525 ms (4% faster), project1d OFF 927.906 vs ON 477.828 ms (94% slower), whole3d OFF 3205.346 vs ON 2041.261 ms (57% slower); broad whole7d hit OOM. **Any global skip is rejected.** ON/OFF was a schema/ingest setting, so it does NOT establish performance of a future per-query skip on identical physical layout.

## 3. Required cost terms and hard memory gate

Model **per physical candidate**, not as a single `estimated_read_rows`:

```text
C_plan =
  C_tags_scan + C_label_predicate
  + C_ranges_scan + C_ranges_filter
  + C_join_build_or_stream + C_sort_if_order_lost
  + C_collector(N_selected_ids, label_bytes)
  + C_id_set + C_sample_marks + C_sample_decode
  + C_bucket_or_grid + C_group + C_result_encode

C_sort_if_order_lost >= 0
C_hash_build >= 0
peak_live ≈ max(peak_join+peak_sort+peak_id_set,
                peak_sample_decode+peak_group) + concurrent overhead
```

If a projection cuts read work by `ΔC_read` but changes the input to the ID-ordered merge join, it is profitable **only if**:

```text
ΔC_read > ΔC_sort + ΔC_join + ΔC_materialization + ΔC_other
```

This is a necessary, not sufficient, performance condition; per-stage CPU sums cannot be substituted for end-to-end wall latency because operations overlap. Bound separately the critical path and total CPU.

A hash JOIN should not be selected solely because build cardinality is estimated smaller. Define `N_right_upper` as an upper bound on the number of **build rows**, `B_entry_upper` as a conservative actual per-row hash/build overhead, and `M_current` as tracked global resident headroom after existing live queries:

```text
M_hash_upper = N_right_upper * B_entry_upper
               + M_key_columns_upper + M_probe_buffers_upper
               + M_idset_upper + M_collector_upper
allow_hash only if M_hash_upper + M_other_peak_upper
                < min(query_memory_headroom, server_RSS_headroom / safety_factor)
```

The factor and row-overhead upper bounds must be measured and calibrated; neither 1.33m logical IDs nor total TAGS rows is an automatic upper bound on the build cardinality if right-side TimeRanges contains repeated rows. If stats are missing, stale, or correlate badly, **fallback is the source's ordered merge**. No unbounded hash reserve.

Statistics needed in addition to conventional NDV: actual selected IDs by matcher; active IDs within time window; per-time/label correlation; right-hand TimeRanges rows *after* filter; physical read order from selected projection; TAGS/SAMPLES marks and compressed bytes per part; samples per selected ID and bucket width; intermediate collector memory; concurrent request count and resident memory budget. Distinguish stale metadata IDs from IDs with samples in the requested window.

### Strong falsifiers for the first intervention

1. The stock ordered in-order plan is already cheapest for both CPU and latency on held-out workloads: do not patch.
2. Any alternative needs a full 14-day right-hand hash or sorting spill/peak that violates memory headroom: reject it regardless of selective p50.
3. Projection `read_rows` falls while its total read+sort+join CPU rises: estimator lacking order cost is invalid; add physical-order state.
4. Exact PromQL differs on missing labels, `!~`, stale values, late samples, duplicated IDs or timestamp boundaries: **CORRECTNESS_FAIL**, not a benchmark gain.
5. Estimates consistently underpredict multi-client peak RSS or CPU on held-out data: **INCONCLUSIVE/REJECT** for adaptive automatic selection.

## 4. Candidate evaluation order: zero unproven C++ changes

**Phase A — explain-only and instrumentation.** Use an existing verified same-source executable. Record the generated internal selector SQL (source already emits a debug log: `Building SQL for selector`), physically resolved nested `EXPLAIN AST/PLAN/PIPELINE`, order properties before/after selected projection, plan/processor stage names, `QueryFinish` and selected marks. Top-level EXPLAIN alone is not enough. Freeze source/binary/fixture SHA before analysis.

**Phase B — lowest-risk order-aware policy.** Compare `OFF+merge`, `AUTO+merge`, `OFF+hash`, `AUTO+hash` on a **new held-out** high-churn and dense-stable fixture, with exact selector and source parity. First target: identify cases in which preserving ID order prevents expensive sort, without losing a large enough pruning gain. The source default remains unchanged.

**Phase C — selective TimeRanges.** In a single source/DDL-matched table, implement an opt-in laboratory **per-query** on/off gate only if E06 oracle verifies equivalence and time-window memory guard. Historical schema-on/schema-off numbers cannot be reused as effect sizes for this implementation. R05 may supply mark bounds; R07 may supply metadata age distribution; neither is owned by R06.

**Phase D — calibration + blind validation.** Fit explanatory coefficients to training cases only, then freeze thresholds and test independent selector × window × 1/4/8 clients cells. Report selectivity predicted/actual, estimated vs measured CPU, p50/p95/p99, peak memory and wrong-plan fraction. Do not mix phases on reused mutable parts if writes/merges differ.

## 5. Minimal evidence schema for the first future E03 A/B test

Every row: `source_sha, binary_sha256, cmake_defines, fixture_sha256, schema_ddl_sha256, query_text, selector_class, window_days, client_count, native_plan_variant, physical_order, profile_path, time_ranges_rows, tags_candidate_ids, sample_selected_marks, sample_read_bytes, phase_id, phase_position, server_rss_before, server_rss_peak, query_peak, cpu_ms, query_wall_ms, errors, output_hash, oracle_result, a_a_drift, decision`. Put full raw logs/JSON in immutable run artifacts.

**Decision now:** original ordered-merge plan stays the safe baseline. Proceed to a read-only physical-order EXPLAIN capture and held-out estimator, *not* a new build. No CI runs launched by this audit. This document is not a validated adaptive planner implementation.
