# Indexed stage in native ClickHouse TimeSeries: source and experiment checkpoint

2026-11-02. Everything here is from public-source revisions and synthetic CI data. No private data, no upstream changes, no production writes.

## Correct the assumption about layout

The two engines both group physical series by metric name but have DIFFERENT secondary sorting, index lookup and query execution. ClickHouse pinned 52529f0c711566d696917678bf15e7068772fdde: SAMPLES ORDER BY(id,bucket), with default id Tuple(sipHash64(metric_name),sipHash128(tags)). For Jenkins test all 1.33m series have the same metric name and the stage=stage-0007 filter hits 35k series spread across almost all marks. VictoriaMetrics v1.134.0 uses TSID sorted MetricGroupID,JobID,InstanceID,MetricID. VM job-like/instance-like tags have specific priorities and DO NOT include stage by default. Therefore VM's large speed advantage on Jenkins stage does not imply that VM sorted physically by stage.

## Exact critical path sources

CH src/Storages/StorageTimeSeriesSelector.cpp:
- lines353-365 convert tag matcher to a separate TAGS column when tags_to_columns was set.
- lines486-571 filter TAGS by a LEFT JOIN with TimeRanges by ID; lines1167-1174 force join_algorithm=full_sorting_merge and optimize_read_in_order=1.
- lines619-675 call timeSeriesStoreTags to register query metadata for every accepted ID.
- lines678-779 filter SAMPLES by time and an IN-set of IDs from TAGS and TimeRanges.
- lines1297-1321 for bucketed v11 choose id-set only; the whole-metric continuous-ID-range hint is only considered for earlier one-sample-per-row schema.
CH src/Storages/TimeSeries/TimeSeriesSink.cpp lines459-532 and1006-1035: SAMPLES is emitted with only id,samples,bucket,min_time,max_time. tags_to_columns adds columns to TAGS, not SAMPLES.

VM lib/storage/index_db.go v1.134.0:
- lines1690-1723 fetch cached TagFilters plus TimeRange -> MetricIDs.
- lines2231-2288 read per-day tag inverted index.
- lines2655+ choose intersected ID sets with estimated cost and short-circuit heuristics.
- SearchTSIDs uses MetricID->TSID cache and returns sorted TSIDs for block reads.
This path does not require SQL materialized ID-set plus generic TimeRanges LEFT JOIN at every selector.

## Prior measured evidence and bounds

CH O2 system allocator stage14 median537.314ms vs VM79.853ms in 41498812663, same synthetic 14d frozen fixture. Profile run41500561862 CH projected stage356.461ms wall and ~940ms CPU vs VM66.427ms wall and ~100ms CPU, NOT the same compiler/settings comparison. CH inclusive CPU samples stage hash/set29.5%, memcpy/alloc20.5%, not additive/exclusive. VM trace stage root83.33ms, TSID search23.7ms, fetch39.6ms, rollup17.8ms, nested stages. Direct TAGS stage metadata lookup ~22.6ms in 41489795764, so reducing TAGS filter alone cannot fix the 350-540ms native query.

The approximate 29.5% hash/set CPU family is only up to 1/(1-.295)=1.42x total CPU speedup if completely removed under Amdahl's assumptions. Focus on pipeline-wide work and avoid optimizing insignificant leaves.

Randomly scattered 1/38 selected IDs with granularity256 yields a hit probability approximately 1-(37/38)^256, or >99.8% of marks. This matches measured SAMPLES EXPLAIN 5191 of5196 selected marks. By prepending stage to ID, measured EXPLAIN reduced this to137 marks and reduced native sparse latency 2.6-2.9x (41505253205); however long compound IDs regressed broad memory and CPU (41504309748 and broad fastpath ON run41506112971).

## Possible indexes

1. TAGS dedicated stage column via TimeSeries tags_to_columns setting: supported in native selector and read, retains same ID and samples layout. Limited to TAGS lookup and tag registry. Can add a relevant TAGS sort key or secondary projection, but changing TAGS order away from id might force re-sort before TimeRanges merge join. Test first.
2. SAMPLES bloom/set skip index on id: selected IDs scattered across almost all granules; poor ability to skip unless a query is extremely selective. One primary index cannot represent tens of thousands of independent tag dimensions.
3. SAMPLES direct skip/text/projection index on stage: NOT possible until stage exists in SAMPLES physical rows. SAMPLES contains no tag values by default. An added DEFAULT empty stage column gets only empty defaults; cannot automatically retrieve it from TAGS.
4. ClickHouse 25.6+ lightweight _part_offset projections and 26.1 INDEX ... TYPE basic projection syntax can index an EXISTING column with low overhead, but the relevant stage must be materialized before a SAMPLES projection can work, and the TimeSeries native selector must push down the relevant predicate.
5. Keep short identity ID and add a separate physical sort_hint selected by metric policy (or a per-part posting list row offset index) at ingestion; adapt TimeSeriesSink and native selector, preserve fallback index on id and full semantics. Requires C++ implementation and correctness/migration/ACL proof. This is potentially safer than lengthening the ID itself.
6. Cheap independent candidate: skip the TimeRanges JOIN for supported selector cases or disable store_time_ranges for a separate new table and compare exact query output. Native sample time bounds can still exclude out-of-window samples. Must test empty/out-of-window and no-duplicate cases.

## Experiments started and/or completed

- Native 3200-series smoke 41507430422 SUCCESS. Default TAGS, tags_to_columns stage and TAGS ORDER BY(metric_name,stage,id) gave matching native values/labels/times for 4 PromQL selectors in each of 3 tables (12 comparisons). Adding stage String DEFAULT '' to SAMPLES led to 3200 rows and uniqExact(stage)=1; no label propagation.
- Four independent large 1.33m metadata-only A/B/B/A jobs in run41507857908: variants no TimeRanges, stage column, stage-sort TAGS, both no TimeRanges plus stage column. Same short ID, same O2 binary, same fixture hash 8f3a36846f80d6be5a8604d142e934399f2687094766c57bf105e43977f52858, 5 query patterns with exact parity, query CPU and RSS. Broad query is mandatory, and no variant may claim success on a broken baseline drift.
- Existing 6 layout selective jobs 41505253205 SUCCESS show stage-prefixed ID sometimes 3.9x faster sparse but broad OOM with fastpath disabled. Fastpath ON broad 41506112971: stage and project variants finish but stage broad is slower by 1.74x and project broad slower by 5.4%; project_stage baseline drift invalid. Do not claim universally successful physical-ID replacement.
- Additional optimal-order decisions require execution-weighted metric/label query distributions, selectivity by label, and metadata validation. The only public corpus available is 8 public Kubernetes dashboards/289 panel expressions, not production query counts.

## Controls and next work

Falsifiers: mismatched PromQL numeric values/timestamps/labels, memory over cap, hidden changes in physical parts/merges, QueryFinish missing, changed source binary/fixture SHA or >30% baseline drift. Performance trials are short and synthetic; no production confidence interval. Direct inner SQL is admin diagnostic only, not an ACL-safe external API.

Next target: read 4 metadata A/B results; isolate which part of native time filtering drives most CPU; test whether no-TimeRanges reduces wall for stage, and whether direct TAGS stage columns have only marginal effect. Compare native pipeline CPU/EXPLAIN with VM's cached tag-postings block search. Only then design an optional per-metric index policy and C++ patch.
