# Independent research checkpoint — metric/label-aware physical locality in TimeSeries v11

Date: 2026-11-02. Public open-source data only. Branch: lab/timeseries-layout-research-20261102.
Base: lab/timeseries-fastpath-ci; this separate branch avoids disrupting the concurrent O2/allocator work.
No production rollouts, no upstream writes, no automated schedules, no private Grafana or infrastructure data.

## User hypothesis to verify

Most metric queries start with a concrete metric name. Their selected time series should be physically close; likely effective next labels include region/DC/cluster, namespace, job, instance, or (for the synthetic Jenkins example) stage/project. Preserve rapid ingestion, merges, compression, time-range pruning, and correct PromQL semantics. A hashed metric-name prefix plus independent suffix is proposed. Analyze actual VictoriaMetrics code and public Grafana queries. Use multiple concurrent GitHub jobs when independent and bounded. Checkpoint source/data/binary IDs.

## Verified upstream implementation (pinned source)

ClickHouse commit 52529f0c711566d696917678bf15e7068772fdde (not just current docs):
- src/Storages/StorageTimeSeries.cpp lines 1243-1250: default TAGS id Tuple(UInt64,LowCardinality(UUID)) DEFAULT tuple(sipHash64(metric_name),toLowCardinality(reinterpretAsUUID(sipHash128(tags)))).
- lines 1221-1231: SAMPLES target id has same type, 1h bucket and ORDER BY (id,bucket), AggregatingMergeTree. Each physical row is an array of samples for one ID and bucket, not one sample; merges can combine duplicate (id,bucket).
- TAGS is ReplacingMergeTree PRIMARY KEY metric_name ORDER BY(metric_name,id); TimeRanges ORDER BY id (lines 1250-1257).
- lines 1403-1417: id type and hash expression customizable in TAGS INNER COLUMNS and all target ID types must match. Existing default **already groups by metric name**. Changing id changes the entire physical layout and is not just a hash performance tweak. tags_to_columns only changes TAGS columns, not SAMPLES layout (lines 1427-1437).
- Version11 uses recent-samples target by default; lab explicitly disables recent_samples_ttl_seconds for consistent cold/main table. Production read path may differ with recent samples TTL.
- src/Storages/TimeSeries/TimeSeriesSettings.cpp includes samples_index_granularity default256.

VictoriaMetrics **exact v1.134.0** source:
- lib/storage/tsid.go lines17-51 and 89-101: TSID {MetricGroupID uint64,JobID uint32,InstanceID uint32,MetricID uint64}; lexicographic TSID.Less first metric group, then job/instance, finally unique MetricID. This is a *separate locality key*, not altering MetricID itself.
- lib/storage/index_db.go lines418-431: MetricGroupID xxhash.Sum64(metric name); JobID first canonical job-like label value hash truncated32 bits; InstanceID second instance-like hash truncated32 bits; MetricID from unique generator.
- lib/storage/metric_name.go lines569-619, 667-725: sortTags() prioritizes recognized job-like keys namespace/ns/datacenter/dc/environment/env/cluster/service/job etc and instance-like keys instance/host/server/pod/node etc. It is **not guaranteed to use literal job,instance if other earlier preferred keys are present**.
- VictoriaMetrics docs: partition blocks sorted by TSID and timestamp, up to8192 raw samples per block from *one series*, time-series-specific index.bin/metaindex.bin. Not point-by-point always. CH v11 granule=256 physical bucketed series rows, not directly comparable to VM8192 samples per series block.

## Known mathematical and measured evidence

Run41492609952 exact stage 35000/1330000 selected but SAMPLES id IN stage touched ~5197/5197 marks at g256 because all 1.33m rows have the SAME metric_name; tag hash within that prefix scatters selected stage IDs.
Run41493836279 controlled g16/64/256: fewer read rows did not help latency; stage g256 679-770ms vs g64 1076ms vs g16 1396ms, broad query g256 ~4.0s vs g16 ~5.43s. g16 produced many more selected marks; do not tune mark size blindly.

If p is selection probability independent within a sorted ID region and g=granule rows, expected touched fraction is 1-(1-p)^g. For stage p=.026316,g256 => ~99.89%; matching observed. For a SORTED stage prefix (38 groups), selected stage should occupy ~35000/256=137 marks plus O(number of parts) boundary marks, **if** ClickHouse query planner can prune ID IN sets by primary key. This is a conditional model, not an actual measured query speedup. Stage-prefixed ID potentially moves data within same metric, not across metric names; extra hash/tuple fields may make ingestion, metadata, merge, compression and broad queries worse.

## Workload analysis and limits

Public corpus pinned dotdc/grafana-dashboards-kubernetes commit ad7c41e8f967ba788c9f090de537519bd376fd4e, 8 dashboard JSONs and 289 panel target PromQL expressions in first run41502051324; cluster, namespace, pod, job, instance are common. **This is not access volume, cannot establish that 99% of requests use these exact filters**. First parser over-counted repeated matches as fraction of panels; corrected in newer script. For real workload ranking require privacy-reviewed aggregated query-log histograms, which are out of scope here because user explicitly requires no private data in public fork.

## Experiments in this branch

1. Small compatibility smoke .github/ts-lab-layout/layout_id_smoke.py, four native TimeSeries id layouts: default, stage, project, project+stage prefix. Twenty PromQL outputs checked against default. Uses 3200 entirely synthetic rows, 2 metric names, four days, two sequential ingest batches and OPTIMIZE. First failure was tuple-key JSON digest error; fixed. Second was TimeSeries SETTINGS placed after INNER COLUMNS, interpreted as unknown inner-table setting; fixed. Latest workflow status must be checked before any heavy 1.33m import.
2. Public dashboard parser .github/ts-lab-layout/analyze_public_promql_corpus.py and short workflow ts-lab-layout-public-promql-20261102.yml. Only repository/commit and aggregate labels published. Repeat after panel-frequency correction.
3. Next: 3 independent full fixture runs by layout candidate. Each runner loads canonical SHA256 8f3a36846f80d6be5a8604d142e934399f2687094766c57bf105e43977f52858 and same matched O2 no-jemalloc source binary as baseline. Pair baseline vs candidate native PROMQL with initial/final controls, exact VM-validated semantic output, selected counts, stable physical parts, read_rows/read_bytes, SAMPLES and TimeRanges granule pruning, CPU and wall latency, RSS, insert time/bytes/merges. User asks for up to8 concurrent jobs; prefer3 independent runners then scale to 8 only with useful independent hypotheses and GitHub quota.
4. Later dense scrape 15-second cadence, many metric names/regions, 3h churn and inserts/background merges; needed before recommending physical key production defaults.

## Safety / modeling cautions

- Changes to the physical ID must preserve the full hash of tags and metric name to keep unique series, and every internal table must use the same ID type. In-place conversion/mixed IDs may break reference and dedup behavior; treat as migration/new table, not an ALTER on production.
- PromQL filter can omit an explicit metric or match multiple metrics by regex. No invariant that 99% have concrete metric name without actual workload evidence.
- CPU cache lines typically 64 bytes; memory pages commonly 4KiB; CPU L1/L2/L3, TLB, page cache and disk access are distinct. Hosted GitHub runner lacks hardware PMU; use CPU-clock DWARF stacks, logical rows/bytes and /proc, do not invent LLC miss rate.
- Do not add sample rows per separate tag to main SAMPLES table without considering its AggregatingMergeTree grouping by (id,bucket).
- Any speed increase with custom ID must be evaluated with all-series broad queries, sparse/dense, histogram of label selectivity and insertion/merge cost.
- The current 1.33m series fixture is intentionally worst case with all series of one metric and one sample per ID. It is not a representative stable 15s scrape; treat results as identifying algorithmic worst cases.
- Runs and source/binary SHA recorded in original action artifacts. Use compact analysis, never dump raw logs into agent context.
