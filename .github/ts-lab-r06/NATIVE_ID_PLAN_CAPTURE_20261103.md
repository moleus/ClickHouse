# R06 — extracted native ID-selector SQL and physical JOIN plan capture

**Scope:** E03 source proof, isolated 8,192-ID fixture, no compile. **Status: PASS for plan capture, NOT a benchmark gain or production plan decision.** Owner token `chatgpt-e03-order-aware-20261103`; [canonical claim](../ts-lab/claims/roots/R06.json).

## Provenance

- Baseline code: `52529f0c711566d696917678bf15e7068772fdde`, TimeSeries v11. O1 pinned JOIN override is lab-only, not release-like.
- Existing artifact rebuilt? **No.** SHA verified unchanged binary `3990e373b43396d0faafd8feffe5dd6514bd57c520ec12e89b8e5796d64e6221` from [build 41531656434](https://github.com/moleus/ClickHouse/actions/runs/41531656434).
- New deterministic 7d fixture: `8192` series, `32768` samples; SHA256 `df9cdfdf76e32ab95a1fbcfeb2f5f189e60fbce0c5ed3f57573829c8bd2a80cb`. Single metric `r06_cpu_seconds_total`, labels stage/project/node, 4 samples/ID. It is a **small plan parser fixture**, not the 1.33m production-like benchmark fixture.
- Verified [CI 41673047398](https://github.com/moleus/ClickHouse/actions/runs/41673047398): PASS, source/binary and native values checked, evidence artifact `13249730581`, archive ZIP SHA256 `eb6cd432ef9664e8304f0c149648b6ceab5161aa701db092b160794fbf34181e`, 250,992 bytes.
- Source: [native plan extractor](probe_nested_selector_plan.py), [unit tests](test_probe_nested_selector_plan.py), [isolated workflow](../workflows/ts-lab-r06-nested-plan-20261103.yml). 4 query variants for `stage` (7 output points) and `project` (133 output points), complete native parity (`max_abs_error=0`).

## What was invisible before

A top-level `EXPLAIN PLAN SELECT * FROM prometheusQueryRange(...)` is not enough: the compiled TimeSeries selector creates nested SQL and its input ID set. The nested outer SQL `SELECT ... FROM SAMPLES WHERE in(id, (SELECT timeSeriesStoreTags(...) FROM TAGS LEFT JOIN TimeRanges...))` also mostly explains SAMPLES and hides the physical JOIN inside `in(id, subquery)`.

The new collector reads the `StorageTimeSeriesSelector: Will execute query:` DEBUG line, captures the entire generated SQL and parses balanced parentheses to extract the inner `SELECT timeSeriesStoreTags(...) FROM TAGS ALL LEFT JOIN (SELECT id,1 AS matched FROM TimeRanges...) ...`. It then runs standalone `EXPLAIN PLAN indexes=1,projections=1` and `EXPLAIN PIPELINE` on this ID-selector, **with the same effective settings** as the native query. The standalone plan is diagnostic, not proof that the native IN-set subquery is always executed with identical steps. Verify actual operators with native `system.processors_profile_log` when making performance claims.

| Native mode | Settings applied to extracted ID SQL | EXPLAIN physical JOIN, stage and project | TimeRanges projection |
|---|---|---|---|
| OFF + merge | `full_sorting_merge`, in-order 1, projections 0 | `FullSortingMergeJoin` | OFF |
| AUTO + merge | `full_sorting_merge`, in-order 1, projections 1 | `FullSortingMergeJoin` | Considered, not selected |
| OFF + hash | `hash`, in-order 0, projections 0 | `SpillingHashJoin(HashJoin)` | OFF |
| AUTO + hash | `hash`, in-order 0, projections 1 | `SpillingHashJoin(HashJoin)` | Considered, not selected |

Both plan and pipeline capture succeeded for all 8 cases. `time_by_max` is considered usable but estimated to read one mark and **does not help with ID sorting**. ClickHouse therefore correctly keeps the original table in this small fixture. This is NOT proof that projections should be globally disabled: the 1.33m historical [factorial](HISTORICAL_FACTORIAL_REDUCTION_20261103.md) did choose a costlier projection and paid for sorting.

## Captured failure modes and method repair

- [Run 41670929674](https://github.com/moleus/ClickHouse/actions/runs/41670929674): **INVALID_METHOD**. Parser wrongly demanded `series_id` alias in outer SQL; original logs preserved in [immutable failure record](../ts-lab/evidence/R06-nested-plan-invalid-method-41670929674.json).
- [Run 41671546950](https://github.com/moleus/ClickHouse/actions/runs/41671546950): eight native generated SQL strings/outer PLAN+PIPELINE, all PASS, but did not expose nested JOIN. Valid for capture, insufficient for join attribution.
- [Run 41672454144](https://github.com/moleus/ClickHouse/actions/runs/41672454144): standalone ID extraction and EXPLAIN PASS, but algorithm comparison **INVALID_METHOD**: standalone merge variants omitted selector's forced `full_sorting_merge`; default EXPLAIN used hash for all four. This does not establish actual native JOIN behavior. Repaired by adding effective source settings, plus unit tests.
- [Run 41673047398](https://github.com/moleus/ClickHouse/actions/runs/41673047398): matching join algorithm and native output parity confirmed; output claims above are based on this repaired run.

## One new CBO hypothesis requiring independent measurement

In the extracted ID `EXPLAIN PLAN`, both `stage` and `project` filter shapes have estimated **left input 1.35 thousand** and estimated **right input 892 rows**. From the deterministic input fixture, `stage="stage-0007"` should match 216 distinct IDs, `project="project-00"` should match 410 distinct IDs, and all 8192 unique series have TimeRanges overlapping the selected days. However, fixture counts and internal physical row counts are distinct facts; before calling this a statistic error, compare with `SELECT count(),uniqExact(id)` on the actual TAGS and TimeRanges targets, using exactly the native time window.

A false-low right-hand build estimate is particularly dangerous if used directly to admit a hash JOIN: memory cost depends on *actual* right-hand rows, hash table growth, collector/ID-set state and concurrent RSS. The source's ordered merge must remain the default until a conservative upper bound and held-out validation exist. A source statistic is a prior, not a memory safety guarantee.

## Status and limitations

- **PASS:** generated SQL capture, accurate pinned context settings, physical standalone JOIN plan verification, output equality and no new compile.
- **NOT TESTED:** 1.33m nested EXPLAIN, 14d production-like cardinality, trained CBO coefficients, multi-client RSS limit, write/merge/TTL correctness, native adaptive C++ code or any performance gain.
- **NO CHANGE:** production, master, upstream, other RNN owners, workflow schedules and existing jobs.
