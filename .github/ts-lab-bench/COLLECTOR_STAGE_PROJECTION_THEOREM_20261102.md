# 1.33m TimeSeries ID collector: stage-only tag projection causal proof

2026-11-02. Pinned 52529f0c711566d696917678bf15e7068772fdde source:
- `src/Storages/StorageTimeSeriesSelector.cpp:630-647` selects `timeSeriesStoreTags(id,tags,'__name__',metric_name,...)` after label/time filtering.
- `src/Functions/TimeSeries/timeSeriesStoreTags.cpp:18-26,99-121`: stateful per-query ID-to-tags registration, returns the original ID unchanged.
- `src/Interpreters/ContextTimeSeriesTagsCollector.cpp:991-1001` stores one tags group per unique full tag set; `:1203-1266` also maintains an ID->group map. For one-sample Jenkins with unique `build`, group count scales with 1.33m even though `avg by(stage)` returns only 38 stage groups.
- `src/Functions/TimeSeries/timeSeriesIdToGroup.cpp:16-21,84-87` looks up per-query stored group for ID.

## Hypothesis
If `timeSeriesStoreTags(id,NULL,'stage',tags['stage'])` is used *after applying the same original full matcher predicates*, ID count remains exactly 1.33m but group-count falls from 1.33m to 38. This reduces time, allocations and query memory; distinct ID->group map still contains 1.33m entries. We expect a large drop in tag storage cost and CPU hashing. This is **only a candidate** for a planner that proves the final output uses stage labels only, and preserves per-ID aggregation for dense data.

## Falsification
Use same SHA verified 1.33m source and fixed 5GB server cap. In fresh O1 ClickHouse processes execute three standalone diagnostics on the *same internal TAGS table*:
  control no registration: `count(),sum(length(tags))`;
  full: `count(),max(timeSeriesIdToGroup(timeSeriesStoreTags(id,tags,'__name__',metric_name)))` (expected max around1.33m);
  stage: `count(),max(timeSeriesIdToGroup(timeSeriesStoreTags(id,NULL,'stage',tags['stage'])))` (expected group max 38).
Execute each variant 3 times, each after server restart, interleave sequence, preserve querylog memory/read rows, RSS peak and CPU samples. Both registration variants must read **identical 1.33m rows**. Report failures (unknown ID, execution order, low-cardinality types) rather than ignoring them.
Then **only if this passes** prototype a separate planner fallback for one-point avg by(stage), with *all* original selector matchers, TimeRanges, ACL and duplicate timestamp behavior intact, and prove end-to-end against VM and original CH on 1.33m series and smaller dense counterexamples.

## Limits
A standalone direct TAGS source test does not establish full TimeSeries selector or PromQL semantic equivalence. A user can request other labels or `without`, so the general collector may need original labels. A compact stage registry is not globally interchangeable with the per-query full-tag registry. Do not change default behavior, production or upstream. Proof must include SQL invoker ACL and stage naming/regex adversarial cases.
