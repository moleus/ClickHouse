# Disjoint one-day PromQL fast path: mathematical equivalence scope and cost bound

2026-11-02. Source pinned upstream `52529f0c711566d696917678bf15e7068772fdde`. This proof describes the guarded **v4** path in `.github/ts-lab-pending/apply_selector_native_one_point_v4_filtered_cpp.py`; it does NOT prove general PromQL equivalence.

## A. Time partition theorem (assuming guarded parameters)

Let `W = 86,400,000 ms`, evaluation time `T_j = T_0 + j W` for integer `j∈[0,E−1]` and `E≥2`. The original `avg_over_time(metric[1d])` includes a sample of integer millisecond timestamp `t` in evaluation j iff:

`T_j − W < t ≤ T_j`.

The original selector's global time range is `T_0 − W + 1 ≤ t ≤ T_{E−1}`. For every timestamp in this range, the unique eligible index is

`j = ceil((t−T_0)/W) = floor((t−T_0+W−1)/W)`.

The range lower bound ensures `t−T_0+W−1≥0`, so ClickHouse `intDiv` has no negative-division corner case for eligible input. The guard requires the query window and step both equal W and uses DateTime64(3), so no overlaps or submillisecond rounding exist. Because `0 ≤ j < E`, `T_j−W<t≤T_j`, and all other windows exclude t, the partition is unique. Conversely every sample eligible in the original query belongs to exactly one j.

V4 computes this j in SQL and then maps t to `t'=t−T_j`. It follows that `−W<t'≤0`, exactly the one-point `timeSeriesAvgToGrid(0,0,W,W)` window. Under this translation, equal timestamps remain equal and sample ordering can still be handled by the native aggregate. Per-ID/per-window sample sets are therefore mathematically identical to the original per-ID `timeSeriesAvgToGrid` subsets.

Sources: original range bounds `src/Storages/TimeSeries/PrometheusQueryToSQL/fromSelector.cpp` and native aggregate implementation `src/AggregateFunctions/TimeSeries/AggregateFunctionTimeseriesBase.h`.

## B. Output aggregation theorem and its important limit

The converter lowers `avg by(stage)(avg_over_time(...[1d]))` to stage-wise `avgForEach` across per-ID grids. For finite exact-real arithmetic, taking the mean across nonempty per-ID results at each j equals computing a regular `avg(series_avg)` after (ID,j) grouping. A null per-ID step contributes no value to either mean. V4 uses the same TimeSeries ID→tag grouping functions and the full parsed label selector string.

**IEEE-754 floating-point addition is not associative.** The two evaluation plans may accumulate cross-ID stage means in different orders, so mathematical equivalence **does not imply bitwise numerical equivalence** on arbitrary groups. Original `avgForEach` was itself unstable on extreme cross-ID cancellation (run 41439318904). This is a separate semantic fidelity problem, not fixed by v4. A dedicated `avgKBNLab` improves finite adversarial cases but is a separate opt-in experiment, not wired to general PromQL. It still overflows for `avg(1e308,1e308)`.

Verified empirical scope:
- v4 compiled with opt-in default false (41447045583);
- exact labels/timestamps and finite outputs for 20 filtered selectors, 3 and 40 steps (41447138654);
- 14/40/90d continuously added build IDs (41447201613);
- explicit 1ms open-left/closed-right windows, duplicate timestamps and stale markers (41452375304);
- full output IEEE bits (41452959910), including 2 ordinary NaNs, ±Inf, +0 and 6 nonzero finite values, zero bit mismatches;
- native `avgKBNLab` State/Merge cross-ID finite cancellation test (41446233352) and full SQL stage test (41447468849).

No proof yet for nonstandard time scale, overlapping windows, offsets, @ modifiers, subqueries, cross-regional/distributed state merge, changing permissions, custom timezones, NativeHistogram or very large numeric overflow. Most are excluded by v4's strict AST/schema guard; security and concurrency still need adversarial testing.

## C. Cost theorem explains need for density-aware dispatch

Let `S` = selected unique IDs, `E` = daily grid steps, `P` = nonempty (ID,j) pairs, and `M` = raw samples. Always `S ≤ P ≤ S⋅E` when every selected ID has at least one eligible sample.

The original native aggregation allocates one per-ID state and its grid finalization visits E grid points per ID (see `AggregateFunctionTimeseriesBase::doInsertResultInto`), even when most are empty. A model is:

`T_original = T_read(M) + a⋅S⋅E + b⋅S + fixed_original`.

V4 creates one native one-point aggregation state per nonempty (ID,j), plus grouping, and does not materialize all absent per-ID grid values:

`T_v4 = T_read(M) + c⋅P + d⋅M + fixed_v4`.

This is a **model**, not a measured decomposition. Coefficients vary with parallelism, physical parts, query filters and allocator. V4 is preferable only if

`a⋅S⋅E + b⋅S > c⋅P + d⋅M + fixed_v4−fixed_original`.

Define density `ρ=P/(S⋅E)`, `1/E ≤ ρ ≤ 1`. A single-sample-per-ID build metric has `ρ≈1/E`, often a large win. A long-lived hourly gauge has `ρ≈1`, where V4 may lose due to many extra (ID,j) hash states and sample expansion.

**Counterexample to count-only or step-only gating:** Two different workload shapes can have the same S and E but P=S vs P=S⋅E. A gate based only on S or E gives the same decision for both even though their relative costs differ. Therefore a generally safe dispatcher needs density information, an execution-stage decision after reading IDs/samples, or a strict metric-specific allowlist. Query-time `count()` extra scans already cost ~2ms client p50 on small fixtures (41437764684), and cannot represent the true active pair count for regex filters and time windows.

The deliberately adversarial compiled dense-vs-sparse A/B/C/A run `41453548480` is the experimental test for this prediction. No assertion about its result until logs are inspected.

## D. Deployment decision

The v4 implementation has a **default-off experimental** setting. Do not enable automatically or merge into production. Preserve a manual allowlist limited to the measured short-lived metrics until missing security/physical-layout/production-load and dense-workload tests pass.
