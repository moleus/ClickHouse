# Materialized CTE setting propagation under PromQL table-function context

2026-11-02. Written before experiment, all changes lab fork.

## Known results
- Run 41456318737: non-admin with SELECT default.* and `CREATE TEMPORARY TABLE ON *.*`, plus a real restrictive TAGS row policy, successfully queried original and opted-in v4 without seeing the forbidden project.
- Run 41456650385: same restrictive row policy with only SELECT default.*, **without** CREATE TEMPORARY TABLE. Original PromQL failed with HTTP403, `ACCESS_DENIED: CREATE TEMPORARY TABLE ON *.*`. Adding the outer SQL `SETTINGS enable_materialized_cte=0` **did not resolve it**. This is a failed read-only workaround, not a v4-specific failure.
- Pinned source `StoragePrometheusQuery.cpp` copies its interpreter context and enables `enable_materialized_cte` when the original context setting is not marked `.changed`; `SQLSubquery.h` documents that `MATERIALIZED` CTE is a hint controlled by this setting. Need distinguish actual context propagation from an independent temp-table privilege check.

## Hypotheses to falsify
H1: The outer SQL `SETTINGS` only takes effect after `StoragePrometheusQuery::readImpl` has checked `.changed`, so the inner generated SQL runs with materialized CTE=1 even though the outer SQL explicitly sets 0.
H2: The setting is in effect as 0, but a different operator/table function still requires CREATE TEMPORARY TABLE.

## Test
- Use isolated local v11 TimeSeries, TAGS row policy + SELECT-only reader, and one original broad `avg by(stage)(avg_over_time(...[1d]))`.
- Queries:
  a. no setting (expected 403),
  b. SQL `SETTINGS enable_materialized_cte=0`,
  c. HTTP parameter `enable_materialized_cte=0`,
  d. SQL `SETTINGS enable_materialized_cte=0` plus HTTP param 0,
  e. HTTP param 1 (expected 403).
- Add separate `SELECT getSetting('enable_materialized_cte')` with the same HTTP parameter to prove parsing.
- Turn isolated server logging to INFO and capture converter's `Will execute query` SQL for evidence of WITH MATERIALIZED vs no materialization. Persist query text and errors.
- If any query works without temp grant, compare exact allowed-only output with original/v4 run using temp grant, and separately check v4 opt-in.
- This is a diagnostic (no C++ build). Do not silently grant temp rights; do not claim no-temp reader works unless it actually produces allowed-only data and denies forbidden label.

Reject H1 if HTTP param 0 still fails even when `getSetting()` proves 0, unless logs show the inner converter explicitly overwrites it. Distinguish no result because of grant from numerical error.
