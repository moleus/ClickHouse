# R35: remove a redundant native PromQL aggregation

Status: source-matched O2 candidate built; native performance validation is in progress. Do not promote from this document alone.

## Exact source and intervention

Source: `52529f0c711566d696917678bf15e7068772fdde`.
Candidate changes only `src/Storages/TimeSeries/PrometheusQueryToSQL/applyFunctionOverRange.cpp` through [apply_metric_drop.py](apply_metric_drop.py). It does not include R32, R34, or metadata JOIN changes.
Build: https://github.com/moleus/ClickHouse/actions/runs/41732238195
Candidate executable SHA256: `68d6e8f954a44beeb137acea61b0b77afac32cc705e59404413c233b57e6a06b`.
Stock R29 executable SHA256: `5d629ddfda91919402fdf8475ec3da9fe718d48ef386bc51be677264df6dc9ef`.
Both use LLVM22.1.8, Release O2, jemalloc, and identical recorded CMake options.

## Work removed

`dropMetricName.cpp` removes `__name__` from every full tag group. It then groups the per-series grids again, using `anyForEach(values)` and `countForEach(values)`. The latter detects duplicate series after metric-name removal. A following aggregation such as `avg by(stage)` builds yet another grouped grid.

For a direct range selector constrained by a nonempty exact metric-name matcher, every selected full label set has the same metric name. Let a label set be `(m, L)`, where `m` is that constant name. The map `(m, L) -> L` is injective: equal projected label sets imply equal original label sets. The range aggregate already groups by the original full-label group. Thus this specific metric-name-removal step cannot introduce duplicate groups.

An immediate ordinary aggregation that discards `__name__` can perform the final label projection itself. The candidate keeps the original per-series range calculation, including sample weights, counters, timestamps, missing values, and staleness behavior. It removes the intermediate duplicate-check aggregation and intermediate copied full tag sets. It does not replace `avg by(stage)(avg_over_time(...))` with an average of raw samples.

## Conservative eligibility

All conditions must hold:

1. The current node is a one-argument range function that would drop the metric name.
2. Its direct child is a RangeSelector, not an offset or subquery.
3. The selector has a nonempty exact `__name__` equality.
4. Its immediate parent is an ordinary one-argument series aggregation, with this node as its only child.
5. The parent is not `without`, and its labels do not include `__name__`.

The candidate does not mark `metric_name_dropped=true` while the name is still present. The parent therefore uses its normal label projection.

Mixed metrics, name regex, `by(__name__)`, `without`, offsets, subqueries, binary operations, topk, root range functions, and label manipulation retain the stock path. This deliberately leaves valid but unproved opportunities unoptimized.

## Native semantic and physical-plan checks completed

A 147-case stock/candidate native suite passed. It covers 16 range functions under eight ordinary aggregations, plus explicit guard cases. The fixture includes multiple labels, ordinary NaN, infinity, the stale-NaN payload, counters and counter resets. The mixed-metric case retains stock exception code 768.

The positive `avg by(stage)(avg_over_time(foo[30s]))` plan has 3 Aggregating nodes in stock and 2 in the candidate. Five negative guards retain their stock counts: by-metric 3, name-regex 3, without 3, subquery 4, offset 3. This confirms the native path changed; a SQL-only speed result would not establish that.

These are differential compatibility tests against stock, not proof that stock implements every PromQL edge case correctly. The performance fixtures also have an independent Python label/timestamp/value oracle.

## Remaining gates

Complete fresh-process native A/B/B/A with exact physical part identities. Report query memory separately from process RSS. Test dense series as a negative/generalization control. A production promotion still requires concurrency, mixed reads/writes, long retention, access-control tests, and upstream review. Do not multiply R34 and R35 gains without a separate combined build and benchmark.
