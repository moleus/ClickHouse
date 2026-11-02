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


## 2026-11-02 18:55 UTC — real smoke, public corpus, mark model and broad-query OOM

- Native small real TimeSeries layout schema 41502387484 SUCCESS: four versions default Tuple2, stage Tuple3, project Tuple3 and project+stage Tuple4, five native PromQL selectors, all 20 output comparisons equal, 3200 entirely synthetic rows, two insert batches plus OPTIMIZE FINAL. Fixture SHA256 cec5b724cd0c2900660ccc33fa56aae1b75eda502277e0023c554ed8ab158f24. Previous 41501554751 (tuple-keyed JSON encoding), 41501791155 and 41502155881 (incorrect TimeSeries SETTINGS and INNER COLUMNS grammar) were test harness failures, not query regressions.
- Public dashboard matcher 41502908657 SUCCESS: eight dashboards, 289 PromQL targets from pinned public dotdc/grafana-dashboards-kubernetes commit ad7c41e8f967ba788c9f090de537519bd376fd4e. Unique panel count with filters cluster287 (99.3%), namespace102 (35.3%), pod87 (30.1%), job55 (19.0%), instance54 (18.7%). All 289 had explicit metric_name in heuristic. NOT real query volume, user traffic, or representative SRE system. No private Grafana data used.
- Independent deterministic 1.33m mark model 41503557195 SUCCESS: simulated mark counts (whole,stage,project,both,regex) default (5196,5195,5196,4230,5196), stage prefix (5196,137,2611,137,5196), project prefix (5196,2599,650,650,5196), project+stage prefix (5196,140,650,35,5196). SplitMix64 instead of ClickHouse SipHash and one sorted part, so predictions are geometric only and cannot imply query performance.
- Actual heavy matrix first run 41502862519 all three jobs failed before query due Python module path missing after importing 1.33m rows; fixed. Next 41503330169 two cases failed due missing asynchronous QueryFinish immediately after successful queries; deferred logging and bounded retries, preserved diagnostic evidence. Follow-up 41504309748 stage-prefix whole metric candidate reached RSS4.63GiB, ClickHouse total memory limit4.66GiB, MEMORY_LIMIT_EXCEEDED at AggregatingTransform. This is a real candidate regression against capacity, not an experiment harness bug and cannot be solved by simply raising the limit. Project and project+stage still require validation.
- Matched O2 experimental native one-point lab setting promql_enable_native_one_point_lab is OFF in this full layout benchmark; candidate needs testing ON separately with semantic/permission guards. Broad query memory increase may come from more complex tuple ID hash maps, materialization or aggregation. Need stack/alloc profile to separate effects.
- Dense-read separate workflow 41504786378 tests 10k stable synthetic series x240 samples each spaced15seconds =2.4m samples per table, comparing standard ID and cluster+namespace prefix, A/B/B/A under one binary and 4 selectors. Bulk import one JSON row per series; NOT actual every-15s remote_write ingestion. Do not claim ingestion findings from it.
- Code for compact reducer .github/ts-lab-layout/aggregate_layout_campaign.py exists; published results branch lab/timeseries-layout-results initialized but full matrix might not pass due genuine OOM. Publish compact failed-case artifacts rather than falsely describing success. Later aim to test physical sort keys without changing logical TimeSeries ID for memory safety.
- Main next steps: selectively test stage/project on native ID before wide query to preserve useful evidence, profile broad OOM memory, check results of dense and other two matrix jobs, evaluate new tuple vs sidecar physical key and smaller ID composition, keep independent source/fixture hashes. Public fork only, checkpoint each meaningful result.


## 2026-11-02 19:00 UTC — complete 3-way broad memory failure and dense 15s sample result

- 41504309748 three independent O2 no-jemalloc full 1.33m layout jobs all **FAILED on broad 14d query**, with server maximum RSS approximately 4.66GiB. Stage-prefix candidate: AggregatingTransform wants 4.66GiB RSS4.63GiB; project-prefix candidate: ID read from TimeRanges MergeTreeSelect RSS4.66GiB; project+stage-prefix candidate: ID IN-Set MergeTreeSelect RSS4.66GiB. All run after full import, OPTIMIZE FINAL and baseline table built. These are genuine high-cardinality broad-query OOM regressions, not successful latency comparisons. Do not raise memory limit and call layout good. Physical tuple key growth and CPU hash/aggregating data structures are causal candidates, but exact exclusive memory allocation site not yet established. No stage/project sparse timing accepted from that run because broad case was tested first.
- 41504786378 dense stable 15-second data **SUCCESS**, fully synthetic 10000 unique series x240 timestamps each (2.4m samples per table, 84,568,960 byte source file, SHA256 83695082ec9713894ae3a75bec127065fed82f13673093ff8de4ac2b8d15c9ce), bulk-inserted one row per series (NOT a 15-second real ingest stream). Native default ID versus (metric_name, cluster, namespace, full-tags UUID) compound ID, same O2 binary, A/B/B/A independent fresh processes, 3 warmups+6 measured requests per phase, same full result values. Sparse cluster exact: 49.037 ->46.419ms (1.056x); namespace: 33.084 ->26.284ms (1.259x); both:29.919 ->21.413ms (1.397x); broad whole metric:104.868 ->112.638ms (0.931x; 7.4% regression). Baseline A/A drift <=2.1%, still limited number of trials and synthetic distribution. The conditional benefit for filtered read and broad regression are both real on this fixture, but memory/ingestion risk prevents production recommendation. Raw artifact id13169568290, https://github.com/moleus/ClickHouse/actions/runs/41504786378.
- To isolate useful small-filter behavior on 1.33m high-cardinality without broad OOM, 41505253205 started **6 parallel GitHub jobs** matrix: 3 ID orders (stage,project,project_stage) x 2 opt-in fastpath settings (promql_enable_native_one_point_lab 0 or1). Four selective query types, A/B/B/A fresh server with 2 warmups+4 measurements per phase and stable physical parts. This is separate from broad-query clearance: even if selective benefits, OOM makes candidate unready until memory is solved. No private data, no cron, no source compiler rebuild.
- Repo branch lab/timeseries-layout-research-20261102 for scripts/checkpoints, branch lab/timeseries-layout-results for short results; source benchmark .github/ts-lab-layout/bench_layout_full.py now has --selective-only and --fastpath. The earlier full-matrix error from QueryLog delayed ingestion was corrected with phase-end logging and bounded retries; OOM is independent.
- Next research: get real SAMPLES EXPLAIN mark savings from 6 sparse experiments, measure long tuple ID overhead in registry and hash sets, compare to no-identity-change secondary physical ordering in separate target (can require C++ or schema custom sort), then profile ingest/merge on 15s arrival. Do not optimize arbitrary small matcher cost without measured high-leverage.


## 2026-11-02 19:08 UTC — six matched selective jobs passed, broad still fails without fastpath

- GitHub Actions run 41505253205 SUCCESS: all six jobs passed, three ID layouts (stage, project, project_stage) x experimental native one-point fastpath OFF/ON. Identical 1.33m source hash, same verified O2 binary, fixed physical parts and full output equality with standard ID for four filtered queries. Each case had phases A/B/B/A with fresh server per phase, two warmups and four measurements per phase.
- Per-job speedups (baseline over candidate), filters in order stage, project, stage+project, branch regex:
  - stage OFF: 2.577x, 1.204x, 3.314x, 0.982x; maximum baseline drift 6.83%.
  - stage ON: 2.939x, 1.180x, 3.898x, 0.958x; drift 20.70%.
  - project OFF: 1.312x, 1.357x, 2.555x, 0.991x; drift 22.22%.
  - project ON: 1.609x, 1.767x, 2.614x, 0.985x; drift 8.24%.
  - project_stage OFF: 2.836x, 1.487x, 2.973x, 0.970x; drift 22.81%.
  - project_stage ON: 2.530x, 1.494x, 3.056x, 0.907x; drift 20.78%.
- All six selective cases completed without OOM; this does not clear the earlier 41504309748 high-cardinality metric-wide OOM at the 4.66GiB cap. Regex sometimes regresses by up to 9.3%, so no universal improvement. Comparing fastpath ON vs OFF from different runners is not a controlled comparison by itself.
- 41506270694 compact aggregator initiated: obtains six pinned raw artifacts and also dense 41504786378, mark model 41503557195, public corpus 41502908657, and publishes summary JSON and Markdown to lab/timeseries-layout-results. Must check run conclusion before citing as published. Raw data stays in original GitHub Actions.
- Separate 41506112971 started three broad metric-name query cases with opt-in fastpath ON, maximum two jobs concurrently. Measures whether broad OOM persists under the optimized one-point C++ path. Do not mix results with default OFF.
- Even with 3.90x observed filtered-query win, longer tuple ID has not met broad query memory and general PromQL/ACL requirements. Consider an opt-in physical sort key separate from logical ID and a query-plan filter on that key; no production or upstream patch yet.
