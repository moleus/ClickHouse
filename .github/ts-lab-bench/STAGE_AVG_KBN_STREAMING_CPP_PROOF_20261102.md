# Streaming stage mean with Neumaier/KBN state: lab aggregate v1

2026-11-02. Written before C++ patch and benchmark.

## Current evidence

Experiment 41444399417 applied existing native `timeSeriesAvgToGrid` to synthetic unique timestamps of per-series values in one stage. Seven adversarial cross-ID cancellation groups matched `math.fsum(values)/len(values)` in every one of 15 repetitions. Original `avgForEach` was inaccurate and unstable. Built-in `sumKahan/count` was wrong in 41440060987. Per-ID `timeSeriesAvgToGrid` was already proven correct on cancellation and duplicate samples (41439148997).

## Hypothesis

Implement a lab-only ClickHouse streaming aggregate `avgKBNLab(Float64)` that reproduces the numerical state in `src/AggregateFunctions/TimeSeries/AggregateFunctionTimeseriesCompensatedSum.h:39-73`, *without* the synthetic timestamps or `groupArray`. State is `Float64 sum`, `Float64 compensation`, `UInt64 count`, at most 24 bytes plus ordinary grouping hash state.

On add(value):
`t=sum+value`;
`if (!isfinite(t)) compensation=0`;
`else if (abs(sum)>=abs(value)) compensation+=(sum-t)+value`;
`else compensation+=(value-t)+sum`;
`sum=t;count++`.
On merge(rhs): call `addCompensated(rhs.sum)`; add rhs compensation if resulting sum finite; `count+=rhs.count`. Do not call the normal add method for rhs.sum, because that would increment the count by 1.
Result is `(sum+compensation)/count` for nonempty state and NaN for empty; serializable for parallel partial aggregation.

**Source fidelity:** This copies the existing TimeSeries KBN Summary arithmetic, not ClickHouse `sumKahan`'s distinct merge formula. It does not prove bitwise identity with Prometheus grouped AVG in all cases; Prometheus uses a near-overflow incremental mean fallback. Null inputs are handled using ClickHouse's aggregate combinator adaptation and must be tested.

## Build

- Add a single lab-only file `src/AggregateFunctions/AggregateFunctionAvgKBNLab.cpp`, register it in `registerAggregateFunctions.cpp`. File creation is applied to exact upstream pinned source `52529f0c...` in GitHub Actions, never fork master or upstream.
- Limit input type to Float64, reject other types. Disable production use; `avgKBNLab` is a dedicated experiment name, not replacement for `avg`.
- Use existing ClickHouse O1 baseline toolchain LLVM22, ccache, and SHA256 artifact evidence. No scheduled CI, no cancellation.

## Acceptance and falsification

First test SQL `SELECT ... avgKBNLab(value) GROUP BY stage` against exact `math.fsum/len` and the already measured synthetic single-point native KBN across all seven groups. Check 15 repeats and `max_threads=1` vs 4/8 to exercise parallel merges. Validate `avgKBNLabState` + `avgKBNLabMerge` across serialized partial states, NaNs/Infs and empty groups separately.

Then replace only final `avg(series_avg)` in the SQL-only native one-point plan with `avgKBNLab(series_avg)`. Compare to the original and to the synthetic KBN reference on the same fixtures; benchmark at N=4200/67200 and E=3/40. Correct outputs should be evaluated against high-precision mathematical reference in extreme cancellation, not against an unstable original.

Reject if any finite test produces non-finite result, if merges lose compensation, if the state cannot serialize/deserialize or if large-query memory/cost worsens without compensating numerical gain. Do not wire into general PromQL converter until semantics including nonfinite/overflow, lifecycle, distributed merges and full data shape are verified.
