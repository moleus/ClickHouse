# Proof-first: translated one-point native `timeSeriesAvgToGrid` lowering

Date 2026-11-02. This is a **SQL-only hypothesis**. It is not an engine patch or production recommendation.

## Motivation and source

The v1 scalar-grouped average rewrite is proven wrong on cancellation-heavy multiple-sample series in run 41436423717: it substitutes SQL `avg(value)` for `timeSeriesAvgToGrid` with Kahan-Babuska-Neumaier compensation. The candidate cannot be promoted by adding a cardinality guard.

Pinned native source: `src/AggregateFunctions/TimeSeries/AggregateFunctionTimeseriesCompensatedSum.h`, `AggregateFunctionTimeseriesBase.h`, `src/Storages/TimeSeries/PrometheusQueryToSQL/getToGridAggregateFunctionArguments.cpp` and `applyFunctionOverRange.cpp`.

A possible correct lowering keeps the existing native `timeSeriesAvgToGrid` aggregate, but creates a **single-point local grid** per `(series_id, evaluation_index)`, avoiding an entire `E`-element nullable grid per selected series.

## Translation proof obligation

For step `i` with evaluation timestamp `T_i = start + i * step`, a PromQL window is `(T_i-L,T_i]`. Map each sample timestamp `t` to `t' = t - T_i`. Then `t ∈ (T_i-L,T_i] ⇔ t' ∈ (-L,0]`. Translation is bijective, preserves timestamp equality and order, does not modify sample values and preserves each series ID.

Feed the translated timestamps and original values to the **same native** aggregate `timeSeriesAvgToGrid` with `grid_start=grid_end=1970-01-01 00:00:00.000`, `step=L=86400 seconds`, and use its single array cell. The original window and transformed window should contain identical per-series values in identical timestamp order. The base implementation calculates bucket indices relative to its start; shifting the grid and samples together should preserve bucketing. This exact bucket-invariance must still be checked against source and runtime, particularly near negative epoch and when native aggregates merge physical buckets in different orders.

After per-series native arithmetic, calculate the unchanged stage-level SQL `avg` of per-series results; SQL stage averaging is NOT applied directly to raw samples.

If the translated aggregate syntax is unsupported or the results differ, reject; do not silently substitute `sumKahan` or `avg`.

## Cost equation

Let selected series count `N`, output grid steps `E`, non-empty series/step pairs `Q`, selected raw samples `P`, and physical read cost `C_read`.

Native `T_0≈C_read+c_grid*N*E+C_group`.
Candidate `T_1≈C_read'+c_dedup*P+c_native_one_point*Q+C_group'+H`.

In sparse one-sample metrics `Q≈P≈N`, so removing most per-series empty grid cells *may* save work when `E` is large. In dense metrics, `P≫N` and extra dedup/grouping may lose. Using the native aggregator increases per-pair work relative to the semantically invalid v1 `avg`; a performance benefit is NOT assumed.

## Falsification and acceptance

1. First run SQL-only native vs translated one-point SQL vs native on the four numerical fixtures from 41436423717 (including cancellation and an ordinary control). Exact labels and timestamps and values within `1e-10 relative / 1e-8 absolute`. Compare diagnostics and retain raw errors.
2. If correct, test sparse selected N=210,4200,16800,67200 and 3/14/40 evaluation steps against unchanged ClickHouse TimeSeries and VictoriaMetrics where semantics are aligned. Include high-density and multiple samples per series; test 14–90d, NaN, stale markers, duplicate timestamp, late arrivals and access policy.
3. Reject if it loses correctness, if there is no reliable measured speed gain, if query memory is unbounded, or if the selector's id->tags collector semantics differ.
4. Existing originals and binaries remain unchanged. No new C++ compile. Fork lab-only workflow with `cancel-in-progress: false` and SHA-verified baseline binary.

## First numerical A/B/A completed, protocol for scaled cost test (before implementation)

Run **41437063380** SUCCESS. Original/native-one-point/original queries ran using exactly the same pinned baseline binary and four adversarial stage samples; the candidate reproduced all original values exactly, including `1/3`, `2`, `5e-7` from cancellation cases. Original recheck matched as well. Small p50 original before 19.648ms, one-point 22.269ms, original after 19.145ms. Correct on these inputs, slower at N=4. This is NOT yet a general semantic proof.

Next controlled A/B/A on existing synthetic mixed metric fixture: true selected series N=4200 or 67200, same one-sample values across 3 days, vary output range `E=3,14,40` while holding the physical set of selected samples fixed (extend only query start back in time). Predictions: native per-series grid allocation grows with E; translated candidate emits only Q nonempty (series,window) values, so candidate relative gain may grow with E. Use 2 warmups+5 measured repetitions per case, original SQL/native-one-point SQL/original SQL on identical unmodified pinned binary and data. Capture read_rows/read_bytes/memory, labels/timestamps/values, before/after drift. No extra C++ build.

**Reject** if numerical mismatches, significant regressions at E=40, or memory increases without bounded benefit. Even a successful sparse result does not permit production use without mixed dense, NaN, duplicates, time-zone/access, -O2 and concurrency tests. Existing v1/v2 remain rejected for general multi-sample arithmetic.

## Independent hypothesis before implementation: remove redundant SQL dedup pass

The first native one-point SQL candidate makes `dedup AS (GROUP BY series_id,sample_ms; argMax(...))`, then calls native `timeSeriesAvgToGrid` per `(series_id,gi)`. But `src/AggregateFunctions/TimeSeries/AggregateFunctionTimeseriesSamples.h` already keeps unique timestamps and uses `timeseriesMaxValueForDuplicateTimestamp` for duplicates in add, merge, and sort. A local one-point native aggregate should therefore resolve duplicates **itself**, if all equal timestamps for one series fall into the same `gi` and the dedicated stale marker is filtered before aggregating. Both hold for a disjoint 1d grid and translation `t' = t-T_i` (equal t implies equal i and t').

Predicted saving: one hash grouping over `P` raw samples, one `argMax` state per unique `(id,timestamp)`, and its memory. Extra native work remains for actual duplicates; no universal speedup is asserted. Disproof: conflicting duplicate values across physically distinct parts, equal-timestamp NaN/real pairs, stale marker mixed with valid values, out-of-order samples, invalid sign/zero handling. Compare three plans on identical data: original native SQL; translated one-point with explicit dedup; translated one-point without SQL dedup. Test numerical cancellation and duplicate timestamp fixtures first. Then A/B/A N/E cost if correctness passes. No C++ source change.

## Source-dedup falsification completed; scaled comparison declared

Run `41437945766` SUCCESS. Original/full native, native-one-point with explicit SQL `argMax` dedup, native-one-point without SQL dedup, and original again returned exactly the same six label/timestamp/value outputs. The input contained three cancellation cases, an ordinary series, and two conflicting duplicate-timestamp series inserted with the same series tags in separate writes: native duplicate values resulted in 55 and -7.5. Candidate without SQL dedup matched both. Small p50 ms: original recheck 25.296; with SQL dedup 27.964; native-dedup-only 26.235. The candidate remains slower than original for N=6.

Next performance experiment (declared before implementation): same unchanged O1 pinned baseline, same mixed 282036-row Jenkins table plus metric N=4200 and N=67200; E=3,14,40, same physical input. Compare original -> with-SQL-dedup -> without-SQL-dedup -> original for each N/E, 2 warmups +5 repetitions per variant. Record query_log server duration, memory, read rows/bytes, and exact outputs. Predict no-SQL-dedup improves CPU and memory on high P, but source-level proof alone does not quantify it. If the numeric equivalence fails on larger data, reject this variant. The dedicated duplicate test does not cover stale NaN payloads, all inf cases, or physical merges under concurrent writes.
