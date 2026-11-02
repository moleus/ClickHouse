# ClickHouse TimeSeries: cost model and proof obligations

**Date:** 2026-11-02. **Source:** pinned upstream `52529f0c711566d696917678bf15e7068772fdde` and lab evidence listed below.

## Experiment policy

Before implementation: (1) identify the exact source path, (2) state a semantic equivalence claim and its assumptions, (3) derive the cost equation and a measurable break-even condition, (4) identify a failure case and test it. During CI: analyze a separate next hypothesis, but do not commit a production optimization on an uncompleted experiment. After CI: record all outputs, absolute/relative time and memory, negative cases, and change acceptance status. Same-run original/candidate/original is mandatory. Compile success alone is not performance evidence.

## Hypothesis A: disjoint grouped `avg_over_time`

Let **S** be selected time series, **E** be evaluation points, **G** output tag groups and **P** physical samples actually selected. The evaluation window at point t is `(t-L, t]`, and evaluation step is Δ. If `Δ >= L`, windows do not overlap, so each sample can contribute to at most one evaluation point. Let `V(s,t)` be valid samples for series s and window t, after the engine's exact duplicate-timestamp and stale-marker rules.

The intended result for tag group g and evaluation point t is

`A(g,t) = SUM_{s in S(g,t)} ( SUM_{v in V(s,t)} v / |V(s,t)| ) / |S(g,t)|`,

where `S(g,t)` includes only series with at least one valid sample in the window. This is *not* equal to a flat average over all samples unless per-series sample counts are equal. This expression proves that a scalar per `(series_id, evaluation-point)` rollup followed by a scalar per `(tag-group, evaluation-point)` average has the same algebraic result **if and only if** the same selected series, sample validity, duplicate policy, window endpoints, and floating-point semantics are preserved. Floating-point aggregation order can change a few final bits, so numerical-equivalence tests remain essential.

Current source builds `timeSeriesAvgToGrid` and grouped `avgForEach`, producing a nullable evaluation grid per series. The candidate reads through the existing `timeSeriesSelector`, does per-ID/window scalar averages, then calls `timeSeriesIdToGroup` and `timeSeriesRemoveAllTagsExcept`. It does not directly read the inner TAGS/SAMPLES tables.

**Work model:** Let `N=|S|`, `E` = output steps per selected series, `Q` = nonempty (series,step) pairs, `C_read` = physical read/selector cost, `C_hash` = scalar hash-group cost, and `H` = fixed extra SQL-plan cost. A simplified model is

`T_native = C_read + c_grid * N*E + c_agg * N*E + C_group`,

`T_scalar = C_read + c_sample * P + c_hash * Q + C_group' + H`,

with `Q <= min(P,N*E)`. Thus scalar wins when

`(c_grid+c_agg)*N*E > c_sample*P + c_hash*Q + (C_group' - C_group) + H`.

For one-sample short-lived series `P ≈ N` and `Q ≈ N`, so the left side grows with the number of evaluation steps E while the right side grows approximately with N. For continuously sampled counters `P >> N`, a second dedup/group hash pass may cost more than the saved grids. This explains why a sparse-only guard is necessary; it does *not* assert that all grouped averages become faster.

**Observed:** On 282,036 Jenkins series, same-run C++ A/B/A run `41420507321` gave native-recheck p50 1977.819 ms, candidate 1579.562 ms, saving 398.257 ms (20.1%). Tracked query memory p95 547,909,091 -> 401,354,131 bytes, saving 146,554,960 bytes (~520 bytes per selected series). Same-run synthetic 210-series run `41420683218` gave native-recheck 16.026 ms and candidate 18.192 ms, losing 2.166 ms. Values/labels/timestamps matched for the tested cases.

A *purely illustrative* linear interpolation from those two unrelated workloads gives marginal saving `(398.257+2.166)/(282036-210) ≈ 0.001421 ms/series`, intercept overhead about 2.46 ms, and crossover near 1.7k series. **This is not a proven threshold**: the two tests had different data shapes, runner conditions, grouping, and query result cardinality. In particular, the proposed 32,768-row gate is a conservative hypothesis, not an outcome of that interpolation.

**Critical counterexample:** `StorageTimeSeries::Samples.totalRows()` measures the *whole inner SAMPLES table*, not the selected metric's matching series. A table with 1M rows from another metric plus 210 matching series passes a 32,768-row gate and may regress. No global speed guarantee follows from a total-table-row guard. Before production, test mixed metrics and estimate selected metric cardinality without an unbounded probe. If that cannot be done cheaply, keep the guarded path experimental or use runtime/adaptive dispatch.

**Correctness not yet proven:** duplicate timestamp tie-breaking between different physical parts, stale NaN bit patterns, invalid/Inf values, late inserts, replacement merges, non-UTC time zones, inclusive/exclusive boundaries for `t0-L`, step aliases, overlapping windows, access rights on outer vs selector, arbitrary groups, and preservation of distributed query semantics.

## Hypothesis B: label postings for selective selectors

TimeSeries v11 TAGS already has a `tags_idx` text index. Its postings contain local part row positions; TAGS rows map them to real series IDs. Index evaluation may cost `C_idx ≈ Σ|postings_i| + intersection_cost + |matching IDs|*sample_seek`. A scan costs `C_scan ≈ |TAGS rows examined|*per_row_predicate_cost`. An index saves time only if `C_idx < C_scan` and the downstream sample seeks do not dominate. For a broad selector, posting materialization can be slower than scanning. The physical design must handle parts, merges, replacements, deletes, late data and label changes. Test selective AND/OR/regex, negative matches and mixed tenants separately.

## Hypothesis C: regular `sum(rate())`

`sum(rate(series))` must compute reset corrections and extrapolation **per series** before grouping; it cannot turn into a single `rate(sum(samples))`. Current dense source already parallelizes four streams. A useful fused implementation must reduce `O(N*E)` nullable per-series grid emission without increasing the cost of reading P samples, while retaining per-series reset and extrapolation state. Thus compare the lower bound `T_read+T_per_series_state` with observed end-to-end time and profile exclusive CPU frames. Inclusive adaptive-merge stacks do not prove that removing adaptive aggregation helps. Run `41418262715` showed disabling adaptive aggregation sped dense grouped rate only ~3–6% and slowed broad Jenkins ~18%.

## Rejected hypotheses and evidence

- Inline 8-entry sliding rate window: run `41417443992`; exact outputs but dense rate +5% and grouped rate +5–6%; reject.
- Metric-ID primary-key range hint on bucketed samples: run `41419049171`; narrow selector regressions 53–110%; reject. Existing non-bucketed behavior unchanged.
- Blanket disable of adaptive GROUP BY: run `41418262715`; small dense gain but broad Jenkins regression; reject as global default.

## Gate design and next tests

1. Inspect the v2 `NodeType` compile failure before rerunning; this is build correctness, not a tested speedup.
2. First verify metric-specific sparsity/selected-series estimation. Do not infer it from total SAMPLES rows. Test small metric in a large mixed-metric table.
3. Measure original/SQL-scalar/original at several series counts on identical hardware, retaining physical sample density, E, label-group count, and filters. Fit the cost coefficients and a confidence interval for crossover rather than setting 32,768 by intuition.
4. If a guard passes cost and correctness tests, evaluate C++ version with 10+ correctness cases, 14–90 days, late inserts, `-O2/-O3` and concurrency. Keep fallback for unsupported shapes.
5. Check access policy and query context equality for outer PromQL vs `timeSeriesSelector`; SQL-only structural similarity does not prove authorization equivalence.

## Source and links

- `.github/ts-lab-bench/ENGINE_GOAL_AND_ROADMAP_20261102.md`
- `.github/ts-lab-bench/SELECTOR_SCALAR_GROUPED_AVG_EXECUTION_20261102.md`
- `src/Storages/TimeSeries/PrometheusQueryToSQL/applyFunctionOverRange.cpp` and `applyOneArgumentAggregationOperator.cpp`
- `src/Storages/StorageTimeSeriesSelector.cpp` and `src/Interpreters/AdaptiveAggregation/Merge.cpp`
- `https://github.com/moleus/ClickHouse/actions/runs/41420507321`
- `https://github.com/moleus/ClickHouse/actions/runs/41420683218`

## While v2 builds: independent analysis of the next two ideas

**Label selection cannot by itself solve the full query gap.** In the dedicated column / sort-order experiment (run `41351343306`), the project-filtered plan read 564,072 rows in 189.145 ms, whereas the alternative read 306,612 rows in 158.299 ms. A two-point affine *illustration*, `T ≈ a + c*rows`, gives `c ≈ 30.846ms/257460 ≈ 0.120 microseconds/row` and `a ≈ 121.6ms`. Even eliminating this proportional component would predict about 121.6ms, not VictoriaMetrics' ~10.5ms on the same project-filter workload. These are two different physical layouts, so the affine fit is not a causal bound; it nevertheless **falsifies the assumption that removing TAGS row scans alone will close the gap**. Measure actual exclusive TAGS-vs-SAMPLES CPU cost and random reads before implementing another index. Broad reads also regressed in the sort-order experiment (850.7ms -> 890.1ms).

**Counter rate must be per series.** For two counters with a reset, A(t0)=100, A(t1)=1 and B(t0)=0, B(t1)=100, their per-series reset-corrected increments total 1+100=101 over the interval. The summed raw counter goes from 100 to 101, with increment 1. Therefore `rate(A+B) != rate(A)+rate(B)` by a factor of 101 in this case. A group-rate fusion may eliminate intermediate nullable grids, but must retain *independent per-ID reset and extrapolation state* before summation. A plan that sums samples first is invalid regardless of benchmark speed.

**Next falsification test started:** v2 original/candidate/original on a mixed-metric TimeSeries table. A 210-series `lab_small_metric` is inserted into `jenkins_ts`, which contains 282,036 series of another metric. The candidate's `totalRows>=32768` gate will activate even when selected metric cardinality is 210. The test is designed to refute a global size gate and determine its regression cost; not to validate it as production-ready. Fixed C++ build run `41431984680`, A/B/A run `41432240947`. The v2 namespace compile correction does not change its algorithm or output semantics.
