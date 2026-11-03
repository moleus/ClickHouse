# R31 — replace native TimeRanges metadata JOIN with an ID membership set?

Date: 2026-11-04. **Status: candidate under SQL test; no native C++ implementation or performance decision.** Owner: `chatgpt-r31-native-metadata-in-semijoin-20261104`, [canonical root](../ts-lab/claims/roots/R31.json), [claim](../ts-lab/claims/R31-timeranges-membership-in-semijoin-v1.json).

## Pinned architecture and the opposing costs

[Source pinned selector](https://github.com/moleus/ClickHouse/blob/52529f0c711566d696917678bf15e7068772fdde/src/Storages/StorageTimeSeriesSelector.cpp#L485-L555) adds:

```sql
SELECT timeSeriesStoreTags(id,tags,'__name__',metric_name)
FROM TAGS
ALL LEFT JOIN (
  SELECT id, 1 AS matched
  FROM TimeRanges
  WHERE time_range_conditions [AND id IN chosen_metric_ranges]
) AS time_ranges_subquery USING(id)
WHERE tag_label_predicates
  AND (time_ranges_subquery.matched = 1 OR id OUTSIDE chosen_metric_ranges)
```

The same C++ source **explicitly says** the forced full-sorting-merge join uses both tables' ID order, constructs *no hash set*, and can stop reading once enough rows have passed. This property is essential for low-latency selective queries and bounded memory. Changing to SQL IN **might make a large materialized right-ID set**, or consume all matching TimeRanges, so it is unsafe to assume that an IN filter is always faster or smaller.

R30 has already proved that when TimeRanges has 16 unmerged physical rows per ID, `ANY LEFT JOIN` cuts output multiplication by 16× and can reduce direct ID SQL wall/CPU; after `OPTIMIZE ... FINAL` the effect collapses to 1×, and ANY raises query-memory tracker values at multiplicity 1/2/4/8. R31 does **not** replicate R30's strictness scope: it tests a different plan family, a set-membership filter.

## Exact transformation and semantic obligations

The generated native SQL includes a right SELECT `SELECT id, 1 AS matched FROM TimeRanges WHERE P(id,time)` and the downstream criterion
`or(equals(time_ranges_subquery.matched,1), id_outside_metric_ranges)`.
R31 rewrites this *without dropping any other predicate*:

```sql
SELECT timeSeriesStoreTags(id,tags,'__name__',metric_name)
FROM TAGS
WHERE tag_label_predicates
  AND (in(id,(SELECT id FROM TimeRanges WHERE P(id,time)))
       OR id_outside_metric_ranges)
```

For each TAGS row, the original *membership condition* is true precisely when the same ID has at least one right row after the right-side time and metric-ID filters, or its ID falls outside the configured metric-ID hash range. SQL IN on the identical ID tuple type captures that existence test. Duplicate right-side rows do not change the set.

This proves membership equivalence **at the logical ID-set level** under the source's right literal `matched=1`, not full native query equivalence. Full label/group aggregation via stateful `timeSeriesStoreTags`, collision error-path, absence semantics, cross-metric ID generators, readonly ACL, empty/negative/regex selectors, bad timestamps and concurrent writes must still be tested. To preserve source-dependent semantics, R31 parser fails closed if the generated source has any unexpected JOIN alias, right matched value, or other RHS reference. It retains the original `OR id_outside_metric_ranges` **verbatim**.

## Differential experiment

- [Executable R31 SQL probe](bench_membership_in.py), [parser unit tests](test_membership_in.py), [isolated workflow](../workflows/ts-lab-r31-membership-in-20261104.yml).
- [CI #41719952453](https://github.com/moleus/ClickHouse/actions/runs/41719952453) launched on identical SHA-verified old O1 JOIN-override source and binary (source `52529f0c...`, executable SHA256 `3990e373b43396d0faafd8feffe5dd6514bd57c520ec12e89b8e5796d64e6221`).
- Two deterministic disjoint TimeSeries tables: `k=1` and `k=16` unmerged writes for 2048 stable IDs, plus before/after-window controls. Scoped `SYSTEM STOP MERGES <new TimeRanges>` before INSERT; exact physical rows/IDs gate; keep both sides' natural TAGS/SAMPLES.
- For `stage`, `project`, `whole`: exact sorted distinct ID-set equality; entire `(id,samples)` output equality, SQL EXPLAIN per mode, and positive nonempty baseline. Any mismatch => **CORRECTNESS_FAIL**.
- For project: `ALL/ANY/IN/IN/ANY/ALL`, 3 warmups+4 measured per phase, 25% drift gate for each mode. QueryFinish wall, `OSCPUVirtualTimeMicroseconds`, `read_rows`, `read_bytes`, query-level memory. A new process is **not** created between modes; matched in-process phase comparisons control obvious warmup/order bias but not RSS retention.
- One job only; no large compile, no workload-scheduler change, no other job cancellation.

## Decision gates

1. Confirm exact ID/SAMPLES hashes across three strategies, and check physical right-side scanned rows/parts. If the IN expression changes output, stop with **CORRECTNESS_FAIL**.
2. Direct IN-set SQL may have higher CPU or memory even when RIGHT TimeRanges duplicates exist. If it loses to ANY, log the negative and **do not** build an IN-based native patch.
3. If it wins direct SQL with bounded tracker memory, use EXPLAIN/processor logs to confirm physical plan, and test on 1.33m/14d/400-node high-churn before attempting C++.
4. The original merge path is a streaming plan. The upper bound for IN-set peak must include the distinct right-ID count plus hash/set overhead, scheduler concurrency and process RSS. CBO cardinality estimation was previously wrong by 9.18× on a small fixture; no plan based solely on `EXPLAIN rows estimated` is safe.
5. No source-default promotion or attribution to production without E06/R08 full native oracle and 1/4/8 concurrent-client RSS/OOM tests. E01's existing R29 optimized stock build is protected.
