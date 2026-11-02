# ClickHouse TimeSeries v11: metric ID pruning, grouped avg and regular rate

Scope: lab branch only; pinned upstream ClickHouse source 52529f0c711566d696917678bf15e7068772fdde; VictoriaMetrics v1.134.0. This is an experimental record, not a production-ready modification. All A/B/A builds use the same on-disk inputs inside a run. ClickHouse builds in these research runs use O1; do not extrapolate their absolute performance to optimized production binaries.

## Reproducible findings

Four-metric Jenkins dataset: 282,036 target series/samples plus 846,108 unrelated samples in three other metric families in the SAME TimeSeries samples target. The independent events table has only Jenkins source data. Regular series workload: 20,000 series × 120 samples. All listed query values were checked against the event oracle or the original ClickHouse implementation.

| Path | Test | Baseline recheck p50 | Candidate p50 | Memory p95 baseline → candidate | Decision |
|---|---|---:|---:|---:|---|
| Batched array rows in AggregateFunctionTimeseriesBase | Jenkins all (run 41411073400) | 762ms | 850ms | 548 → 549 MB | REJECT: slowdown |
| Batched array rows | Dense sum(rate) | 111ms | 123ms | 76.5 → 76.5 MB | REJECT |
| Late tags JOIN without sample metric prefilter | Jenkins all, four metric families (run 41410791419) | 1390ms | 1525ms | 505 → 289 MB | REJECT as general plan |
| Late tags JOIN with canonical metric ID range | Jenkins all, four metric families (run 41411788979) | 1398ms | 632ms | 507 → 203 MB | CANDIDATE: 2.21×, canonical IDs assumption |
| Late tags JOIN with canonical metric range plus residual tags-ID lookup | Jenkins all, four metric families (run 41412500210) | 1540ms | 673ms | 510 → 199 MB | CANDIDATE: 2.29×, access and ID-type controls pending |

The array patch passed 13 PromQL tests, but 10-case A/B/A showed consistent latency regressions with no relevant memory gain. Preserve the patch as an experiment only.

In the run with four metric families, the original unfiltered late JOIN read 1,410,180 rows. Canonical ID range pruning reduced the reads to 564,072 rows; the 2.21× C++ candidate also passed all eight A/B/A query-correctness comparisons. The residual-ID C++ candidate read 846,108 rows: its extra tags query needs to scan matching metadata, but remains faster than the native wide PromQL on this dataset.

SQL-only crosscheck on the same four-metric dataset:
- Run 41411008407: unfiltered late-join p50 1919ms, canonical ID prefix expression 946ms, full tuple ID range 796ms, early tags JOIN followed by per-ID daily avg 599ms, original CH PromQL 1348ms, VM 534ms. All pointwise values equal to event oracle.
- Run 41411869762: range-only 777ms, **range plus residual lookup 796ms**, early tags JOIN 603ms, original CH 1370ms, VM 542ms. All values equal.
- Run 41412065305: immutable query-log audit. For the broad query, ID range changed late-join read_rows 1,410,180 → 564,072, memory p95 ~267MB → ~179MB. The original CH query used ~507MB; early-JOIN SQL about 145MB.
- The early-JOIN SQL still wins over the late range plan for most selective queries. No single universal plan is justified by these experiments.

The full native-selector and late grouping SQL, run 41409851008, did NOT improve latency (1669ms original vs 1634ms candidate on all stages; branch 618 vs 608ms; project 322 vs 336ms; 10 stages 319 vs 326ms). Query outputs equal.

For full dense PromQL rate, run 41412276475 enabled optimize_aggregation_in_order and optimize_read_in_order. EXPLAIN PIPELINE still showed the same AggregatingTransform/ReadPool operators; read_rows and memory remained unchanged. Baseline latency drifted during the test, so DO NOT claim an improvement from the timing variations. Do not enable either setting globally.

## Source: how ID indexing and sample alignment work

Canonical default v11 TAGS id: Tuple(UInt64, LowCardinality(UUID)), first component sipHash64(metric_name), second component a UUID hash of the complete label set. References: TimeSeriesIDGenerator.cpp, TimeSeriesIDRange.cpp and gtest_normalize_time_series_definition.cpp.

The TAGS table has native tags_idx text/token postings to ROW positions inside a MergeTree part, not directly to series IDs. TAGS rows contain metric_name, ID and Map tags. The SAMPLES table is ordered by (id, bucket) and stores physical Array(Tuple(timestamp,value)) samples. A physical 1-hour bucket is NOT a PromQL 5-minute rate window or an evaluation grid.

~~~mermaid
flowchart TD
  METRIC["metric name + labels"] --> ID["ID = (sipHash64(metric), uuidHash(labels))"]
  METRIC --> TAG["TAGS: metric_name, id, tags"]
  TAG --> INDEX["tags_idx: token to per-part tag row positions"]
  INDEX --> TAG
  ID --> S["SAMPLES: id, physical bucket, samples[]"]
  ID --> PREFIX["Fast range: (hash(metric), UUID-min..UUID-max)"]
  PREFIX --> S
  TAG --> RESIDUAL["Residual IDs: matching metric with noncanonical first component"]
  RESIDUAL --> S
  S --> WINDOW["Dedup samples by id/timestamp; assign evaluation window"]
  WINDOW --> PERID["avg per series per window"]
  PERID --> JOIN["JOIN TAGS FINAL on ID, exact metric check"]
  JOIN --> STAGE["avg per stage per grid step"]
~~~

The canonical ID range is not guaranteed for tables with a changed default expression, external targets, or older data created under a different layout. The SQL/C++ range-only path is deliberately *lab only*. The residual-ID query asks TAGS for matching IDs outside that range, so it preserves such values when tags are complete and available. This still has the same direct-inner permission/row-policy limitations as the original late-join prototype.

## Important C++ guard and read-path details

The current direct-inner C++ prototype is eligible only if:
- TimeSeries engine is version 11;
- samples and tags targets are owned inner tables;
- recent samples and time-range targets are absent;
- samples type contains Float64 at the supported DateTime64 scale;
- the parsed PromQL AST is a very specific root form: avg by(stage)(avg_over_time(exact_metric[window])), disjoint windows and compatible grid. Explicit branch/project/stage matchers are NOT eligible, so those other A/B/A query differences are unrelated to the specialized path (they take the default converter). Do not attribute them to the optimization.

The C++ type guard candidate checks TAGS id_type == Tuple(UInt64, LowCardinality(UUID)); nonmatching ID types must use the original converter. The newer residual-ID lookup is intended for noncanonical VALUES of the same tuple type. The type-guarded binary and its adversarial validation have their own CI runs; do not mark their status successful without inspecting the completed runs.

**Security gate**: direct queries to inner targets must not bypass the outer table's access semantics or row policies. All latency tests here run a single unrestricted benchmark account. Before production, use an approved engine-internal read path that checks the user's outer table access, storage row policies and target ownership. The current direct-inner SQL should NOT be enabled by default.

## Small-table cost guard and index-granule evidence

Source level: IStorage::totalRows(ContextPtr) supplies an optional part-metadata row count for a samples target. A lab-only C++ cost guard now uses this estimate and keeps the unchanged PromQL converter for target tables with fewer than 16,384 sample rows. This threshold is an experiment, not a production cost model. Patch .github/ts-lab-pending/apply_direct_inner_safe_size_guard_cpp.py; build 41413900299, mixed-metric A/B/A 41413945372, adversarial corner A/B/A 41413952829 (statuses require final CI review).

Why it is needed: 41413638122 verified ten adversarial corner-case outputs for the type-guarded C++ candidate, including duplicate timestamps, missing labels, 6h/36h windows, regex, offsets and overlapping windows. But on 210 short series the guarded broad 1d grouped avg was 18.8ms p50 versus 13.3ms baseline recheck; 6h case 17.9 vs 11.3ms. The specialized plan is not universally faster. The small-table threshold should prevent the overhead on these inputs, while retaining the wide Jenkins path.

Existing sample-granularity experiment 41354012551 already disproves a simple smaller-granule fix. Decreasing samples_index_granularity from 256 to 8 reduced stage10 read_rows ~562K → 327K, but increased p50 ~88ms → 204ms. The project-specific query similarly dropped 564K → 370K read rows but grew 92ms → 283ms. Additional index marks and small scattered reads can cost more than the skipped row decoding. Do not globally lower the index granularity without a new per-ID seek design.

## Remaining acceptance gates

1. The type-aware ID guard passed canonical, shifted tuple and UInt64 cases (41413078542). Extend correctness testing Verify exact labels, timestamps and numbers against original C++. Include nondefault id_generator/default expressions, missing labels, regex, overlaps, duplicates, out-of-order timestamps, back-delta, 15s HA and 14-90d windows.
2. Reduce expensive per-series rate grids or fuse the grouped rate operation. Avoid a broad configuration setting change: rate source already has four concurrent AggregatingTransform/ReadPool streams in full PromQL.
3. Re-run the accepted guarded implementation under O2/O3 with production-like cardinality, 69% late samples, 14-90d retention and ingest/merge CPU, part count, memory and S3/storage cost. Compare exact semantics with VictoriaMetrics; the observed MetricsQL rate behavior may differ from PromQL.

## Source, patches, CI

- C++ array candidate: .github/ts-lab-pending/apply_array_batch_runs_cpp.py, run https://github.com/moleus/ClickHouse/actions/runs/41411073400
- Late JOIN mixed families: https://github.com/moleus/ClickHouse/actions/runs/41410791419
- Canonical metric-ID range C++: .github/ts-lab-pending/apply_direct_inner_late_tags_metric_range_cpp.py, build 41411743589 and A/B/A https://github.com/moleus/ClickHouse/actions/runs/41411788979
- Complete residual-ID C++: .github/ts-lab-pending/apply_direct_inner_late_tags_safe_id_range_cpp.py, build 41412428782 and A/B/A https://github.com/moleus/ClickHouse/actions/runs/41412500210
- Type guard: .github/ts-lab-pending/apply_direct_inner_safe_id_type_guard_cpp.py; build 41412903481 SUCCESS and 13 PromQL tests, ID layout test 41413078542 SUCCESS (canonical, shifted tuple, UInt64 exact). The guarded C++ A/B/A run 41413630390 passed all 8 output checks; broad Jenkins 1363ms baseline repeat vs 887ms candidate, memory ~507MB → 200MB, but dense-rate timings also changed on the supposedly unchanged path, so consider the latency estimate noisy.
- Metric ID prefix/range SQL: .github/ts-lab-bench/bench_jenkins_metric_id_range.py, 41411008407
- Residual lookup SQL: .github/ts-lab-bench/bench_jenkins_metric_id_range_safe.py, 41411869762
- Read rows and memory audit: 41412065305
- In-order full-PromQL rate: 41412276475
- Existing storage drawings: .github/ts-lab-bench/TIMESERIES_V11_STORAGE_INDEX_ALIGNMENT_20261102.md

No work was merged to master/upstream; all source and workflows are on the lab branch.
