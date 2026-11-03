# R03: source-level index and query path comparison (not an attribution proof)

Date: 2026-11-03. Scope: source-pinned read semantics for the
[CH vs VM 400-node comparison](../ts-lab/claims/roots/R03.json).
Both engines are queried through their documented/native PromQL range
entrypoints. Do **not** infer full operator cost from read_rows alone.

## Pinned source paths

- ClickHouse source
  `52529f0c711566d696917678bf15e7068772fdde`:
  [`StorageTimeSeriesSelector.cpp::readImpl` lines 1219–1340](https://github.com/moleus/ClickHouse/blob/52529f0c711566d696917678bf15e7068772fdde/src/Storages/StorageTimeSeriesSelector.cpp#L1219-L1340).
  It builds a TAGS query, may involve TimeRanges, and then reads SAMPLES
  using the selected ID set. For v11 **bucketed SAMPLES**, comment
  lines 1300–1304 expressly say it uses an ID set for primary-key
  index analysis, instead of the whole-metric ID-range fast path
  that is available to unbucketed SAMPLES. This is a real
  mark-pruning path, but is not guaranteed to select only requested
  series' physical rows.
  `applySettingsForSelectIDsQuery` (lines 1167–1174) chooses
  `full_sorting_merge` and `optimize_read_in_order` to avoid
  a full per-metric TimeRanges hash table. The read implementation
  also lowers concurrent-read thresholds for bucketed rows.
- VictoriaMetrics single-node v1.134.0:
  [`index_db.go::searchMetricIDs` lines 1689–1723](https://github.com/VictoriaMetrics/VictoriaMetrics/blob/v1.134.0/lib/storage/index_db.go#L1689-L1723)
  uses the tag-filter/time-range key, checks a MetricIDs cache, and
  falls back to an index search. Its
  [`SearchTSIDs` lines 1725–1755](https://github.com/VictoriaMetrics/VictoriaMetrics/blob/v1.134.0/lib/storage/index_db.go#L1725-L1755)
  converts the result to time-series IDs. Its
  [`searchMetricIDsInternal` lines 2231–2250](https://github.com/VictoriaMetrics/VictoriaMetrics/blob/v1.134.0/lib/storage/index_db.go#L2231-L2250)
  can use a composite tag-filter path. Its
  [filter intersection lines 2655–2702](https://github.com/VictoriaMetrics/VictoriaMetrics/blob/v1.134.0/lib/storage/index_db.go#L2655-L2702)
  intersects candidate MetricID sets, postponing expensive filters
  and choosing direct metric-name matching once cheaper.
  This is a different physical search and filter strategy, not
  evidence by itself that VM's indexes are always better.

## Experimental observations

In first [R03 CI #41623609320](https://github.com/moleus/ClickHouse/actions/runs/41623609320)
both query endpoints returned exactly the same nonempty
`avg by(namespace/project)(avg_over_time(...[1d]))` values and all
daily timestamps on 2,488,320 input gauge samples.
ClickHouse `query_log.read_rows` for the one-namespace shape was
**102,368** even with 3d/28,896 distinct IDs and an opt-in RAW UUID
layout; this count is across all read subplans (TAGS, TimeRanges
and SAMPLES) and must **not** be presented as 102,368 samples
matching the namespace or as a direct measure of posting-list misses.
Node-broad read_rows was **57,728** and pod-broad **103,392**
on this source fixture; narrower label matching can incur more
query-log read rows than a broader query through additional
subqueries/metadata.

A selective namespace read was much more expensive on stock O1 CH
than VM1.134 on this warm host, even though CH opted into RAW UUID
rather than the default LowCardinality ID schema. This does not show
that UUID hash decoding is the dominant remaining cost. Both engines
retain internal caches: `-search.disableCache`/VM `nocache=1`
disable query response caching, **not necessarily** the MetricIDs
tag-filter cache named above.

**Causality not established:** R03 logs provide end-to-end CPU/wall
and CH physical read rows/bytes but do not separately isolate
TAGS index search, ID-set materialization, TimeRanges merge,
SAMPLES decompression, time-grid aggregation, or VM index-cache hits.
The R05 owner is already investigating candidate ID set vs actual
mark coverage. Do not duplicate that experiment.

## Targeted follow-up (only after valid R03 64-wave CI)

1. Obtain `EXPLAIN PIPELINE` and per-operator
   `processors_profile_log` for the exact one-namespace query,
   alongside TAGS/TimeRanges/SAMPLES physical rows and selected marks.
   Compare with the same all-pod query **on identical parts**.
   Do not add layer times as though they were wall-time summands.
2. Use VM's query tracer / index search counters and warm/cold
   tag-filter cache controls to determine actual time spent
   intersecting postings versus retrieving samples.
3. Verify the **same CPU/memory cgroup budget and compiler optimization**
   before claiming that one production engine is faster.
   The current CH executable is an unusual stock `-O1` lab build,
   whereas VM is an optimized release. This is an intentionally
   declared first-stage comparison, not a production benchmark.
4. Prioritize a pinned 14-day/1.33m CI case if numeric parity,
   8-client memory safety and VM's 165k search cap are measurable;
   avoid multiplying synthetic 3d speed ratios into a 3-month forecast.

The first R03 run was censored by VM A/A drift and an intrusive
RSS observer; the [immutable failure record](../ts-lab/evidence/R03-first-censored-41623609320.json)
must remain separate from the corrected 64-wave follow-up.
The independent ongoing jemalloc build [#41607779848](https://github.com/moleus/ClickHouse/actions/runs/41607779848)
is preserved, not cancelled or replaced.
