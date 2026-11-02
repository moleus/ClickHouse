# Can existing TimeSeries KBN arithmetic repair stage-level grouped AVG?

2026-11-02, written before the SQL experiment. Pinned upstream ClickHouse source 52529f0c711566d696917678bf15e7068772fdde.

## Problem and evidence

- v3 native-one-point per-ID aggregation is correct on 6 numerical tests and faster on 67,200 sparse series, but its final per-stage SQL average uses ordinary `avg`.
- Original ClickHouse PromQL `avg by(stage)` lowers to `avgForEach` in `src/Storages/TimeSeries/PrometheusQueryToSQL/applyOneArgumentAggregationOperator.cpp`; the original engine also varies between runs with large cross-ID positive/negative cancellation (41439318904).
- Prometheus `promql/engine.go` calls `kahansum.Inc` on cross-ID average and has an overflow fallback.
- ClickHouse built-in `sumKahan(series_avg)/count()` was not robust for this workload (41440060987). It is not the same as native `timeSeriesAvgToGrid` compensated implementation.

## Hypothesis to test without a new C++ build

The native `timeSeriesAvgToGrid` `Summary` uses Kahan-Babuska-Neumaier compensated sum and count (`AggregateFunctionTimeseriesCompensatedSum.h`). It deduplicates equal timestamps, so it cannot aggregate per-series values directly at the same evaluation time.

For a stage with G per-series averages v[1]..v[G], create synthetic, unique timestamps `t_i=-i ms`, i=1..G; all satisfy a single native window `(-86400000,0]` if G<86,400,000. Use `groupArray` + `arrayEnumerate` to materialize the pairs (DateTime64(-i), v[i]), then call the exact native `timeSeriesAvgToGrid` on the one-point grid at the Unix epoch. No timestamps collide. The returned value should approximate the oracle `math.fsum(v)/G` regardless of input permutation; the KBN arithmetic should suppress most cancellation.

SQL prototype `per_stage`: `SELECT stage_group, gi, assumeNotNull(arrayElement(timeSeriesAvgToGrid(fromUnixTimestamp64Milli(0),fromUnixTimestamp64Milli(0),toDecimal64('86400',3),toDecimal64('86400',3))(arrayMap((i,v)->tuple(fromUnixTimestamp64Milli(-toInt64(i)),v),arrayEnumerate(vals),vals)),1)) AS stage_avg FROM (SELECT stage_group,gi,groupArray(series_avg) AS vals FROM per_series GROUP BY stage_group,gi)`.

Test also explicit descending absolute magnitude order of vals (as diagnostic only).

## Important limitations

This is **only a numerical and source-equivalence probe**, not a proposed production SQL plan: materializing a large array per stage costs O(G) memory and potentially a high allocation count; synthetic timestamps are not Prometheus samples and may violate range bounds for G>=86.4M. Native KBN partial state merge is not guaranteed exact, and NaN/Inf behavior requires separate cases. Using a different summation order does not guarantee bitwise equivalence with Prometheus, even if both use KBN.

Compare unchanged original, translated no-dedup + ordinary avg, translated + native KBN stage, translated + sorted KBN, and original again. 2 warmups +15 repeats each on the *same* immutable 7-stage synthetic fixture. Measure p50/p95, per-stage min/max/number of distinct outputs, median absolute error vs high-precision fsum, and HTTP errors. Include baseline instability as an independent finding.

Reject if it produces wrong stage groups, NaNs on finite samples, or increased mathematical error for most cases. If it improves accuracy, the next engineering step is a streaming C++ aggregate with the same compensated state, count and documented merge logic, to avoid `groupArray` and O(G) extra allocations. No production promotion, upstream writes, CI cancellation or long-lived workflows.
