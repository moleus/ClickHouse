# R41 — final-output-only metric-name projection for native root range queries

**Status: C++ research candidate; native correctness and performance have not yet been established.**

## Why this is not R37 again

R35/R38 allow an immediate `by` or `without` parent to remove an exact constant metric name. R37 handles nondeferred expressions by deleting `__name__` from the group IDs just after a range function. That removes an extra aggregation, but it still creates/registers a projected group ID for **every distinct full-label source series**.

For a root expression such as `avg_over_time(ci_duration{stage="stage-1"}[1d])`, there is no downstream operation consuming projected `group` IDs. Finalization only needs the output **tags**. R41 keeps the original group ID through the range calculation and, only at the final output boundary, removes `__name__` from `timeSeriesGroupToTags(group)` using an `arrayFilter` over the label tuples. This avoids an intermediate `timeSeriesRemoveTag(group,'__name__')` collector lookup/registration for each series. The output tag array is still constructed, and sample reads, range evaluation, sorting and sample values do not change.

The proof requires a direct range selector with an exact, non-empty `__name__`. For all returned original groups the name is constant, so removing it is injective; two distinct source groups cannot collide. Never apply this to regex selectors that may contain several metrics, `offset`, subqueries, nested expressions, or functions that intentionally preserve the name (`last_over_time`, `first_over_time`). Existing no-duplicate error semantics remain unchanged when this proof is absent.

## Implementation and source guard

- [Source-pinned three-file helper](apply_late_root_tags.py); upstream source `52529f0c711566d696917678bf15e7068772fdde`, build LLVM 22.1.8 O2 jemalloc, R40 as exact compiler baseline.
- `applyFunctionOverRange.cpp`: after R35/R38 safe-parent check, when there is no parent and R37's exact-metric proof holds, set `metric_name_deferred_to_output` rather than constructing projected group IDs.
- `SQLQueryPiece.h`: add one bool default false, valid only for the root exact-metric range result.
- `finalizeSQL.cpp`: wrap `timeSeriesGroupToTags(group)` with a native SQL `arrayFilter` retaining every tuple whose key is not `__name__` when that flag is set. This works for instant- and range-response finalization, and retains all tag values and their order.
- [Primary claim](../ts-lab/claims/R41-root-late-name-projection-v1.json) and [root](../ts-lab/claims/roots/R41.json).
- [Isolated optimized build workflow](../workflows/ts-lab-r41-late-root-tags-o2-20261104.yml), [native guard](guard_suite.py).

## Required negative and performance gates

Run both R40 and R41 on exactly the same native PromQL fixtures. Verify the 155 inherited compatibility cases plus root `rate`, `sum_over_time`, `last_over_time`, explicit exact-name selectors, and name-regex multi-metric results. Compare labels, timestamps, values, NaN/Inf/stale markers and error codes, not only row counts. Physical `Aggregating` node counts and all unrelated query shapes must remain unchanged.

For the performance gate, use the frozen 1.33m-series RAW_UUID fixture. Filter `stage="stage-1"` to return 35,000 independent full-label series, each with one distinct sample and 14 evaluation steps. Compare R40/R41/R41/R40 on identical parts with fresh processes, 4 CPU quota, 4GiB cgroup, 2.85GB ClickHouse server cap and two query threads. Every output must match a generator-derived root-series oracle (including full label sets). Record QueryFinish wall/CPU/memory/read counters and sampled process RSS. Do not substitute standalone SQL or `COUNT` probes that remove output tag processing from the timed query.

**R41 is not a confirmed speedup until these tests pass.** No production/upstream/master changes.
