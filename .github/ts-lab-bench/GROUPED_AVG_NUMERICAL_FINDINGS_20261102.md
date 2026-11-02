# Cross-ID grouped AVG: source and numerical evidence

2026-11-02. Independent proof and falsification, pinned ClickHouse upstream 52529f0c711566d696917678bf15e7068772fdde.

## Native ClickHouse and Prometheus source

- ClickHouse `src/Storages/TimeSeries/PrometheusQueryToSQL/applyOneArgumentAggregationOperator.cpp:77-80` maps PromQL `avg by(...)` to `avgForEach`. The scalar one-point SQL candidate also uses ordinary `avg(series_avg)` per stage.
- ClickHouse `src/AggregateFunctions/AggregateFunctionAvg.h` uses ordinary numerator addition/merge in `AggregateFunctionSumData`, without the Kahan-Babuška-Neumaier compensation used by `timeSeriesAvgToGrid` at the *per-series* level.
- The Prometheus engine's `promql/engine.go:4444-4555` explicitly uses `kahansum.Inc` for `avg` across input series (with a fallback to incremental mean near float overflow). `util/kahansum/kahansum.go` implements a Neumaier-style correction. Therefore ordinary ClickHouse `avgForEach` is not numerically identical to Prometheus's grouping path in all cases, even if raw per-series averages match.

## Numerical proof counterexample

For N distinct short-lived series with the same stage, values `[1e18,1,-1e18]`, the mathematical mean is `1/3`. Ordinary Float64 grouping may lose the 1. A group `[-1e18] + [1.0]*127 + [1e18]` has mathematical mean 127/129=0.984496124..., but changing arrival and group-merge order changes the intermediate rounded sum.

## Measured same-binary instability — run 41439318904

On one unchanged TimeSeries table with several hundred distinct series split into seven stage groups, 15 repeats after two warmups per phase:
- `group_many_small`: exact `math.fsum/129=0.9844961`; unchanged native original first phase output **0.317829.. to 0.620155..** (5 distinct rounded values), recheck **0.031008.. to 1.209302..** (6 distinct values).
- `group_many_small_reversed`: oracle 0.9844961; native first-phase **0.217054.. to 1.294574..**; native recheck also unstable.
- `group_big_split`: oracle 0.50248756; original returned values **0 to 0.00995025**.
- `group_many_interleaved`: oracle 0.5; original returned **0 to 0.0125**.
- `group_three`: oracle 1/3; native sometimes 0, sometimes 1/3.
- `group_small_exact` (101 ones) was stable and exactly 1.
- Both translated one-point variants also showed different numerical results and some repeated-run instability for these adversarial cross-ID groups. The first direct exact-match test 41439009696 failed *before candidate execution* because two original reads of `group_many_small_reversed` differed: 1.2945736434 vs1.3333333333.

**Conclusions:**
1. Original `avg by(stage)` is not deterministic/accurate for extreme cross-series cancellation. This is an independent original-engine semantic issue; it is NOT caused by the v3 patch.
2. Requiring pointwise equality with an unstable baseline is ill-posed on this fixture. Do not claim numerical equivalence of v3 for all high-cancellation grouped cases. Report candidate distributions and distance from high-precision oracle.
3. Replacing `avgForEach` with `sumKahan/count` without controlled tests is NOT proved correct: SQL `sumKahan` did not reproduce native compensated per-ID results in 41436423717. An accurate stage-level aggregate may need a dedicated Kahan-Babuška-Neumaier state, a defined parallel merge policy, and explicit benchmark/Prometheus validation.
4. This is a research-only finding, not grounds for changing all production `avg` behavior. Test NaN/Inf, overflow, concurrency, 14–90d, numerical input distribution and precision before a patch.

Source URLs:
- https://github.com/ClickHouse/ClickHouse/blob/52529f0c711566d696917678bf15e7068772fdde/src/Storages/TimeSeries/PrometheusQueryToSQL/applyOneArgumentAggregationOperator.cpp
- https://github.com/prometheus/prometheus/blob/main/promql/engine.go
- https://github.com/prometheus/prometheus/blob/main/util/kahansum/kahansum.go
- https://github.com/moleus/ClickHouse/actions/runs/41439318904
