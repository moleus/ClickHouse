# Read-only PromQL user: materialized CTE privilege hypothesis (before test)

2026-11-02. Pinned upstream source `52529f0c711566d696917678bf15e7068772fdde`. Lab only.

## Observed blocker
CI run 41455874443 created a localhost-only SQL user with SELECT on default.* and an enforced permissive TAGS row policy. A direct SELECT of TAGS as this user returned ONLY project=allowed, while the admin saw allowed and denied. But the **original** `prometheusQueryRange` failed before returning data: error 497, `CREATE TEMPORARY TABLE ON *.*` required. This is not a v4-specific failure. The initial ACL comparison was inconclusive and must not be counted as a pass or data leak.

## Source hypothesis
`src/Storages/StoragePrometheusQuery.cpp:209-212`:
```cpp
auto query_context = Context::createCopy(context);
if (!context->getSettingsRef()[Setting::enable_materialized_cte].changed)
    query_context->setSetting("enable_materialized_cte", true);
```
The generator enables materialized common table expressions unless the caller explicitly sets the setting. Materialization can require temporary table creation rights. Hypothesis: explicitly adding `SETTINGS enable_materialized_cte=0` to the outer query prevents materialization and allows a SELECT-only user to execute generated PromQL without a global `CREATE TEMPORARY TABLE` grant.

## Experiment
1. New isolated server, one TimeSeries with allowed and denied project stages, single metric; user `tslab_reader` has SELECT default.* and a row policy on inner TAGS, but **NO** `CREATE TEMPORARY TABLE` privilege.
2. Verify raw TAGS SELECT as reader sees only allowed project and admin sees both.
3. Check original unmodified PromQL as reader without explicit CTE setting fails **with precisely** the missing temporary-table privilege. (Expected denial is a negative control, not a test failure.)
4. Check original with `SETTINGS enable_materialized_cte=0`; if allowed, ensure output includes stage-allowed only, excludes stage-denied.
5. Check compiled v4 with both `promql_enable_native_one_point_lab=1, enable_materialized_cte=0`, and v4 opt-out with CTE disabled. Compare exact output labels/time/values and query results with baseline.
6. If explicit CTE=0 still fails due the same missing grant, reject this workaround and require an isolated temporary-table grant or a different converter. If output includes a denied stage, treat as security correctness blocker and save raw error.

Avoid claims that an inner TAGS policy alone is a complete tenant isolation boundary; direct SELECT on the SAMPLES target remains separately authorized when wildcard SELECT is granted. This experiment only tests generated PromQL path equivalence and how CTE privilege affects it.
