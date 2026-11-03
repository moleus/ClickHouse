# R10: source-level CPU cause of the 900-second SAMPLES bucket regression

Date: 2026-11-03. Branch `lab/ts-cbo-adaptive-20261103`.
Full native [2×3 R10 CI #41613268106](https://github.com/moleus/ClickHouse/actions/runs/41613268106);
matched [short-lookback gate #41614179460](https://github.com/moleus/ClickHouse/actions/runs/41614179460).
Immutable evidence: [R10 20-pair record](../ts-lab/evidence/R10-bucket-id-factorial-v1-41613268106.json)
and [R10 12-pair short gate](../ts-lab/evidence/R10-short-lookback-41614179460.json).

## Source call chain and causal hypothesis

Pinned ClickHouse source SHA `52529f0c711566d696917678bf15e7068772fdde`.
1. [`TimeSeriesSink.cpp`](https://github.com/moleus/ClickHouse/blob/52529f0c711566d696917678bf15e7068772fdde/src/Storages/TimeSeries/TimeSeriesSink.cpp#L575-L646):
`makeSamplesBlocksForTimestampType` sorts the samples once and groups them
into SAMPLES target buckets using `samples_bucket_step_seconds`.
2. [`StorageTimeSeriesSelector.cpp::readImpl`](https://github.com/moleus/ClickHouse/blob/52529f0c711566d696917678bf15e7068772fdde/src/Storages/StorageTimeSeriesSelector.cpp#L1230-L1304):
the selector reads bucket step from the TimeSeries storage setting.
For bucketed SAMPLES, it chooses a TAGS-based ID set; it also adjusts
`merge_tree_min_rows_for_concurrent_read=0` when not explicitly configured.
The actual sample query receives both time bounds and the bucket step.
3. [`ColumnLowCardinality.cpp::insertRangeFromDictionaryEncodedColumn`](https://github.com/moleus/ClickHouse/blob/52529f0c711566d696917678bf15e7068772fdde/src/Columns/ColumnLowCardinality.cpp#L335-L345):
the LC ID alternative has additional dictionary rebuild/remapping work.
But the following 900s regression exists **with RAW UUID too**, hence is not
explained solely by LowCardinality decoding.

For a 3h-life series, 900/3600/10800s SAMPLES buckets make 12/3/1 physical
rows per series. The query may have fewer sample-array elements to filter per
row at 900s, but it also performs more MergeTree read/bucket aggregation work.
The expected effect must be tested, not inferred solely from index granularity.

## Physical and native same-source measurements

R10 `pod_namespace` **1-day PromQL** at the same 28,896 logical series and
2,488,320 sample points:

| RAW UUID statistic | 3600s control | 900s candidate | Direction |
|---|---:|---:|---|
| Total physical SAMPLES rows | 86,688 | 346,752 | **4×** |
| Total SAMPLES part bytes | 5,303,391 | 5,677,267 | +7.1% |
| Native `query_log.read_rows` | 102,368 | 218,592 | **+113.5%** |
| Native p50 HTTP latency | 37.270 ms | 48.052 ms | **+28.9%** |
| Native CPU/request (`OSCPUVirtualTime`) | 61.184 ms | 88.962 ms | **+45.4%** |
| A/A p50 control drift | | | **0.142%** |

These A/B/B/A numbers are medians/means over repeated native queries.
The `system.processors_profile_log` and `system.trace_log` rows below are
from **one representative query per variant**, NOT medians:

| Processor-work / trace | 3600s query | 900s query | Caveat |
|---|---:|---:|---|
| `MergeTreeSelect(ReadPool)` work, sum elapsed | 30,475 µs | 54,593 µs | work across instances/threads, not wall |
| `MergeTreeSelect(ReadPool)` output rows | 3,672 | 14,688 | only this processor class |
| `ExpressionTransform` work | 3,372 µs | 4,409 µs | sum over instances |
| `ExpressionTransform` input rows | 72,578 | 94,610 | output of multiple upstream branches |
| `AggregatingTransform` work | 1,383 µs | 2,286 µs | sum over instances |
| `AggregatingTransform` input rows | 8,569 | 19,585 | after query pipeline transforms |
| CPU trace `05_read_decode` samples | 33 | 57 | sampled diagnostic, not latency |
| CPU trace `01_lowcard_dictionary` samples | 1 | 0 | RAW UUID schema still has LC tag names |

The representative `MergeTreeSelect(ReadPool)` work increase (~24ms)
is of the same order as the measured total per-query CPU increase (~28ms).
This is **consistent with additional physical read/decode work**; it is not
a wall-time critical-path proof, and the sample counts do not establish
exact causal percentages. No mutex contention was measured here.

The corresponding LC UUID `pod_namespace` representative shows
`MergeTreeSelect` work 32,655→59,191µs and read/decode CPU samples29→57
at 3600→900s: the read-path increase also appears with the LC layout.

## Why longer buckets need a workload-specific decision

For RAW UUID `node_broad` **1d** at 3600→10800s:
- Native p50 **91.906→95.784ms** (+4.2%) even as
  read_rows **57,728→38,592** and compressed-read bytes
  decrease about6.7%. Native CPU **176.513→173.541ms** (slightly lower).
- Representative `MergeTreeSelect(ReadPool)` work **36,493→32,934µs**,
  `AdaptiveBucketMergingSource` work **44,612→45,142µs**.
  Extra wall time is **not explained by CPU of those two operators**;
  scheduling, pipeline dependencies or ordinary run variance remain possible.
  Do not assert a lock or algorithmic regression from one sample.

For RAW UUID `node_broad` **15m** across an hourly boundary,
3600→10800s instead yields native p50 **44.416→40.123ms** (-9.7%),
CPU **78.407→62.346ms** (-20.5%), read_rows **56,704→38,592**
with A/A drift1.07%; this is a real workload crossover.
For RAW UUID `pod_namespace [3h]`, p50 **36.965→33.951ms** (-8.2%)
with drift0.325%, but CPU changes only -1.5%; that effect needs
independent replay before a general CPU claim.

**Current decision:** 900s buckets fail this workload's latency/CPU test,
even at 15m/1h lookbacks. 10800s buckets save ~8% compressed disk and
some read CPU but do not dominate 3600s across 1d vs15m queries.
Neither bucket width should replace the 3600s default globally.

## Next nonduplicating test

The remaining falsifiable problem is separating `ReadFromMergeTree` CPU,
`AdaptiveBucketMergingSource` CPU, scheduler runnable wait, and
pipeline input/output backpressure under the **same** 1d broad selector
for 3600 vs10800s. Existing R10 full artifact already contains the
representative processors and full CPU categories; do not launch another
expensive run just to reprint them. If a fresh run is justified, retain
exact native A/B/B/A correctness and physical SHA pins and include
more realistic HPA/15s/60s write/merge and 14d/8-client memory gates.

Other R02 agent owns write/merge and R04 owns RecentSamples; reuse
their findings rather than creating duplicate CI. No master or
production settings changed.
