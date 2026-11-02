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
