# R37: injective metric-name projection without duplicate-check GROUP BY

**Status: VALIDATED RESEARCH CANDIDATE for the measured without(...) selector; native full-query gain reproduced in fresh-process A/B/B/A. Production release NOT approved.** Build: [41789987661](https://github.com/moleus/ClickHouse/actions/runs/41789987661).

## Completed full native result — 2026-11-04

[Immutable experiment summary](../ts-lab/evidence/R37-native-1330k-local-20261104.json) and [155-case native oracle CI](https://github.com/moleus/ClickHouse/actions/runs/41790880980). R35 and R37 are pinned to the same source, LLVM 22.1.8, Release O2 and jemalloc; R37 changes only `applyFunctionOverRange.cpp`. The user-provided raw evidence archive in the originating chat contains phase reports with complete native output, query logs, independent Python fixture oracle and physical part census.

Full 1.33m-series, 14-day `avg without(build,project,branch,job)(avg_over_time(ci_duration[1d]))`, 532 full oracle-matched output points, unchanged 3,990,000 physical read rows and 336,024,252 read bytes. Four fresh server processes in order R35/R37/R37/R35. Two warmups and four measured requests per phase. Same RAW_UUID parts, 4 CPU quota, 4GiB memory cgroup, 2.85GB server memory cap, two query threads.

| Metric | R35 | R37 | Change |
|---|---:|---:|---:|
| Wall time, mean of phase medians | 12088ms | 8123.75ms | **−32.79%** |
| Query CPU, mean of phase medians | 11976.64ms | 8026.72ms | **−32.98%** |
| Tracked query memory | 1,416,389,840 B | 1,366,786,256 B | **−3.50%** |
| Mean observed request RSS peaks | 2,035,899,392 B | 2,025,735,168 B | −0.50%; not a guaranteed maximum |

Control drift: R35 wall 6.78%, query CPU 6.73%. Every native output matches the independent fixture-derived oracle. All query-log rows and physical part identifiers were verified. This is a new win **relative to R35 for this one query**; do not multiply with R36's distinct whole-metric improvement or infer p95/p99.

Secondary experiment R38 may avoid even the intermediate row-preserving projection for the immediate `without(...)` case. R37 already eliminates full regrouping; R38 is a separate opt-in candidate.

## Why this is new relative to R35

R35 eliminates `dropMetricName()` when a direct one-metric range function is immediately consumed by a safe ordinary `by()` aggregation. Its measured 1.33m/14d whole-metric result is about 35% faster than optimized stock. R35 deliberately leaves root range functions, `without`, `by(__name__)`, `topk` and binary parents on the original expensive path.

The pinned native compiler calls `dropMetricName()` after `rate(metric[5m])`, `avg_over_time(metric[1d])`, and other drop-name range functions. Stock `dropMetricName()` creates a new `GROUP BY` and `anyForEach(values)`/`countForEach(values)` duplicate detection. A second aggregation is unnecessary when the selector has a **single exact, nonempty metric name**: full label set `(metric,L)` maps injectively to `L`. Distinct original full-label groups cannot become equal by removing the same constant name.

R37 keeps R35's existing safe parent deferral first. When that deferral is unavailable but the same exact-name proof holds, it replaces the duplicate-check aggregation with a **row-preserving SELECT projection**. It still calls `timeSeriesRemoveTag(group,'__name__')` to make the output labels correct and marks `metric_name_dropped=true`. A separate `new_group` rename query avoids SQL column/alias ambiguity.

Because no range aggregate or raw sample access changed, no averaging across series is moved before the per-series range calculation. The query still groups raw data per original full-label set; it does not replace average-of-per-series-averages with average-of-raw-samples.

## Source and contract

- Canonical root and [claim](../ts-lab/claims/R37-injective-drop-without-regroup-v1.json); [source-pinned R37 helper](apply_injective_projection.py).
- R35 source patch [here](../ts-lab-r35/apply_metric_drop.py), then R37 source patch. Input R35 compiler blob SHA1 `b231510bfa086d20ef0e5bd4037fbd62897ee943`; refuse unexpected source.
- Pinned upstream source `52529f0c711566d696917678bf15e7068772fdde`. R35 build [41732238195](https://github.com/moleus/ClickHouse/actions/runs/41732238195) is the exact **control** (LLVM22.1.8, Release O2, jemalloc). Only `applyFunctionOverRange.cpp` changes for R37; no R36, R34 or R30/R31.
- R37 guard suite [here](guard_suite.py) compares native output vectors and physical-plan changes with R35.

## Source-level upper bound, not a speed claim

Let N be the number of unique full-label groups entering a range function and G the number of query evaluation steps. The stock metric-name drop adds a hash aggregation holding up to N groups and performs `anyForEach` and `countForEach` over G grid positions. R37 avoids that **additional** regrouping and duplicate-check work. It still has N projection calls and still produces N per-series grids, so cannot claim zero-copy output.

## Correctness gates

1. Differential full native vectors, timestamps, labels and values against R35 for 155 compatibility cases; compare NaN, ordinary infinity and stale-NaN payload cases, missing labels and absent sets. The source's mixed-metric duplicate-series exception must remain.
2. Positive physical plan must lose exactly one aggregation on `root avg_over_time(foo[30s])`, `sum without(instance)(avg_over_time(foo[30s]))`, `sum by(__name__,stage)(avg_over_time(foo[30s]))`, `topk` and binary cases. R35 `avg by(stage)` plan remains unchanged.
3. Regex and multiple-metric selectors, subqueries, offsets and incompatible function arguments must retain the R35 fallback and plan structure.
4. Benchmark native `avg without(build,project,branch,job)(avg_over_time(ci_duration[1d]))` (same 1.33m input and 38 output labels), selected-root `avg_over_time(ci_duration{stage="stage-1"}[1d])`, by-metric and dense rate controls. Use independent result oracle; matched fresh-process R35/R37/R37/R35, at least two warmups and four measurements per phase; exact physical read rows/bytes, server RSS and query memory.

## Promotion

A compiled binary or a faster standalone SQL is insufficient. Accept only a native, source-matched improvement with complete oracle and stable controls. Validate read/write/merge, retention, ACL and distributed paths before any production change. No master/upstream/production writes.
