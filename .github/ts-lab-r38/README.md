# R38 — defer fixed metric-name removal through `without(...)`

**Status: VALIDATED RESEARCH CANDIDATE for the measured native without(...) query; not production-promoted.** R35 direct-control binary SHA256 `68d6e8f954a44beeb137acea61b0b77afac32cc705e59404413c233b57e6a06b`.

## Verified native A/B/B/A, 2026-11-04

[Immutable R38 evidence](../ts-lab/evidence/R38-native-1330k-local-20261104.json), [source-matched R38 build 41791235811](https://github.com/moleus/ClickHouse/actions/runs/41791235811), [155-case native correctness and physical-plan PASS 41791869622](https://github.com/moleus/ClickHouse/actions/runs/41791869622).

The native `avg without(build,project,branch,job)(avg_over_time(ci_duration[1d]))` query, 1.33m series/14d/532 output points, uses exactly the same frozen RAW_UUID parts with 3,990,000 read rows and 336,024,252 read bytes in all phases. Four fresh servers in sequence R35/R38/R38/R35, two warmups+four measured requests each; every response matches the independent generator-derived oracle. Cgroup 4 CPU quota/4GiB memory, ClickHouse server cap 2.85GB, two query threads. Values are means of within-phase medians, not p95/p99.

| Metric | R35 | R38 | Reduction |
|---|---:|---:|---:|
| Elapsed wall | 11,615ms | 7,056.5ms | **39.25%** |
| Query CPU | 11,498.10ms | 6,952.35ms | **39.53%** |
| Tracked query memory | 1,416,390,064B | 870,314,648B | **38.55%** |
| Observed request RSS peaks | 2,049,265,664B | 1,431,579,648B | **30.14%** |

Baseline control drift: wall 1.91%, CPU 2.32%; all query outputs and physical parts equal. R38 is a valid **additional gain compared with R35 on this single native query**. It avoids both the intermediate duplicate-check aggregation and intermediate metric-name group projection. R37 independently saves 32.8% wall on the same logical selector, but the two candidates have **not** been directly compared in one matched ABBA experiment. No production/VM superiority or latency tails claim.

## Source proof

R35 already proved the injectivity of `(exact_metric_name, other_labels) -> other_labels` after a per-series range function, and removes redundant duplicate-check GROUP BY when an immediate `by(...)` parent discards the metric name. R35 explicitly refused `without` parents.

The pinned `transformGroupASTForAggregationOperator.cpp` implementation of `without` always removes `__name__` itself when `drop_metric_name=true` and `metric_name_dropped=false`. See [source](https://github.com/moleus/ClickHouse/blob/52529f0c711566d696917678bf15e7068772fdde/src/Storages/TimeSeries/PrometheusQueryToSQL/transformGroupASTForAggregationOperator.cpp).

R38 removes only the explicit `|| parent.without` exclusion from R35's `canDeferMetricNameRemoval`. The rest of R35's proof remains unchanged: a direct range function with a single exact nonempty metric-name matcher, immediate one-argument aggregation, no `__name__` in parent label list, and no offset or subquery. The parent performs the single required final label projection. R38 removes both the duplicate-check aggregation and the intermediate per-series name-only tag projections. The per-series range calculation, time grid, samples, retention and read path are unchanged.

This is distinct from R37, which *still* projects `__name__` row by row before `without` if no R35 deferral is available. R38 does not include R37 or R36.

## Frozen experiment

- [R38 canonical root](../ts-lab/claims/roots/R38.json) and [claim](../ts-lab/claims/R38-defer-exact-metric-through-without-v1.json).
- [Single-line source-pinned patch helper](apply_without_deferral.py), applied **after** [R35](../ts-lab-r35/apply_metric_drop.py) to the pinned upstream source `52529f0c711566d696917678bf15e7068772fdde`.
- [Optimized O2/jemalloc build workflow](../workflows/ts-lab-r38-without-deferral-o2-20261104.yml); same LLVM22.1.8 flags, same pinned submodules and ccache as R35.
- [155-case native guard](guard_suite.py) compares all outputs to R35. Positive `without` and `wide_without` plans should lose one Aggregating node; direct `by(stage)`, `by(__name__)`, root range, regex, subquery, offset, topk and binary remain unchanged. Mixed-metric duplicate error still required.

## Performance and safety gates

Primary native query on the fixed R35 1.33m / 14d RAW_UUID data:

```promql
avg without(build,project,branch,job)(avg_over_time(ci_duration[1d]))
```

Expected output: exactly 38 `stage` series and 532 timestamp/value points, checked against the independent generator-derived R35 oracle. Compare R35/R38/R38/R35 under exactly the same 4-CPU/4GiB cgroup, 2.85GB ClickHouse server memory cap and `max_threads=2`. Two warmups plus four measured requests per fresh-server phase, same physical parts, full output and QueryFinish logs. Report wall, CPU, query memory and observed request RSS peaks, baseline drift and any errors. Test `without` with smaller selectors, dense time-series, missing labels, NaN/staleness and mixed metric regex before production promotion.

A source build alone is not speed evidence. Do not multiply R35/R36/R37 gains. Do not modify master, upstream or production.
