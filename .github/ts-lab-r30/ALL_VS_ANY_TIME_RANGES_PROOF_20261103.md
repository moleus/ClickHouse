# R30 — TimeRanges ALL LEFT → ANY LEFT membership JOIN

Date 2026-11-03. Status: **SQL_EQUIVALENCE_AND_DIRECT_COST_PASS**, native C++ performance **not measured**. Canonical claim [R30](../ts-lab/claims/roots/R30.json), [experiment](../ts-lab/claims/R30-time-ranges-any-semi-join-v1.json), owner `chatgpt-e03-r30-any-time-ranges-20261103`.

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

## Completed pre-merge diagnostic: 16x row multiplicity; CPU and memory reduction

[CI #41714502297](https://github.com/moleus/ClickHouse/actions/runs/41714502297) **PASS**, immutable [SHA-verified full evidence](../ts-lab/evidence/R30-any-sql-premerge-41714502297.json). The earlier [INVALID_METHOD run #41713474075](../ts-lab/evidence/R30-any-sql-invalid-method-41713474075.json) stopped before comparison due unaliased storeTags output; corrected standalone-only alias `r30_id` avoids shadowing physical `id`. The candidate source C++ remains untouched.

Input: 4096 stable IDs × 16 chronologically distinct INSERT batches, 512 IDs with only before-window samples and 512 only after-window. `SYSTEM STOP MERGES` prevents physical range collapse. Full fixed source 52529f0c7115; O1 lab binary SHA `3990e373b43396d0faafd8feffe5dd6514bd57c520ec12e89b8e5796d64e6221`; fixture SHA `613b3b1f1835d46e2845ebc60ae073f3bc41818236a23a6ae885e0ff83bf34a5`. TAGS 5120 rows/IDs over 3 parts; TimeRanges **66,560 physical rows, 5120 IDs, 18 parts**. The 4096 hot IDs each have 16 right-hand rows before merges.

| Selector | ALL JOIN result rows | ANY JOIN result rows | Distinct ID set | Exact SAMPLES rows/timestamps/values |
|---|---:|---:|---:|---|
| stage | 16,384 | 1,024 | 1,024 equal | 16,384 rows, matching full hash |
| project | 32,768 | 2,048 | 2,048 equal | 32,768 rows, matching full hash |
| whole | 65,536 | 4,096 | 4,096 equal | 65,536 rows, matching full hash |

ClickHouse's standalone ID `EXPLAIN` rewrote the *physical* join direction and printed `Type: right` in both cases, but retained `Strictness: all` or `Strictness: any`, with `Algorithm: FullSortingMergeJoin`. The rewrite is not a hash-vs-merge confounder. **The SQL source logically remains LEFT**. Check actual source rows and native operator logs before attributing how much CPU comes from dedup, tag registration or join output materialization.

Direct ID-selector SQL, same binary and frozen parts, `ALL/ANY/ANY/ALL`, 3 warmups + 4 measured per phase, paired phase-median averages:

| Project metadata ID query | ALL | ANY | Difference |
|---|---:|---:|---:|
| QueryFinish wall | 73.75 ms | 50.50 ms | **-31.5%** |
| Query CPU | 94.259 ms | 70.044 ms | **-25.7%** |
| Query tracker peak memory | 26,716,442 B | 14,268,661 B | **-46.6%** |
| Read rows | 70,656 | 70,656 | 0 |
| Read bytes | 3,421,276 | 3,421,276 | 0 |

Controls: ALL A/A wall drift 0.68%, ANY A/A 1.98%, no query errors. The 8 per-variant measured wall samples are separated with no overlap (ALL 73–79 ms, ANY 50–52 ms), but all were executed on **one** GitHub runner. This is a robust SQL-stage gain for this fixture, **not a full native PromQL or production speedup**. The unchanged physical read counters support an output-multiplicity mechanism, not lower storage IO.

The first necessary condition for an actual production gain is high physical duplicate density. Next negative control must explicitly `OPTIMIZE TABLE TimeRanges FINAL` and compare when the right side has one row per logical ID. After that, only a matched C++ A/B/B/A with native labels/timestamp/value and multi-client memory checks can promote the patch.

## Verified negative control: after FINAL the 16x multiplier disappears

[CI #41715251318](https://github.com/moleus/ClickHouse/actions/runs/41715251318) **PASS**, independent [immutable SHA-verified evidence](../ts-lab/evidence/R30-any-sql-postmerge-41715251318.json). The same single ClickHouse O1 lab binary SHA `3990e373...`, exact fixture SHA `613b3b1f...`, and nested generated SQL from premerge were used before and after a scoped operation:

```sql
SYSTEM START MERGES default.`.inner_id.timeranges.<test UUID>`;
OPTIMIZE TABLE default.`.inner_id.timeranges.<test UUID>` FINAL;
SYSTEM STOP MERGES default.`.inner_id.timeranges.<test UUID>`;
```

Only the TimeRanges inner table was merged. TAGS and SAMPLES remained unchanged.

| Property | Before FINAL | After FINAL |
|---|---:|---:|
| Physical TimeRanges rows | 66560 | 5120 |
| Unique TimeRanges IDs | 5120 | 5120 |
| Active TimeRanges parts | 18 | 1 |
| ALL/ANY output multiplicity, stage | 16:1 | 1:1 |
| ALL/ANY output multiplicity, project | 16:1 | 1:1 |
| ALL/ANY output multiplicity, whole | 16:1 | 1:1 |

Exact sorted distinct ID sets and full `(id, samples)` outputs match for ALL versus ANY **and remain equal before versus after** for stage/project/whole.

The same-runner project ID SQL A/B/B/A gave:

| Direct SQL metric | Before ALL | Before ANY | After ALL | After ANY |
|---|---:|---:|---:|---:|
| Query wall median of paired phase medians | 84.25ms | 55.50ms | 23.00ms | 20.50ms |
| CPU | 104.176ms | 75.587ms | 25.862ms | 23.595ms |
| Tracked memory peak | 26.793MB | 14.268MB | 9.616MB | 9.959MB |
| read_rows | 70656 | 70656 | 10240 | 10240 |
| read_bytes | 3421276 | 3421276 | 883564 | 883564 |

Premerge ALL/ANY wall difference `28.75ms` (−34.1%), CPU difference `28.59ms` (−27.4%); postmerge wall difference `2.50ms`, CPU difference `2.27ms`, and ANY now has slightly **higher** peak tracked memory. ALL/ANY postmerge wall drift from mirrored phases was 4.35%/4.88%; 2.5ms is too small to conclude a stable gain. **Causal negative-control result is the collapse of physical and JOIN output multiplicity, not a proof of a global zero-cost cutoff.** Physical bytes scanned also shrink after FINAL, so pre/post absolute latency must NOT be attributed solely to JOIN strictness.

**Next bounded experiment:** [R30 duplicate-density 1/2/4/8/16 within-fixture matrix](../workflows/ts-lab-r30-duplication-curve-20261103.yml), [CI #41716512958](https://github.com/moleus/ClickHouse/actions/runs/41716512958), compares ALL/ANY A/B/B/A **within each frozen table** and requires exact cardinality, full sample output parity, per-mode drift; no compiled C++ and no performance claim until artifact inspection. Cross-level wall is not paired, so use within-level effects only.

## Strict gates before production-oriented work

1. Prove physical `r_i>1` with `SYSTEM STOP MERGES`, not inferred from write count. Record number of TimeRanges parts, rows, distinct IDs and actual ALL/ANY join output rows. If no duplicate rows, this candidate has no basis.
2. Compare complete distinct ID sets and SAMPLES `(id,timestamp,value)` arrays on exactly the same frozen parts; test both positive and negative filters, before/after window cohorts.
3. Confirm `FullSortingMergeJoin` supports `Strictness: any` and does not silently select a different algorithm for the diagnostic SQL. If unsupported, mark REJECT or redesign; never replace with unbounded hash just to make ANY work.
4. Reconcile unmatched-ID generator exception and stateful collector semantics in **full modified native binary**, matching compiler/link flags and fixture to the untouched control. No gain claim from SQL-only A/B/B/A.
5. Real native A/B/B/A high-churn and repeated-stable samples, 1/3/7/14d, 1/4/8 clients, same-source C++ baseline vs only one strictness delta. CPU, wall p95/p99, RSS and ingest+merge effect. Negative controls after full merge must show near-zero benefit.
6. Reject if CPU/RSS regress, semantic oracle changes, baseline drift >25% or any OOM. Use a new isolated build only after E01 R29 source parity baseline is established; do not cancel other builds or share their mutable ccache directories.

No `master`, production, upstream, R05, R07, R28 or E01 modification.
