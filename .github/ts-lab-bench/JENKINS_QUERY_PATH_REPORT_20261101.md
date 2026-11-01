# Jenkins build-stage duration: VictoriaMetrics 1.134.0 vs ClickHouse TimeSeries v11

**Date:** 2026-11-01. **Scope:** reproducible synthetic tests on GitHub-hosted runners. This is NOT an evaluation of the production VictoriaMetrics cluster or a production ClickHouse deployment.

## Source versions and exact semantics

- ClickHouse fork `moleus/ClickHouse`, pinned source [`52529f0c7115`](https://github.com/moleus/ClickHouse/tree/52529f0c711566d696917678bf15e7068772fdde), C++ `-O1 -DNDEBUG -g0`, most optional libraries disabled. **This is NOT a production-optimized C++ build.**
- VictoriaMetrics standalone **v1.134.0**, downloaded from pinned Actions artifact and verified by SHA256, `-search.disableCache`; normal comparable query tests use `-search.maxUniqueTimeseries=1000000` to avoid a cap disqualifying one engine. A separate experiment restores the **165000** cap.
- Three stores receive exactly the same labelsets, source timestamps and values: ClickHouse TimeSeries `Engine=TimeSeries`, VictoriaMetrics imported via `/api/v1/import/prometheus`, and native ClickHouse `MergeTree` table `jenkins_events` with one row per build-stage event.
- Sample type: a **single scalar observation per logical series**. Metric `jenkins_avs_build_stage_duration`, labels `stage, app, app_name, branch, build, jenkins_job, job, project, purpose, result`.
- Query semantics chosen for exact comparison:
  ```promql
  avg by(stage) (avg_over_time(jenkins_avs_build_stage_duration[1d]))
  ```
  evaluated at **UTC day boundaries** with **step=86400s**. It is equivalent, on this exact input, to:
  ```sql
  SELECT toStartOfDay(ts) + INTERVAL 1 DAY AS epoch,
         stage, avg(duration)
  FROM jenkins_events
  WHERE ts >= :first_day AND ts < :last_day_end
  GROUP BY epoch, stage;
  ```
  The SQL output is the independent reference: all stage names, evaluation timestamps and each value must match, not only p50 latency or total counts. Exact equality was tested across all 4 measured repetitions (tolerance rel=1e-8/abs=1e-7, actual worst abs error ~1e-13).

**IMPORTANT:** This test proves equivalence for *one sample per series*, explicit [1d] lookbehind and aligned daily evaluation. It **does not** prove that native SQL is a drop-in replacement for arbitrary MetricsQL/PromQL; especially not raw instant selectors, counters, `rate`, HA dedup, or delayed/out-of-order write handling.

## Source-level query path

### VictoriaMetrics v1.134.0

```text
MetricsQL parser
  -> default_rollup or avg_over_time[1d]
  -> indexdb per-date inverted index / MetricIDs
  -> matching MetricID set, maxUniqueTimeseries gate
  -> fetch/compress-decode values for selected series
  -> per-series rollup, avg grouped by stage
  -> 1,448 stage-series × 3 timestamps (3d dataset)
```

- [`lib/storage/index_db.go` lines 2220–2280](https://github.com/VictoriaMetrics/VictoriaMetrics/blob/v1.134.0/lib/storage/index_db.go#L2220-L2280): `searchMetricIDsInternal` uses a per-date inverted index when range is not global, checks the size of the matched ID set and returns the error when it exceeds the limit.
- [`app/vmselect/netstorage/netstorage.go` lines 230–340](https://github.com/VictoriaMetrics/VictoriaMetrics/blob/v1.134.0/app/vmselect/netstorage/netstorage.go#L230-L340): query values unpacked/processed by multiple local workers. The VM implementation does not require a dense ClickHouse `Array(Nullable)` for every series in the same way.
- [`app/vmselect/promql/eval.go` lines 279–286](https://github.com/VictoriaMetrics/VictoriaMetrics/blob/v1.134.0/app/vmselect/promql/eval.go#L279-L286): raw metric selector becomes `default_rollup`; [`rollup.go` lines 700–755](https://github.com/VictoriaMetrics/VictoriaMetrics/blob/v1.134.0/app/vmselect/promql/rollup.go#L700-L755): without an explicit window, `window=step` in this 1d-step test. This differs from ClickHouse PromQL instant lookback.

### ClickHouse TimeSeries v11, pinned source

```text
PromQL parser / SQL converter
  -> SELECT IDs from TS tags table (label filters)
  -> LEFT JOIN time_ranges on id; full_sorting_merge
  -> id IN (selected IDs), read bucketed MergeTree samples
  -> timeSeriesAvgToGrid per series
  -> avgForEach over nullable value grids, grouped by stage
  -> stage/timestamp results
```

- [`StorageTimeSeriesSelector.cpp` lines 490–665](https://github.com/moleus/ClickHouse/blob/52529f0c711566d696917678bf15e7068772fdde/src/Storages/StorageTimeSeriesSelector.cpp#L490-L665): building `SELECT id` from tags and `time_ranges` LEFT JOIN. [Lines 1160–1175](https://github.com/moleus/ClickHouse/blob/52529f0c711566d696917678bf15e7068772fdde/src/Storages/StorageTimeSeriesSelector.cpp#L1160-L1175) set `full_sorting_merge`; [lines 1220–1365](https://github.com/moleus/ClickHouse/blob/52529f0c711566d696917678bf15e7068772fdde/src/Storages/StorageTimeSeriesSelector.cpp#L1220-L1365) read samples with selected ID sets and time limits.
- [`applyFunctionOverRange.cpp` lines 179–195](https://github.com/moleus/ClickHouse/blob/52529f0c711566d696917678bf15e7068772fdde/src/Storages/TimeSeries/PrometheusQueryToSQL/applyFunctionOverRange.cpp#L179-L195): `avg_over_time` maps to `timeSeriesAvgToGrid`. [`applyOneArgumentAggregationOperator.cpp` lines 58–85](https://github.com/moleus/ClickHouse/blob/52529f0c711566d696917678bf15e7068772fdde/src/Storages/TimeSeries/PrometheusQueryToSQL/applyOneArgumentAggregationOperator.cpp#L58-L85): grouped `avg` maps to `avgForEach`.
- `system.query_log.read_rows` equals exactly **three source events per input sample** on the 3d all-stage experiment (846,108 rows vs 282,036 source samples), consistent with reading three TS target tables. This is **aggregate query-log accounting**, not proof of exact per-table read attribution. `EXPLAIN indexes=1` plus per-table profile would be needed.

### Native ClickHouse MergeTree

```text
time predicate / MergeTree part pruning
  -> read one event row per build-stage
  -> avg(duration) GROUP BY (day,stage)
  -> stage/day result
```

There is **no per-event global series ID**, label-index search, time-ranges JOIN, or one nullable grid per logical series. This simpler data model is important for events with exactly one scalar observation.

## Query benchmarks

Each engine runs on the **same GitHub-hosted runner**, with one warmup and four measured repetitions; timings are **whole HTTP request** p50 in milliseconds. Values were compared for every stage/day point. Results from different runs MUST NOT be directly treated as an A/B/A speed difference.

| Dataset, query filter | VM p50 | CH TimeSeries p50 | CH native MergeTree SQL p50 |
|---|---:|---:|---:|
| 14d, 79,800 samples, all 38 stages | 124.864 | 249.584 | 12.404 |
| 14d, branch=main | 30.603 | 86.145 | 8.243 |
| 14d, project=project-00 | **3.806** | 41.349 | 6.576 |
| 14d, ten stage values | 34.385 | 91.920 | 8.011 |
| 3d, 282,036 samples, all 1,448 stages | 575.200 | 1,523.358 | **32.170** |
| 3d, branch=main | 164.365 | 652.061 | 30.989 |
| 3d, project=project-00 | **14.363** | 340.882 | 22.642 |
| 3d, ten stages | **7.377** | 344.921 | 16.512 |

**Run links and evidence:**

- [14d exact VM/CH/SQL run 41344558732](https://github.com/moleus/ClickHouse/actions/runs/41344558732), artifact `13099075579`, ZIP SHA256 `ce811755427bb717d79ef2b1a99ba727b720e8f3ee5ce0f0ed99e77b2f4e1ac3`, output532 points.
- [3d exact VM/CH/SQL run 41344565484](https://github.com/moleus/ClickHouse/actions/runs/41344565484), artifact `13098533879`, ZIP SHA256 `1a8a6de5ae0a110ccd9b5055f9f868423c88fcbf4a6bada3d9392102dc142c0a`, output4344 points.
- VictoriaMetrics ingestion becomes query-visible **asynchronously** after import: 14d job needed 5 polling attempts, 3d job needed 6. The first two failed runs did not wait, and were correctly **not** counted as performance results.

### Observed query memory, reads and physical storage

| 3d all-stage query | CH TimeSeries | CH native SQL |
|---|---:|---:|
| `query_log.read_rows` p50 | 846,108 | 282,036 |
| `query_log.read_bytes` p50 | 103,784,412 | 5,233,080 |
| `query_log.memory_usage` p95 | 506,816,979 B | 18,099,681 B |
| `query_log.memory_usage` peak | 507,020,924 B | 18,100,515 B |

VM equivalent tracked `query_log.memory_usage` does not exist. We did **not** obtain comparable VM peak RSS or CPU metrics, so do NOT conclude CH uses 28× more RAM than VictoriaMetrics. **The 28× figure compares two different ClickHouse query routes only.**

At the end of the 3d run, **active ClickHouse parts** occupied TS samples **10,918,201 B** + tags **18,407,385 B** + time_ranges **8,145,850 B** = **37,471,436 B**, vs native event MergeTree **3,230,790 B**. These are before long-term merge/compaction; not a storage-retention benchmark. VM data directory **41,252,122 B** has a different format and accounting; compare it cautiously.

## Independent optimization A/B/A results

### Time-ranges JOIN toggle

[Run 41344936171](https://github.com/moleus/ClickHouse/actions/runs/41344936171), artifact `13098951667`, SHA256 `2b0fefa547c5b5fac338f7eed699ea64e7bda794b9b24536c9f7339fdc6c178a`.

Both TS tables contain the identical 282,036 input records; one has `store_time_ranges=true`, the other `false`. All 4 query cases return the same labels/timestamps/values as VM and native SQL.

| Case | ON p50 | OFF p50 | ON recheck p50 | ON → OFF read_rows |
|---|---:|---:|---:|---:|
| all | 770.867 | 663.806 | 819.165 | 846,108 → 564,072 |
| branch=main | 229.545 | 185.531 | 240.070 | 846,108 → 564,072 |
| project=project-00 | 98.577 | 71.365 | 101.254 | 846,108 → 564,072 |
| ten stages | 103.979 | 75.144 | 105.436 | 844,060 → 562,024 |

But the wide-query memory remains around **507–510 MB** in all phases. The JOIN is a measurable latency and IO cost but not the principal high-cardinality memory cause. Removing it drops active TS parts from about **37.5 to 29.3 MB**. **Do not disable universally:** for short lookback queries over long historical retention, time-ranges pruning can save much more work than the JOIN costs.

### Store labels `stage,branch,project` in dedicated tags columns

[Run 41345268019](https://github.com/moleus/ClickHouse/actions/runs/41345268019), artifact `13098084872`, SHA256 `d2c4a42c2facae2138b2321a2e656ca24109386708f4ef2a1a8cf21782262399`.

Both tables have `store_time_ranges=false`. Candidate uses ClickHouse `tags_to_columns={'stage':'stage','branch':'branch','project':'project'}`. All results exact.

| Case | Plain tags p50 | Dedicated columns p50 | Plain tags recheck |
|---|---:|---:|---:|
| all | 664.977 | 752.240 | 725.200 |
| branch=main | 245.312 | 276.316 | 256.495 |
| project=project-00 | 109.094 | 107.856 | 111.882 |
| ten stages | 104.275 | 99.776 | 105.886 |

Full query memory p95 **506.3 → 549.0 → 509.2 MB**; `read_rows` **564,072** in all phases. Dedicated columns increased tags part size from **18.41 to 21.53 MB**. They **did not** provide a general index-pruning improvement. A proper primary/skip index and matching predicate pushdown would require separate source and runtime validation.

## IMPORTANT MetricsQL vs PromQL instant semantics

[Run 41345168886](https://github.com/moleus/ClickHouse/actions/runs/41345168886), artifact `13098872218`, SHA256 `a444b57a6e37bacc4f27ef4e32edb2c84895bf82ef6bf2fe422b957622c62`.

- `avg by(stage)(jenkins_avs_build_stage_duration)` on 14d with daily step: **VM MetricsQL returns 532 points; CH PromQL returns 0**.
- Explicit `avg by(stage)(last_over_time(metric[5m]))`: **both return 0**. Synthetic events occur during the previous day, not within 5 minutes of midnight.
- Explicit `avg by(stage)(avg_over_time(metric[1d]))`: **both return identical 532 points** and equal event SQL.
- VM v1.134.0 source: [`eval.go` around 279](https://github.com/VictoriaMetrics/VictoriaMetrics/blob/v1.134.0/app/vmselect/promql/eval.go#L279-L286) raw selector maps to `default_rollup`; [`rollup.go` around 734](https://github.com/VictoriaMetrics/VictoriaMetrics/blob/v1.134.0/app/vmselect/promql/rollup.go#L734-L749) uses `window=step` when implicit. This can use previous-day values at daily step, unlike CH PromQL lookback.

**Warning:** `avg by(stage)(metric)` over a 1d Grafana step and `avg by(stage)(avg_over_time(metric[1d]))` are semantically different: the first may return the last sample per series in its implicit window; the second computes each series' explicit daily average (with one sample they happen to be the same per-series value). A comparison must never silently equate the two expressions.

## VM 165k cap and validated workaround

- Under `-search.maxUniqueTimeseries=165000`, a **full 3d, 282,036-series** query fails **HTTP 422** *before any data block is read*, error `the number of matching timeseries exceeds 165000`. Reproduced independently in [run 41344565484](https://github.com/moleus/ClickHouse/actions/runs/41344565484) and [run 41345689762](https://github.com/moleus/ClickHouse/actions/runs/41345689762).
- In the dedicated second run with capped VM, **day-by-day queries at midnight did NOT reliably avoid the limit**: first two one-day query_range requests HTTP422, last day succeeded. Their index search windows included two calendar dates. A simple "break into days" workaround is NOT automatically safe.
- **Disjoint project label sharding works** on this source data: 2 groups of 34 projects each, match each using regex; issue `sum by(stage)(avg_over_time(metric[1d]))` AND `count by(stage)(avg_over_time(metric[1d]))` for each shard; merge each stage/day as `sum(shard_sums)/sum(shard_counts)` (NOT mean-of-means). Reconstructs **4,344 exact points** vs native SQL with max absolute error ~1.14e-13. 4 sequential HTTP calls, aggregate p50 **1,114.002ms** across 4 repeats, compared with a single uncapped initial request **758.139ms** (one measurement, no statistical equivalence for cost). This is a functional mitigation for constrained VM but increases total work.
- [Sharding run 41345689762](https://github.com/moleus/ClickHouse/actions/runs/41345689762), artifact `13099281874`, ZIP SHA256 `a23e4c8c0a2f3790efbfc9d653ea204b0b0f663f6eaa69d55c63494b7a2fe328`. First attempt failed solely because old VM still held `flock.lock` at startup; runner lifecycle code now polls the actual exclusive file lock before restart (script commit `b68be39...`). No failed run used as a query performance result.

## Production recommendation and open gates

For **events** like CI stage-duration (one observation per build stage), the best tested path is native MergeTree rows plus explicit time-window aggregates, not ClickHouse TimeSeries PromQL. Keep the original high-cardinality metric if other consumers need original labels and drilldown. For the Grafana average, maintain hourly/daily **`sum(duration)` and `count(duration)`** by explicit stable labels (`jenkins_job`, `branch`, `stage`, `result` as required), then compute `sum(sum)/sum(count)` at query time. Aggregating averages of averages is incorrect when build counts differ. Choose dimension labels based on product need, not merely data volume.

The VM screenshot recorded **53,205 currently visible series** but does NOT tell us the unique series per vmstorage over the 15–40-day query range. User-provided facts indicate about 94–95k new series/day and 69% back-delta. Our generator ingests data in chronological day order and did **not** model that 69% delayed ingestion or re-aggregation and retention effects.

**Still not tested:** actual Grafana dashboard JSON, 13.4M live series, 650–800k samples/s, >=500 new series/s, 15s HA dedup, vmselect cluster fanout/piecewise sharding, promxy error masking, VM RSS and CPU, CH production -O2/-O3 binary, S3 cold storage, 40-day partitions/merges, and long-term backfill correctness. The `promxy ignore_error=true` -> blank response / "No data" diagnosis comes from user-provided config/facts, NOT from this single-server CI test.

Full machine-readable artifacts, raw query logs, binaries SHA256, ingestion checks and source code are linked from [ENGINEERING_NOTES_20261101.md](./ENGINEERING_NOTES_20261101.md).
