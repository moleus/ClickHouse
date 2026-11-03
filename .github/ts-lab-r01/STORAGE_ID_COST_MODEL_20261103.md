# R01: TimeSeries UUID economics depend on **bucket rows**, not sample count

Date: 2026-11-03. Research-only, branch `lab/ts-cbo-adaptive-20261103`.
Source SHA `52529f0c711566d696917678bf15e7068772fdde`, unmodified
stock O1 binary SHA256 `f0a2fa1f814c58853f43423705cac10ab9df3bfe5887e27ebe9620ba7cb9660e`.
Synthetic fixture SHA256
`ea9a8d43c9e58ed916f1bf43949556d8ed18169ddd14033c293af4cd54c9881d`.
[Generator](generate_k8s_churn_fixture.py);
[distribution CI #41611534144](https://github.com/moleus/ClickHouse/actions/runs/41611534144).

## Corrected source-backed cost model

Earlier R01 claim used samples per series in its illustrative estimate for a
LowCardinality(UUID) ID column. That is not the right physical unit for the
ClickHouse v11 SAMPLES inner table.

- [Pinned StorageTimeSeries.cpp, samples schema](https://github.com/moleus/ClickHouse/blob/52529f0c711566d696917678bf15e7068772fdde/src/Storages/StorageTimeSeries.cpp#L1098-L1116):
  `samples SimpleAggregateFunction(timeSeriesGroupArray,
  Array(Tuple(timestamp,value)))`. One row contains the **array of samples**
  for one series ID within one bucket.
- [Pinned TimeSeriesSink.cpp](https://github.com/moleus/ClickHouse/blob/52529f0c711566d696917678bf15e7068772fdde/src/Storages/TimeSeries/TimeSeriesSink.cpp#L575-L646)
  sorts samples then groups them into bucket rows using the configured step.
  The stock default is `samples_bucket_step_seconds=3600`;
  `recent_samples_ttl_seconds=0` disables the second samples target.
- [Pinned SerializationLowCardinality.cpp](https://github.com/moleus/ClickHouse/blob/52529f0c711566d696917678bf15e7068772fdde/src/DataTypes/Serializations/SerializationLowCardinality.cpp#L716-L756)
  delegates dictionary-key remapping to
  `ColumnLowCardinality::insertRangeFromDictionaryEncodedColumn`.
  The [pinned implementation](https://github.com/moleus/ClickHouse/blob/52529f0c711566d696917678bf15e7068772fdde/src/Columns/ColumnLowCardinality.cpp#L304-L345)
  validates indexes, inserts unique dictionary keys and maps the row indexes
  (with `ColumnUnique::uniqueInsertRangeFrom`).
  The cost can track **distinct keys per decoded block** and index-array rows,
  not only sample-array elements.

For D distinct series, R bucket rows, S original sample points, and a
hypothetical 2-byte LC dictionary index width, an **uncompressed** first-order
UUID-component model is

- Raw UUID field: approximately `16 * R` bytes.
- LC UUID field: approximately `16 * D + 2 * R` bytes, plus metadata,
  dictionary headers/offsets, potential replicated local dictionaries
  and runtime reverse-index hashing.
- Maximum possible precompression raw-minus-LC difference in this model:
  `14 * R - 16 * D`. This is NOT a prediction of compressed on-disk bytes
  or wall time. Dictionary index width and dictionary locality must be
  measured, not assumed.

**For the exact R01 fixture:**
- 400 active slots, 3h node life, 3 days, 28,896 unique metric/tag IDs.
- 9,600 node 60s + 19,200 pod 300s + 96 burst pod 15s **series**.
- 2,488,320 points; average **86.12 points per series**.
- Yet each 3h life uses exactly **3 hourly sample buckets**.
  Therefore `R=86,688` inner SAMPLES rows, and `R/D=3`,
  regardless of whether the series has 36, 180 or 720 points.
- The idealized uncompressed 2-byte-index saving is
  `14*86688-16*28896 = 751,296 bytes`, only around 0.72 MiB,
  not tens of MiB derived from 2.49m samples.

## Actual pinned same-fixture physical results

First instrument run [#41611838191](https://github.com/moleus/ClickHouse/actions/runs/41611838191),
artifact `13219461708`. The **data ingest and OPTIMIZE FINAL succeeded**;
physical part counts/types and SHA are in `r01/report.compact.json`.
The run **did not measure query speed**, because an incorrect benchmark
assertion compared 86,688 bucket rows to 2,488,320 sample elements.
That is a measurement-method failure, not a ClickHouse bug or a semantic failure.

| v11 inner table | LC UUID bytes | RAW UUID bytes | LC minus RAW bytes | Same physical rows |
|---|---:|---:|---:|---:|
| TAGS | 1,544,707 | 1,486,557 | 58,150 | 28,896 |
| TimeRanges | 693,006 | 634,858 | 58,148 | 28,896 |
| SAMPLES | 5,332,102 | 5,303,391 | 28,711 | **86,688 bucket rows** |
| **Total** | **7,569,815** | **7,424,806** | **145,009** | — |

The RAW schema occupies about **1.92% less compressed disk** across the
three targets, even though the original logical series have repeated samples.
This result is restricted to this 3d, fixed-cadence, one-part-per-target
synthetic fixture and these codec/part configurations.

**Correction made:** [bench_k8s_mixed_native.py](bench_k8s_mixed_native.py)
now independently asserts `sum(length(samples)) = 2,488,320` and
`count() = system.parts.rows` for the SAMPLES inner tables; it no longer
equates bucket rows and sample elements. An isolated path-triggered
workflow reruns the test [#41612120162](https://github.com/moleus/ClickHouse/actions/runs/41612120162).
Do not publish latency/CPU conclusions until this run finishes all
native PromQL output and A/B/B/A drift gates.

## Implications and falsifiable follow-up

1. A raw-ID win or loss must be explained by **bucket row count and distinct
   ID density**, not merely the original scrape interval or points/series.
   Distinct ID count and stored samples are separate costs.
2. Optional later *bucket-step x ID-type factorial*: with the same 3h-life
   fixture, compare `samples_bucket_step_seconds=900/3600/10800` and
   LC/RAW across a controlled 2x3 design. Physical rows per 3h-life
   shift roughly **12→3→1**; more bucket rows increase ID duplication
   but can change time pruning, aggregation and mark selection. Read CPU,
   write/merge CPU, exact native results and RSS must decide the tradeoff.
   Do not globally change bucket duration without a query-window matrix.
3. The concurrent **other R01 generator** at
   [r01_kubernetes_workload.py](../ts-lab/experiments/r01_kubernetes_workload.py)
   models HPA, staggered node lifetime and counters with **one sample
   per JSONEachRow**. It is complementary and should not be replaced:
   this fixture groups samples into multi-point JSON records to isolate
   repeated-ID storage economics. Neither is measured production data.

No master, upstream, old branch, production resources or schedules changed;
all older CI failures remain visible.
