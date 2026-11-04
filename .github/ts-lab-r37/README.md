# R37: injective metric-name projection without duplicate-check GROUP BY

**Status: C++ research candidate; native performance not yet proven.** Build: [41789987661](https://github.com/moleus/ClickHouse/actions/runs/41789987661).

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
