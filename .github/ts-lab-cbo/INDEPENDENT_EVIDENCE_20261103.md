# ClickHouse TimeSeries cost and statistics: verified evidence, 2026-11-03

Scope: isolated fork `moleus/ClickHouse`, branch `lab/ts-cbo-adaptive-20261103`.
Source pinned to `52529f0c711566d696917678bf15e7068772fdde`.
No production data or master changes. No scheduled GitHub workflows enabled.
The O2 binary is SHA-verified from Actions run 41467151457. The Jenkins fixture
is SHA256 `8f3a36846f80d6be5a8604d142e934399f2687094766c57bf105e43977f52858`.

## 1. Correlated stage/project labels

Run: https://github.com/moleus/ClickHouse/actions/runs/41536188291 (PASS).
Script: `.github/ts-lab-cbo/bench_correlated_labels.py`.

A 152,000-series synthetic fixture uses `j = i % 152`,
`stage = j % 38`, `project = j % 8`. Each series has one sample.
Fixture SHA256: `d305b1b79ad71177f9f5221dd044facb79320af8c3c2c6a2fcb636b1095625bb`.
There are 240/240 query-log records. Native PromQL produced 2,069 points;
Map and dedicated `stage, project` columns had the same labels, timestamps and
values (maximum absolute difference 0).

| Filter, full 14 days | Actual TAGS IDs | Estimated TAGS rows with Map | Estimated TAGS rows with columns |
| --- | ---: | ---: | ---: |
| stage=7 and project=0 (impossible) | 0 | ~8,280 | 250 |
| stage=6 and project=0 (correlated) | 1,000 | ~8,280 | 250 |
| stage=7 | 4,000 | ~25,080 | 2,000 |
| project=0 | 19,000 | ~25,080 | 9,500 |
| whole metric | 152,000 | 76,000 | 76,000 |

The native JOIN plan estimated 82,760 output rows for either two-label Map
predicate and 2,500 for either dedicated-column predicate, despite actual
outputs of 0 and 1,000. The table estimate is not an actual result.

The dedicated-column TAGS part used 6,465,735 bytes, versus 5,584,491 bytes
for Map (+15.8%). This storage cost did not prove a native query speedup:
the single native run was 466.104 ms for Map and 461.796 ms for columns.
Reject unstable cases with >30% A/B/A baseline drift.

Cause verified against pinned sources:
- `StatisticsUniqV2.cpp` collects all LowCardinality dictionary entries,
  including an unused default. Constant `metric_name` has true NDV 1,
  but a stored UniqV2 estimate of 2.
- `ConditionSelectivityEstimator.h/.cpp` uses an unknown-predicate
  factor of 0.33. Independent column cardinalities cannot model correlated
  or impossible stage/project pairs.
- No usable Tuple-ID NDV in this binary; the join plan falls back to an
  approximate cardinality. A separate optional C++ patch is being compiled.
These causes can compound. Do not use estimated rows as ground truth.

## 2. LowCardinality NDV curve

Run: https://github.com/moleus/ClickHouse/actions/runs/41536759462 (PASS).
Script: `.github/ts-lab-cbo/probe_lowcard_curve.py`.
20 controls: String vs LowCardinality(String), k=1,2,8,38,152, with and
without an actually used empty default key, 15,000 rows each.

For k=1,2,8,38, LowCardinality estimates k+1 when its default is unused,
while String reports k. When the default is used, LC estimates k, as
expected. At k=152, sketch approximation adds +/-1 differences.
For k=1, true self-join pairs are 225,000,000, while LC plan estimates
112,500,000 because NDV is 2. Both physical plans and exact group counts
are saved. Self-join is EXPLAIN-only; no large pair set was materialized.

## 3. Highly skewed label distribution

Run: https://github.com/moleus/ClickHouse/actions/runs/41537465947 (PASS).
Script: `.github/ts-lab-cbo/probe_skew_selectivity.py`.
100 distinct labels in 100,000 rows, either 90% or 99% with value `hot`.
String UniqV2 estimate NDV 100; LC estimate NDV 101.

For String with 90% hot labels: the planner estimates the *same* 10,000
JOIN output rows for `hot` (actual pair count 8,100,000,000) and
`cold-1` (actual 10,201). With 99% hot: estimated 10,000 for `hot`
(actual 9,801,000,000) and for `cold-1` (actual 121).
Actual pair counts are exact `frequency^2`, validated from group counts.
The quadratic JOIN was NOT executed; these are estimated vs mathematical
cardinalities, not measured runtime.

Pinned `ColumnStatistics::estimateEqual` uses `uniq_v2` as a uniform
frequency fallback. It uses `CountMinSketch` earlier if available, and
TDigest for numeric columns with fewer than 2,048 distinct values.

Run https://github.com/moleus/ClickHouse/actions/runs/41538082297 (PASS as
capability probe): `countmin` is not available in the pinned lab binary.
ALTER returned `Unknown statistic type 'CountMinSketch'` for all four
String/LC skew controls. No CountMin speedup claim is allowed.
An independent numeric-ID TDigest test is at
https://github.com/moleus/ClickHouse/actions/runs/41538429974;
read that run before drawing conclusions.

## 4. TimeRanges max_time projection

Run: https://github.com/moleus/ClickHouse/actions/runs/41537651194 (PASS
after correcting system.projection_parts schema, QueryFinish retries and
warmup count). Script `.github/ts-lab-cbo/bench_time_projection.py`.

TimeRanges is an AggregatingMergeTree; add the projection only with
`deduplicate_merge_projection_mode='rebuild'`. The projection is
`(SELECT id,min_time,max_time ORDER BY max_time,id)`.
In the canonical 1.33m fixture, the projection stored 1,330,000 rows
(34,268,414 bytes in system.projection_parts). The paired A/B/B/A comparisons
used 5 warmups and 4 measured repetitions per phase. All reported baseline
drift values were below 30%.

| Query and range | No projection | Force projection | Relative speedup | Read rows base -> projection |
| --- | ---: | ---: | ---: | --- |
| Direct COUNT 1d | 15.25 ms | 5.5 ms | 2.77x | 1.33m -> 101,200 |
| TAGS JOIN COUNT 1d | 107.0 ms | 77.5 ms | 1.38x | 2.66m -> 1.4312m |
| TAGS JOIN COUNT 3d | 221.5 ms | 185.75 ms | 1.19x | 2.66m -> 1.6196m |
| TAGS JOIN COUNT 7d | 322.25 ms | 325.0 ms | 0.99x | 2.66m -> 1.9964m |
| TAGS JOIN COUNT 14d | 417.5 ms | 436.25 ms | 0.96x | 2.66m -> 2.66m |

**Not a full native PromQL gain**: these are direct SQL diagnostics. A
cost-based choice should avoid forcing projection on wide windows. Full
EXPLAIN, read marks, query-log CPU, RSS and parts are in uploaded artifacts.
The cost-mode vs none A/B/B/A run is
https://github.com/moleus/ClickHouse/actions/runs/41538293956.

Correctness control run:
https://github.com/moleus/ClickHouse/actions/runs/41537846983 (PASS).
Script `.github/ts-lab-cbo/probe_time_projection_updates.py`.
Inserted the same 1,024 series into two TimeSeries tables. One has the
projection. Tests run before and after each incremental update and FINAL
merge, including older out-of-order samples. Five phases produced 4,8,8,9,9
native PromQL points with identical labels and timestamps, and maximum
absolute numerical error below 6.3e-13. Projection and ordinary paths
agreed for all four time predicates. The final merged TimeRanges had
256 recently updated IDs, 512 old IDs and 1,024 total IDs. Before merge,
raw row counts included duplicate records; tests checked this rather than
assuming every row was a distinct series. This is a small control, not proof
of all production update/replication behavior.

Initial failures remain in Actions artifacts: 41536526138 queried a
nonexistent `projection_name` in system.projection_parts; 41536988345
lost a QueryFinish during log extraction; 41537095237 had 42.25% baseline
drift in the 1d direct read. They were fixed and rerun; do not report their
timings as verified performance claims.

## 5. Native C++ and next step

Two independent pinned-source builds are still in progress; preserve both
and their ccache: https://github.com/moleus/ClickHouse/actions/runs/41531656434
(opt-in native JOIN algorithm override) and
https://github.com/moleus/ClickHouse/actions/runs/41535183046
(combined override plus Tuple UniqV2 validator).

Once a verified patched binary artifact is available, start the existing
`ts-lab-cbo-native-join-20261103.yml` four-way full native PromQL A/B/B/A
suite with exact expected timestamps/labels/numbers. Compare variants
within the same O1 binary. Do not compare patched O1 against O2 as a
single-variable performance change. The workflow is intentionally
workflow_dispatch-only until a compiled binary exists. Do not start it early.

Other independent work: measure actual CBO cost selection rather than
forcing projections, test TDigest for frequent numeric label surrogates,
and check whether a small number of stats-bearing columns can repair
estimates without expensive high-cardinality per-label statistics.
Do not enable any scheduled GitHub workflow, edit master, use production,
or cancel an active build.

## 6. TDigest frequency results on numeric category IDs

Run: https://github.com/moleus/ClickHouse/actions/runs/41538429974 (PASS).
Script: `.github/ts-lab-cbo/probe_tdigest_skew.py`.
This is a *numeric surrogate* test, not a replacement for the original
String-label TimeSeries interface. The source's `ColumnStatistics::estimateEqual`
consults TDigest first when the numeric column has <2,048 distinct values.
The test used UInt16 category IDs and 100,000 rows.

| Hot population | Actual hot records | Estimate of filtered hot input with TDigest | Actual cold ID=2 | Estimated filtered cold input |
| --- | ---: | ---: | ---: | ---: |
| 90% | 90,000 | 90,000 | 101 | 0 |
| 99% | 99,000 | 99,000 | 11 | 0 |

TDigest corrected the heavy-hitter input estimate, but underestimated rare
values to zero. For a self-JOIN with an equality on the label, the planner
still used NDV=100 and estimated 81 million JOIN rows for the 90% hot label
versus 8.1 billion mathematical pairs (100x low). For 99% hot it estimated
98.01 million versus 9.801 billion actual pairs. Filtered input cardinality
and JOIN output cardinality are different problems. The source does not infer
that the filtered JOIN key has only one distinct value.

The TDigest stats added 1,955 bytes to a 100,000-row part at 90% hot and
1,910 bytes at 99% hot. This is only the measured part-size difference, not a
cluster-wide memory or ingest-cost claim. High-cardinality/string key support,
rare-value estimates, type conversion and time stability need separate checks.
The quadratic JOIN itself was not executed.

## 7. Cost-mode projection plans, direct physical read

Run: https://github.com/moleus/ClickHouse/actions/runs/41538293956.
The READ matrix completed successfully. This is the natural planner choice
(`optimize_use_projections=1`), NOT `force_optimize_projection=1`.

| Time window | No projection median | Natural cost mode median | Chosen plan | Read rows |
| --- | ---: | ---: | --- | --- |
| 1d | 12.25 ms | 5.0 ms | time_by_max | 101,200 |
| 3d | 11.25 ms | 5.25 ms | time_by_max | 289,616 |
| 7d | 11.75 ms | 9.25 ms | time_by_max | 666,448 |
| 14d | 12.25 ms | 11.0 ms | base table | 1,330,000 |

For 14d, EXPLAIN explicitly says the projection needs 163 marks and
has no useful sort advantage. It selects the base table, avoiding the
forced projection regression. The 1d/14d baseline drift was 28.57%, close
to the 30% rejection guard; these wall-time differences need independent
confirmation. The 3d/7d baseline drift was 4.44%/4.26%, respectively.
This is a direct COUNT diagnostic; full native PromQL benefit is unproved.
The JOIN matrix of the same run must be checked separately.

## 8. Cost-mode projection JOIN result

Run: https://github.com/moleus/ClickHouse/actions/runs/41538293956
(full 2-job matrix PASS). The JOIN matrix used 1.33m TimeSeries IDs, 5 warmups
and 4 measured queries per A/B/B/A phase. Query-log count results matched in
all eight conditions. Cost selection was compared to `optimize_use_projections=0`.

| Range | Base time | Cost-based time | Speedup | Drift | Selected physical TimeRanges read |
| --- | ---: | ---: | ---: | ---: | --- |
| 1d | 177.75 ms | 98.5 ms | 1.80x | 5.91% | time_by_max |
| 3d | 228.75 ms | 185.25 ms | 1.23x | 4.59% | time_by_max |
| 7d | 340.25 ms | 304.75 ms | 1.12x | 14.55% | time_by_max |
| 14d | 407.25 ms | 406.0 ms | 1.00x | 0.86% | base table |

Unlike the forced-projection 14d test (which regressed), the natural
optimizer chooses the base table at 14d, as verified in full EXPLAIN.
All four paired drift checks passed. The 1d and 3d results are strongest;
7d has more variance and a smaller gain. The direct SQL test does not
establish a full native PromQL gain. All physical plans, CPU/read metrics,
marks, QueryFinish entries and original ZIP evidence remain attached to
the run. Two original C++ native JOIN builds are still active.
