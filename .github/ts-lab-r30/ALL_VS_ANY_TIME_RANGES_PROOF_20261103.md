# R30 — TimeRanges ALL LEFT → ANY LEFT membership JOIN

Date 2026-11-03. Status: **SOURCE-SUPPORTED HYPOTHESIS + SQL AND C++ PROTOTYPES; native C++ performance not measured**. Canonical claim [R30](../ts-lab/claims/roots/R30.json), [experiment](../ts-lab/claims/R30-time-ranges-any-semi-join-v1.json), owner `chatgpt-e03-r30-any-time-ranges-20261103`.

## Why this is a different optimization than R06 and R05

[Source-pinned v11 TimeSeries description](https://github.com/moleus/ClickHouse/blob/52529f0c711566d696917678bf15e7068772fdde/src/Storages/StorageTimeSeries.cpp#L1168-L1183): **every insert** records one time range per written series. The inner TimeRanges target is `AggregatingMergeTree ORDER BY id` and merges multiple rows per series in background ([source docs](https://github.com/moleus/ClickHouse/blob/52529f0c711566d696917678bf15e7068772fdde/src/Storages/StorageTimeSeries.cpp#L1442-L1453)). While inserts are ahead of merges, one logical ID can have multiple matching physical TimeRanges rows.

[StorageTimeSeriesSelector::addTimeRangesTableJoin](https://github.com/moleus/ClickHouse/blob/52529f0c711566d696917678bf15e7068772fdde/src/Storages/StorageTimeSeriesSelector.cpp#L486-L559) currently generates **ALL LEFT JOIN** with `SELECT id, 1 AS matched FROM TimeRanges WHERE time-overlap AND optional metric-ID ranges`, and the consumer is [SAMPLES `id IN (subquery)`](https://github.com/moleus/ClickHouse/blob/52529f0c711566d696917678bf15e7068772fdde/src/Storages/StorageTimeSeriesSelector.cpp#L1274-L1350). This is a **membership**, not a data-valued aggregation from the right side; each matched right row has the same literal flag `matched=1`.

R05 studies physical SAMPLES ID/mark pruning and does not own JOIN strictness. R06 studies CBO/order and memory. R30 is an independent semantic and physical strictness question, with no new index/layout, no new join order and no replacement of TimeRanges storage.

## Algebraic equivalence claim, limited to ID membership

For a left TAGS row t with ID `i`, let `r_i` be the number of *filtered* TimeRanges rows with the same ID.

```text
Source JOIN:  LEFT ALL (SELECT id, 1 matched FROM TimeRanges WHERE filter) USING(id)
Candidate:    LEFT ANY (same SELECT) USING(id)
Post filter:  matched=1 OR id NOT IN metric_hash_ranges
Next:         SELECT timeSeriesStoreTags(...) -> id IN <set> -> SAMPLES
```

- If `r_i > 0`, the source emits `r_i` identical copies of the *same left row* before `timeSeriesStoreTags`; the candidate emits one. Both retain ID `i` because the right flag is always 1 for a match.
- If `r_i = 0`, both use the left-join default right flag. The original ID-range exception expression remains unchanged. Whether the left row is included depends on that **same** filter on both paths.
- `IN` set construction removes duplicate IDs. Therefore the candidate ID set is mathematically identical to the source ID set **for an unchanged left input**, including before merges, if the right-hand filtered rows have the same literal `matched=1`. This is a set proof, not a proof that all stateful tag operations are equivalent.
- The upper bound on duplicate *left output* rows for a single matching ID is `r_i:1`. When `r_i=1`, no reduction is possible. The right-side table must still be read/sorted; do **not** claim `r_i`-fold query speedup from this bound.

Remaining semantic gates: `timeSeriesStoreTags` is stateful; duplicate left TAGS rows and custom ID generator collisions can affect group registration order. Test full native tag labels/group outputs, multiple metric IDs with different generators, missing labels, negative/regex selectors, updates/merge races, empty and time-boundary windows, late inserts, repeated timestamps, NaN/Inf, readonly/tenant access. Exact ID set and SAMPLES row equality are necessary but **not sufficient** for production promotion.

## Work initiated

- [Pre-registered claim](../ts-lab/claims/R30-time-ranges-any-semi-join-v1.json).
- [SQL differential A/B/B/A probe](bench_timeranges_any_sql.py): one isolated ClickHouse server, same pinned O1 lab binary with source intact. Stop merges; stream 16 batches over 4096 identical series IDs plus before/after nonmatching cohorts; verify distinct ID set, `SELECT id,samples`, physical TimeRanges multiplicity, and nested `EXPLAIN` strictness. Four phases ALL/ANY/ANY/ALL with 3 warmups + 4 measured each, query-log wall/CPU/memory, control-drift gate. **This is NOT modified native PromQL**.
- [Isolated source-pinned workflow](../workflows/ts-lab-r30-timeranges-any-sql-probe-20261103.yml) [run 41713474075](https://github.com/moleus/ClickHouse/actions/runs/41713474075) launched; do not infer pass before report is inspected.
- [Optional C++ patch script](apply_any_join_cpp.py) changes only the single `table_join->strictness` in the source function when explicitly invoked with `--apply`. It defaults to dry-run, checks the source context and is **not applied to any shared source checkout or production**. Unit tests constrain its one-site diff. No patched build started while [E01 optimized stock R29 build](https://github.com/moleus/ClickHouse/actions/runs/41685467438) is running.

## Strict gates before production-oriented work

1. Prove physical `r_i>1` with `SYSTEM STOP MERGES`, not inferred from write count. Record number of TimeRanges parts, rows, distinct IDs and actual ALL/ANY join output rows. If no duplicate rows, this candidate has no basis.
2. Compare complete distinct ID sets and SAMPLES `(id,timestamp,value)` arrays on exactly the same frozen parts; test both positive and negative filters, before/after window cohorts.
3. Confirm `FullSortingMergeJoin` supports `Strictness: any` and does not silently select a different algorithm for the diagnostic SQL. If unsupported, mark REJECT or redesign; never replace with unbounded hash just to make ANY work.
4. Reconcile unmatched-ID generator exception and stateful collector semantics in **full modified native binary**, matching compiler/link flags and fixture to the untouched control. No gain claim from SQL-only A/B/B/A.
5. Real native A/B/B/A high-churn and repeated-stable samples, 1/3/7/14d, 1/4/8 clients, same-source C++ baseline vs only one strictness delta. CPU, wall p95/p99, RSS and ingest+merge effect. Negative controls after full merge must show near-zero benefit.
6. Reject if CPU/RSS regress, semantic oracle changes, baseline drift >25% or any OOM. Use a new isolated build only after E01 R29 source parity baseline is established; do not cancel other builds or share their mutable ccache directories.

No `master`, production, upstream, R05, R07, R28 or E01 modification.
