# E03/R06 — verified native ID selector JOIN-cardinality estimate error

**Date:** 2026-11-03. **Status:** PASS (estimate-vs-actual diagnostic), **no measured adaptive planner gain**. Owner: `chatgpt-e03-order-aware-20261103`; [canonical R06 root](../ts-lab/claims/roots/R06.json).

## Exact evidence and scope

- Source revision `52529f0c711566d696917678bf15e7068772fdde`, TimeSeries v11.
- Same SHA-verified lab O1 binary as all R06 plan probes: `3990e373b43396d0faafd8feffe5dd6514bd57c520ec12e89b8e5796d64e6221`. No C++ rebuild, *not* optimized release.
- New read-only plan fixture: 8192 unique series, 32768 samples, SHA256 `df9cdfdf76e32ab95a1fbcfeb2f5f189e60fbce0c5ed3f57573829c8bd2a80cb`; one metric, 7d, deterministic tags `stage`, `project`, `node`, four sample points per series.
- [Completed CI 41673788133](https://github.com/moleus/ClickHouse/actions/runs/41673788133), `PASS` with empty errors and native exact output parity across 8 stage/project × merge/hash × projection ON/OFF variants. Artifact ID `13248783964`, ZIP SHA256 `de2a1b3e7202645564a051d148a492f64218e24a622f4a9ec98156e79114b020`, size 253843 bytes. The artifact includes raw fixture, generated internal SQL, inner `EXPLAIN PLAN/PIPELINE`, direct actual COUNT and uniqExact, source/binary verification, unit tests and server log.
- Script: [probe_nested_selector_plan.py](probe_nested_selector_plan.py), [test suite](test_probe_nested_selector_plan.py), and [isolated workflow](../workflows/ts-lab-r06-nested-plan-20261103.yml). A separate plan-only proof and its pitfalls are in [NATIVE_ID_PLAN_CAPTURE_20261103.md](NATIVE_ID_PLAN_CAPTURE_20261103.md).

## Join input estimate vs measured input cardinality

Raw `EXPLAIN PLAN indexes=1,projections=1` on the native-generated inner `SELECT timeSeriesStoreTags(id,tags,...) FROM TAGS ALL LEFT JOIN (SELECT id,1 AS matched FROM TimeRanges WHERE ...) USING(id)` **with native-effective JOIN and in-order settings**. Compare plan's `Left: rows estimated ...` and `Right: rows estimated ...` against separate exact SQL `count(), uniqExact(id)` on the same physical table inputs and predicates.

| Input after its filters | EXPLAIN estimated rows | Actual `count()` | Actual distinct IDs | Actual / estimated | Interpretation |
|---|---:|---:|---:|---:|---|
| TAGS, metric + `stage="stage-0007"` | 1350 | 216 | 216 | 0.1600 | 6.25x **over**estimate |
| TAGS, metric + `project="project-00"` | 1350 | 410 | 410 | 0.3037 | 3.29x **over**estimate |
| TimeRanges, metric ID range + overlapping 7d | 892 | 8192 | 8192 | 9.1839 | 9.18x **under**estimate |

The planner displayed the exact same left/right estimates for stage and project despite different true label selectivity. Both merge and hash variants produce these same cardinality estimates, and all eight native output parity checks pass. The specific estimate `1350` is the printed `1.35 thousand`, not a machine-exact unrounded statistic.

Direct TimeRanges count uses the same temporal window as native `avg_over_time(...[1d])`, including the one-day lookback: from Unix milliseconds `1792368000001` through `1792972800000` inclusive. The fixture contains only one metric, so the generated metric-ID range covers all TimeRanges identifiers; no competing metric IDs. All 8192 series overlap the requested range. Direct TAGS count also matches independent raw-fixture counts (216 stage, 410 project).

The values compare **logical JOIN input cardinality estimates to physical filtered table counts**. A SQL optimizer's formatted row estimates are approximate, can be affected by where PREWHERE and other operators run, and are not direct allocated-memory measurements. This is a decisive fixture-specific counterexample to using these estimates as safe hash build capacities. It is **not** proof of a fixed global bias or poor estimates on all datasets.

## Physical plan and memory implications

- With explicitly matched `join_algorithm=full_sorting_merge,optimize_read_in_order=1`, extracted inner SQL physically plans a `FullSortingMergeJoin`.
- With `join_algorithm=hash,optimize_read_in_order=0`, it physically plans `SpillingHashJoin(HashJoin)`.
- Time projection `time_by_max` is considered but *not chosen* on this small dataset because it covers one mark and loses the ID-order benefit. Thus these eight cases cannot reproduce the 1.33m time-projection crossover. Separate [large native 2×2 factorial](HISTORICAL_FACTORIAL_REDUCTION_20261103.md) supplies that evidence.

An adaptive planner must distinguish estimated costs from *hard* memory admission:

```text
Nright_est = model estimate from SQL CBO (892 here)  [NOT a safe cap]
Nright_bound = conservative physical / snapshot bound that covers all
               input rows after merging parts, in-flight data, and chosen
               TimeRanges path (8192 for this frozen 1-part example)
Mhash_bound = Nright_bound * conservative_bytes_per_hash_entry
              + right_columns + id_set + collector + probe_buffers
allow_hash only if complete upper bound fits BOTH per-query memory limit
                and process-wide RSS headroom under current concurrency
                with a validated safety margin;
otherwise use ordered merge or another verified bounded path.
```

This simple `Nright_bound` from `system.parts` is a candidate for a *frozen, fully materialized, single-node* fixture only. It is **not yet proven** sound for concurrent inserts, merge races, partition TTL, late samples, multi-shard reads, retention, memory allocator overhead or tenant ACL. A production-ready bound must take table snapshots and concurrent activity into account; it must not treat old metadata counts as current active IDs.

If a projection changes required `id` ordering, add sorting work/peak to the comparison. Prior native factorial showed input row reduction of 16.6–16.8% alongside 32.7–36.2% slower wall time with `AUTO+merge`, showing a single estimate of read rows is insufficient to select the physical plan.

## Falsifiable next gates (not claims of implementation)

1. Confirm the row estimates on 1.33m/14d high churn and 400-node 3h-churn fixture, and on a dense steady 15s fixture. Compare SQL estimates to direct physical counts and actual native output cardinality, with raw DDL/query/source/binary hashes. A toy ratio must **not** become a global constant correction.
2. Measure `Mhash_bound` versus *actual* per-query peak and process RSS under 1/4/8 clients. Track hash map build rows, selected marks, projection chosen, sorting, right-side bytes and collector memory; fail on underestimation/OOM. Never fit solely to one fixture.
3. Expose the required downstream physical order in the estimator; add conservative fallback when exact order, statistics or memory upper bounds are unavailable.
4. Use E06 oracle and R08 semantics before C++ opt-in hook. No unconditional removal of TimeRanges and no direct TAGS deletion. Keep the source's full sorting merge default.

**Result:** correctness and plan capture PASS; currently demonstrated **estimation failure**, not an optimized plan. No code path for automatic native CBO was changed; master/production untouched, no scheduled workflow enabled and no other active CI cancelled.
