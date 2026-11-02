# Request-time metric cardinality probe: bound and falsification

Date 2026-11-02. Written before benchmark implementation. No C++ patch is authorized.

## Question

Can a separate metric-specific `COUNT` estimate selected TimeSeries cardinality cheaply enough to choose native versus fused scalar grouped AVG in the PromQL converter, without paying more than the query saves?

## Source evidence

- `StoragePrometheusQuery.cpp` invokes the `PrometheusQueryToSQL::Converter` before the `timeSeriesSelector` table function is executed.
- `src/Storages/StorageTimeSeriesSelector.cpp:619-675,1297-1343` builds a TAGS-id query only in selector read planning; it also may perform a time-range join and ID set filtering.
- `src/Functions/TimeSeries/timeSeriesStoreTags.cpp:114-120` records IDs/tags only during `executeImpl`, not at planning time. The collector cannot supply a selected-ID count to the earlier converter without extra work.
- The v2 guard on all SAMPLES rows was falsified by run `41433029087`.

## Cost proof obligation

For a 210-series query with native p50 ≈ 16–18ms, a budget of 5% for an extra synchronous estimate is ≤0.8–0.9ms **end-to-end**. Let `T_estimate = T_plan + T_index + T_scan + T_IPC + T_sync`. If `T_estimate >0.9ms`, even an ideally perfect choice cannot meet that small-query overhead requirement unless the estimate cost overlaps other required selection work.

Measure both client HTTP time and `system.query_log.query_duration_ms` (the latter excludes some transport overhead). Same server, data and index, 2 warmups and 15 repeats. Compare:
1. `SELECT 1` for HTTP/planner floor.
2. `SELECT count() FROM jenkins_ts WHERE metric_name=...` public outer table.
3. `SELECT count() FROM default.<inner-tags-target> WHERE metric_name=...` inner TAGS only (diagnostic; not a permitted production bypass).
4. `SELECT uniqExact(id) FROM default.<inner-tags-target> WHERE metric_name=...` to account for possible duplicate tag rows.

Counts of all-time TAGS are NOT the number of series selected by a query with time and arbitrary labels: if the requested interval contains only 210 of a historical metric's 100000 series, a total metric count gives a false-positive high-N decision. Thus even a free `COUNT` on all tags is at most an upper bound for a properly selected, time-filtered set. A safe rejection of fastpath for low count may use a verified upper bound; **selection of the fastpath based on high total count is not proven**.

## Acceptance

- All counts match the deterministic synthetic fixture where each series has one distinct tag row, with no replacement merges.
- Log p50/p95, raw observations, read_rows and query duration; show 0.8–0.9ms bound explicitly.
- Do not promote any C++ guard based only on this probe; the v1 source is already numerically wrong for multi-sample series. This is only a feasibility test for a future **semantically correct** fastpath.
- No new C++ build, no scheduled workflows, no upstream/fork-master writes. If a source estimate is not cheap and conservative, investigate execution-stage adaptive dispatch instead.
