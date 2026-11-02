# TimeSeries physical ID locality, public-source checkpoint — 2026-11-02

Research only. All data is synthetic or from explicitly pinned public Grafana dashboards. No production deployment, no private query logs, and no upstream writes.

## Direct source conclusions

- ClickHouse pinned source 52529f0c711566d696917678bf15e7068772fdde already implements a metric-name-first ID: Tuple(sipHash64(metric_name), sipHash128(tags)). SAMPLES ORDER BY(id,bucket). TAGS ORDER BY(metric_name,id), TimeRanges ORDER BY id. See src/Storages/StorageTimeSeries.cpp lines 1221-1257, 1403-1417. The high-cardinality Jenkins stress case has **only one metric name**, so stage and project scatter inside its ID suffix.
- VictoriaMetrics source v1.134.0 uses TSID sorted lexicographically by MetricGroupID, JobID, InstanceID, MetricID. See lib/storage/tsid.go lines17-101, lib/storage/index_db.go lines418-431. sortTags chooses its first two job-like and instance-like preferred tags; it need not be the literal job and instance tags, see lib/storage/metric_name.go lines569-725.
- Both query by blocks; VM index.bin/metaindex.bin and CH MergeTree sparse index differ in physical model. No demonstrated hardware PMU counters on hosted GitHub runners.

## Public corpus

- Successful 41502908657, dotdc/grafana-dashboards-kubernetes commit ad7c41e8f967ba788c9f090de537519bd376fd4e.
- 8 public dashboards and 289 PromQL panel expressions. Panels using a label: cluster 287 (99.3%), namespace 102 (35.3%), pod 87 (30.1%), job 55 (19.0%), instance 54 (18.7%). Counts are panel definitions, **not execution volumes**, and the parser is heuristic.
- The corpus cannot prove that 99% of all production queries have a metric name or any particular label. Query weights cannot be inferred from dashboard panel counts.

## Full high-cardinality dataset: multi-field ID is not safe yet

- Native small 41502387484 PASS: 3200 synthetic series, four ID types default/stage/project/project+stage, five native PromQL expressions per type, 20/20 matched values/labels/timestamps.
- Mathematical counterfactual run41503557195 PASS: 1.33m rows, modelled 256-row marks. Default, stage, project, project-stage prefix marked selected granules respectively:
  - exact stage: 5195,137,2599,140
  - exact project: 5196,2611,650,650
  - exact both:4230,137,650,35
  Predictions are not measured ClickHouse EXPLAIN or latency.
- Full 1.33m native broad run41504309748 **FAILED for all three compound IDs**, each reached total RSS limit approx4.66GiB on 14d metric-wide query:
  - stage prefix: AggregatingTransform
  - project prefix: TimeRanges MergeTreeSelect read of ID
  - project+stage: ID-set filter MergeTreeSelect
  No verified speedup from this run; memory regression disqualifies deployment at current limits. Raising memory cap is not evidence of a good optimization.

## Dense 15-second sampled values, initial effective benefit

- Native run41504786378 PASS, synthetic fixture sha256 83695082ec9713894ae3a75bec127065fed82f13673093ff8de4ac2b8d15c9ce, 10k stable series x 240 samples at 15s spacing, 2.4m samples per physical table. Values bulk inserted once per series (not repeated 15-second remote-write ingestion).
- Same SHA-verified O2 experimental binary, default versus (metric,cluster,namespace,full-tags-hash) ID, A/B/B/A fresh server phase ordering, 3 warmups+6 measured per phase; all native output parity passed:
| Selector | Standard ID p50 | Compound ID p50 | Speedup (baseline/candidate) |
|---|---:|---:|---:|
| cluster | 49.037ms | 46.419ms | 1.056x |
| namespace | 33.084ms | 26.284ms | 1.259x |
| cluster+namespace | 29.919ms | 21.413ms | 1.397x |
| full metric | 104.868ms | 112.638ms | 0.931x (7.4% slower) |
- All start/end baseline drift ratios <=2.1%. Short trial counts, synthetic data, no hardware PMU and no live ingestion: not production SLO evidence.

## Active follow-up and acceptance

- Six independent jobs run41505253205: stage/project/project+stage ID formats x opt-in native v4 fastpath OFF/ON, sparse selectors only. Will establish if real primary-key/index granular pruning improves work despite broad memory OOM. Check final CI artifacts before making any claim.
- Earlier sample index mark granularity test41493836279: g256 best among g16/g64/g256 on 1.33m one-sample series; smaller granules read fewer rows but took more CPU/time, so no universal small-granule solution.
- Need address OOM causally with memory/CPU profile of join/set/collector and test physical sort key separate from unique logical ID, plus long-running 15s ingestion and merges.
- Full numerical parity, nullable/missing tags, NaN/Inf/stale samples, duplicate late ingest, tenant ACLs, 14-90d dense/sparse, concurrent query load, cold storage and distributed replicas remain required before production readiness.

## Reproduce and source of truth

Fork branch [lab/timeseries-layout-research-20261102](https://github.com/moleus/ClickHouse/tree/lab/timeseries-layout-research-20261102) contains all scripts, pre-registered experiments and [layout checkpoint](https://github.com/moleus/ClickHouse/blob/lab/timeseries-layout-research-20261102/.github/ts-lab-layout/LAYOUT_RESEARCH_CHECKPOINT_20261102.md).

Runs:
- https://github.com/moleus/ClickHouse/actions/runs/41502387484
- https://github.com/moleus/ClickHouse/actions/runs/41502908657
- https://github.com/moleus/ClickHouse/actions/runs/41503557195
- https://github.com/moleus/ClickHouse/actions/runs/41504309748
- https://github.com/moleus/ClickHouse/actions/runs/41504786378
- https://github.com/moleus/ClickHouse/actions/runs/41505253205

No new production patch. Checkpoint and raw artifacts are versioned and retained under GitHub Actions; this digest is deliberately small.
