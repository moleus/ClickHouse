# Isolate CREATE TEMPORARY TABLE requirement to TimeSeries selector or PromQL wrapper

2026-11-02. Proof-before-run. Pinned ClickHouse source 52529f0c711566d696917678bf15e7068772fdde; no production changes.

## Evidence
Run 41457566304 demonstrated SELECT-only caller with restrictive TAGS row policy is denied `CREATE TEMPORARY TABLE ON *.*` for 12 combinations: old/new C++ binary, materialized CTE on/off in SQL or HTTP, v4 explicit opt-in. `getSetting` probes confirmed HTTP 0/1 values were accepted. Run 41456318737 with temp grant showed correct policy semantics in four original/v4 phases. Thus neither setting delivery nor source selection alone avoids privilege requirement.

## Source fault isolation
`StoragePrometheusQuery::readImpl` calls the converter and then an InterpreterSelectQueryAnalyzer of generated SQL. V4 emits its own direct SQL using `timeSeriesSelector` and `timeSeriesIdToGroup`. Both paths still demand temporary table permission, so the cause may be inside shared table functions or the outer generator rather than MATERIALIZED CTE.

Experiment on **the same reader with only SELECT and a verified inner TAGS row policy**, against a tiny isolated TimeSeries:
1. `SELECT count()` directly from physical inner TAGS; control must return one allowed entry.
2. `SELECT count()` directly from physical inner SAMPLES; confirms underlying physical SELECT has no temp privilege requirement.
3. `SELECT count()` from `timeSeriesSelector('default','access_ts','tslab_acl_metric', fromUnixTimestamp64Milli(...), fromUnixTimestamp64Milli(...),'samples')`; and `timestamp_and_value`. These are the earliest common functions in both plans.
4. Query `timeSeriesIdToGroup`, `timeSeriesGroupToTags` over selector output; captures whether a tag/group helper triggers permission.
5. Compare original `prometheusQueryRange` as denied control. Save raw success/errors, 403 grant name, no privilege escalation, no failed workflow for expected denial.
6. If raw selector requires temp grants, that explains why materialized CTE off did not fix it. If raw selector works but PromQL denies, locate the later helper or outer wrapper, do not guess.

This is only diagnosis; in particular, a SELECT wildcard grant on the internal SAMPLES table means that a reader might access raw data independent of TAGS policy. Do not recommend that grant arrangement as complete tenant isolation.
