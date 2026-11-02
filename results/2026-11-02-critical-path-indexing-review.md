# ClickHouse TimeSeries versus VictoriaMetrics — source-verified critical-path results, 2026-11-02

Synthetic public-fork experiment only, **no private telemetry** or production change. All result numbers below compare two alternatives on one runner via A/B/B/A, 2+ warmups and 4+ trials per phase. Different jobs are not pooled because runners differ.

## Why VictoriaMetrics is quicker on selective Jenkins stage

Both systems put the metric name before the individual series. ClickHouse source 52529f0c711566d696917678bf15e7068772fdde uses id tuple(sipHash64(metric_name),sipHash128(tags)) and SAMPLES ORDER BY(id,bucket), TAGS ORDER BY(metric_name,id), TimeRanges ORDER BY id. VictoriaMetrics source v1.134.0 uses TSID (MetricGroupID,JobID,InstanceID,MetricID) plus per-day inverted tag indexes and TagFilters->MetricIDs caches. Stage is not normally a VM preferred job/instance physical sort tag. The major difference for exact stage is the ID search and execution pathway, not an absence of metric-name locality in ClickHouse.

CH native TimeSeriesSelector builds a TAGS/TimeRanges LEFT JOIN with full_sorting_merge, a set of IDs, a SAMPLES IN filter, per-query tag registration and PromQL aggregations. In VM the index evaluates tag posting-list intersections, caches and fetches selected TSIDs directly. Prior CPU profiles 41500561862 CH stage projected wall356.461ms/CPU940ms versus VM66.427ms/CPU100ms; CH hash/ID set 29.5% inclusive CPU samples and memory/copy20.5% (overlap; not exclusive). No hardware cache-miss PMU on hosted runner.

## The strongest direct tests

1. Native 3200-row TAGS stage column smoke 41507430422 PASS, 12 native PromQL output comparisons equal. tags_to_columns adds stage ONLY in TAGS. A SAMPLES stage String DEFAULT '' column stayed empty for all3200 rows; no inherited tags.
2. Full1.33m metadata-only factorial 41508506906: store_time_ranges=0 got stage1.528x/project1.367x/conjunction1.345x/regex1.228x, but broad whole0.887x (11.3% slower). tags_to_columns stage alone stage0.890x and broad0.722x; adding a stage column did not help. Sorting TAGS by (metric_name,stage,id) led to broad OOM around4.65GiB in MergeSortingTransform. All ID types remained the same short Tuple(UInt64,LowCardinality(UUID)).
3. Time windows 41510101527: no TimeRanges on whole-metric query 1d642.033->1674.198ms (2.61x slower),3d859.597->1795.684ms (2.09x slower),7d1472.390->2153.856ms (1.46x slower). The 14d whole comparison invalid baseline drift42%. Stage no TimeRanges gives speedup1.157x at1d,1.248x at3d,1.262x at7d,1.112x at14d. Project small/no-ranges 1d1.038x,3d0.982x,7d1.139x,14d1.329x. This proves a conditional time-index strategy is needed, not blanket deletion.
4. Fixed-width packed UUID label prefix, native small smoke41509110232 PASS 15 native PromQL comparisons with 3200 unique IDs. Actual 1.33m paired run41509482488 with experimental C++ fastpath ON: stage packed prefix: stage3.119x, project1.439x, both3.367x, regex0.979x, broad1.031x; project packed prefix: stage1.630x, project1.755x, both2.749x, regex0.983x, broad0.981x. All five checked PromQL outputs equal per successful job; no broad OOM. With fastpath OFF, BOTH packed IDs OOM near4.66GiB on broad query despite same 2-component ID TYPE. This disproves 'wide ID type is the sole cause of broad OOM'. WARNING: tag hash128 was truncated to112 bits to fit16bit label prefix and changes ID VALUES, so collision/migration/ACL/dense correctness not proven.
5. Native TAGS lightweight _part_offset projection smoke 41510682917 PASS after enabling deduplicate_merge_projection_mode='rebuild' for ReplacingMergeTree. It preserves original TAGS sort and ID and maintains projection on (metric_name,stage) through merges. The tiny 3200-row EXPLAIN had only ONE TAGS granule and did NOT choose projection, even with optimize_use_projections=1. Not a performance win yet. An actual SAMPLES stage index still requires stage to exist there and for the native selector to push filter into SAMPLES.
6. Granularity sweep41493836279 g16/g64/g256: g16 reduced SAMPLES rows scanned but doubled CPU/time for exact stage, due mark overhead, not a good universal fix.

## Math and data model

For stage matching p=1/38 of rows, g=256 uniformly mixed IDs, expected hit fraction for granules is 1-(1-1/38)^256, about99.89%. A SAMPLES bloom/set/minmax skip index cannot skip most marks in this layout. A physical chosen-tag prefix groups matching series together, but the cost of tag-map handling, ID set, time JOIN, and aggregation remains. Index more than a small set of useful labels only with measured query-rate and selectivity and a bounded ingest/merge write amplification budget.

Current main research checkpoint: https://github.com/moleus/ClickHouse/blob/lab/timeseries-layout-research-20261102/.github/ts-lab-layout/LAYOUT_RESEARCH_CHECKPOINT_20261102.md

## Next targeted hypotheses, not yet accepted

Use TAGS to produce a small candidate ID set FIRST, then perform an id-filtered sparse TimeRanges lookup or avoid the JOIN when time selectivity is low. For whole metric or short time range, retain time pruning. Preserve short immutable logical IDs and index selected physical labels separately if per-metric policy merits it. Full query p95, memory, dense 15s ingestion, merge write amplification, NaN/staleness/duplicates, cross-tenant ACL, replication, and cold IO still unverified.

Production-ready: NO. Upstream patch: NONE. Only public synthetic fork source, raw GitHub Actions artifacts, and reproducible check points.
