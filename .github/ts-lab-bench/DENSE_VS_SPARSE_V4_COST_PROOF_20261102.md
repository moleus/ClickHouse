# Counterexample to a universal v4 avg_over_time fast-path: dense long-lived series

2026-11-02. **Proof before execution.** Pinned upstream source `52529f0c711566d696917678bf15e7068772fdde`, compiled lab v4 binary run `41447045583`. No prod/master changes.

## Structural cost model

Let `N` be **selected unique series IDs**, `E` be daily result steps, and `K` be samples per ID within each step. The original converter groups by ID and calls `timeSeriesAvgToGrid` for all E output times, then performs stage `avgForEach` over E-sized arrays. A simplified lower bound for its materialized per-ID grid work is `N * E`.

The v4 converter expands samples via `arrayJoin`, calculates `gi=ceil((timestamp-start)/step)`, groups by `(series_id,gi)`, runs one-point `timeSeriesAvgToGrid` per nonempty pair, then groups by stage and gi. Let `P` be the number of **nonempty (ID,gi)** pairs. Its per-ID state and grouping overhead is approximately `P`, not `N`.

The counterexample:

- **Short-lived CI build series**: each ID contributes one sample in one day. If N=40,000 IDs across E=40 days, then P=40,000. Original emits `N*E=1.6 million` array entries; v4 only P=40,000 one-point states. Large potential benefit, verified direction by 41447201613.
- **Long-lived dense gauge series**: each of N=1,000 IDs produces K=24 samples/day for E=40 days, so P=N*E=40,000, and raw samples M=N*E*K=960,000. Original needs N=1,000 aggregate states and 40,000 grid positions; v4 needs 40,000 `(series_id,gi)` aggregate states, 40x more aggregation keys than original, without saving any grid slots. The exact ratio depends on hash table overhead, window implementation, memory allocator and physical parts; cost sign is **not** proved by asymptotic bounds alone.

Both workloads match v4's AST guard: `avg by(stage)(avg_over_time(metric[1d]))` and 1d query step with exact metric name. **Therefore v4's strict semantic AST guard does not itself guarantee beneficial performance.**

## Experiment

Create one TimeSeries v11 Float64 table using public outer INSERT:
- `tslab_dense_40d`: 1,000 fixed IDs, 40d active, 24 hourly samples/day per ID, 38 stages, project labels, ~960k samples and ~1000 selected unique IDs.
- `tslab_sparse_40d`: 1,000 new IDs per day, one sample per ID, 40d active, same 38 stages, project labels, ~40k IDs/samples.
- Same time limits; evaluate E=3,14,40 for both metrics, and a selective project filter. Values finite and ordinary to make numerical equality unambiguous.

Phases: pinned original / v4 C++ setting on / v4 setting off / original. Each phase 2 warmups+5 measured repeats, same physical data, exact output keys/timestamps/Float64. Record server query-log read_rows, duration and peak tracked memory. Keep physical data unchanged throughout. Store raw JSON and fixture checksum.

## Decision

If v4 regresses on dense long-lived gauges, **do not enable v4 unconditionally** even for large E. A safe planner needs selected `N`, active pair density `P/(N*E)`, and cost of grouping + grid materialization. A count-only gate is insufficient. For production use, either explicitly scope the feature to known short-lived one-sample-per-build metrics or develop an execution-stage adaptive mechanism that uses already selected IDs/pairs without a second expensive query.

A small runner with 1,000 IDs cannot establish full-scale performance. No upstream writes, automatic schedules or cancellations.
