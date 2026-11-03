# R10: Source-backed bucket alignment CPU counterexample

Date: 2026-11-03, same experiment root R10, research only.
[Staggered full native ABBA CI #41618941657](https://github.com/moleus/ClickHouse/actions/runs/41618941657),
[SHA-pinned immutable report](../ts-lab/evidence/R10-staggered-native-41618941657.json),
same unmodified pinned ClickHouse source `52529f0c711566d696917678bf15e7068772fdde`,
stock O1 binary SHA256 `f0a2fa1f814c58853f43423705cac10ab9df3bfe5887e27ebe9620ba7cb9660e`.

## What changed and what did not

The old R01 generator aligns the three-hour life of all 400 node slots to
UTC multiples of 10800 seconds. The new opt-in fixture shifts each slot's
sample timestamps by `(slot * 317) % 10800` seconds without changing
metric names, tags, sample values, number of time series or samples.
Original SHA256
`ea9a8d43c9e58ed916f1bf43949556d8ed18169ddd14033c293af4cd54c9881d`
is retained. New shifted SHA256
`ae85496efcab3417634ac812cbd50a40c4eea297a397b50792efd024838e1b9a`.
Both contain 28,896 IDs and 2,488,320 original points.

The source-based count is exact: [`TimeSeriesSink.cpp::makeSamplesBlock`](https://github.com/moleus/ClickHouse/blob/52529f0c711566d696917678bf15e7068772fdde/src/Storages/TimeSeries/TimeSeriesSink.cpp#L447-L521)
makes **one row per series ID per bucket it occupies**, not per original point.
For each ID and step `b`, the number of rows is
`floor(last_sample_epoch/b) - floor(first_sample_epoch/b) + 1`.
The source generator enumerates every sample on a regular cadence, so
there are no skipped buckets between its first and last sample. Summing
over all 28,896 IDs yields the following **actual ClickHouse counts**
after `OPTIMIZE FINAL` (not just a prediction):

| SAMPLES step | Aligned rows (prior fixed fixture) | Shifted rows (new fixture) |
|---|---:|---:|
| 900s | 346,752 | 368,544 (unit test; no 900s ClickHouse table in staggered native run) |
| 3600s | 86,688 | **113,760 verified** |
| 10800s | 28,896 | **57,120 verified** |

The shifted 3600 and 10800 targets both contain 2,488,320 original points
and share the exact logical ID digest. Every inner table has one active
part; no change was made during read trials.
The shifted variant is an **alignment probe**, not a correct 400-active-node
population simulator across the first and final lifecycle boundary.

## Read/CPU counterexample: more CPU after fewer bytes and marks

One client, full native 3d daily-grid `prometheusQueryRange`,
`avg by(namespace)(avg_over_time(tslab_pod_cpu_ratio{namespace="team-000"}[1d]))`,
fixed **same shifted fixture**, RAW UUID in both SAMPLES layouts:

| Measurement | Bucket 3600s | Bucket 10800s | Effect |
|---|---:|---:|---|
| Native p50 ms | 73.726 | 85.345 | **+15.8%** |
| Native CPU/query ms | 135.859 | 168.388 | **+23.9%** |
| Native p95 ms | 80.006 | 92.501 | +15.6% |
| `query_log.read_rows` | 119,008 | 83,168 | **−30.1%** |
| `query_log.read_bytes` | 22,691,112 | 21,093,160 | **−7.0%** |
| `ReadCompressedBytes` | 4,904,827 | 4,818,664 | −1.8% |
| `SelectedMarks` | 294 | 154 | **−47.6%** |
| p50 A/A control drift | | | **1.68%** |

One representative query per variant, `processors_profile_log`:
- `MergeTreeSelect(ReadPool)` plus `ReadPoolInOrder` summed work:
  **89,217 µs** at 3600s vs **122,431 µs** at 10800s.
- On-CPU `05_read_decode` samples: **85→120**; total sampled CPU
  **127→164**. These samples are diagnostic, not query medians.
- Native per-query CPU increase ~32.5ms and representative read processor
  work increase ~33.2ms have matching scale, consistent with
  **more expensive physical read/decode per retained bucket row** despite
  fewer marks. This is an attribution hypothesis, NOT proof that data
  decompression alone is the cause: the reader may perform sample-array
  materialization, column transposition, or other work.

For the 15s scrape subset, 3600→10800s:
- native p50 **54.164→61.632ms (+13.8%)**, CPU **90.879→114.482ms (+26.0%)**,
  A/A drift5.08%;
- `read_rows` **66,272→63,712**, but `read_bytes`
  **11,973,992→14,144,088 (+18.1%)** despite fewer rows;
- representative total read processor work **58,106→83,061µs**,
  read/decode CPU samples55→78.

Therefore **physical read rows, selected marks, and compressed bytes are
not sufficient to predict CPU cost** for TimeSeries SAMPLES; row payload
(array length), data layout and downstream transformations matter.

## Cross-fixture causal limitation and control

The earlier aligned native factorial [#41613268106](https://github.com/moleus/ClickHouse/actions/runs/41613268106)
and current staggered run occurred on **different GitHub runners**.
The within-staggered 15.8% regression is real for this run, with 1.68%
control drift. But directly attributing the difference between the
aligned-vs-staggered speedup **ratios** solely to start-time alignment
requires one same-runner factorial.

To address this, independent nested same-runner experiment
[#41619669176](https://github.com/moleus/ClickHouse/actions/runs/41619669176)
uses exactly the two SHA-verified source fixtures and four RAW UUID tables,
with outer aligned/stagger/stagger/aligned fresh-server phases and
inner 3600/10800/10800/3600 native query phases. It keeps separate
native oracles per fixture; values for equal evaluation timestamps
are *not* necessarily equal across aligned vs shifted data.
No C++ compile is required. **No performance result should be assumed**
from this follow-up before its parity/drift checks complete.

## Next technical alternatives after that gate

If the same-runner interaction is confirmed, test whether a TimeSeries
storage read setting or different bucket step can reduce long-array decode
cost while preserving samples and time-pruning semantics. First localize
exact decoded/expanded bytes and operator CPU: `ReadFromMergeTree`,
`AdaptiveBucketMergingSource`, array materialization/decompression.
Do not suggest time-bucket tuning from only `SelectedMarks`, and do not
call OS scheduler wait mutex contention. Keep read/ingest/merge and
official jemalloc allocator checks separate.
