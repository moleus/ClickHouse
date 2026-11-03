# R17: JSON output is not the main native TimeSeries CPU cost

Date: 2026-11-03. Only branch `lab/ts-cbo-adaptive-20261103`.
[CI #41628966700](https://github.com/moleus/ClickHouse/actions/runs/41628966700) **PASS**,
[immutable evidence](../ts-lab/evidence/R17-output-format-cpu-41628966700.json),
[benchmark source](bench_native_output_floor.py),
[isolated workflow](../workflows/ts-lab-r17-output-floor-20261103.yml).
Artifact `tslab-r17-native-output-floor-41628966700` ID `13228015124`,
ZIP SHA256 `2276b147331c9393dc57652dd907762ad03cd6caf6ef26ec3dd3a50798816892`,
independently checked against the downloaded ZIP.

## Controlled design

Same stock ClickHouse O1 source `52529f0c711566d696917678bf15e7068772fdde`,
LLVM22.1.8 unmodified binary SHA256
`f0a2fa1f814c58853f43423705cac10ab9df3bfe5887e27ebe9620ba7cb9660e`.
The same R01 400-node aligned 3-day fixture SHA256
`ea9a8d43c9e58ed916f1bf43949556d8ed18169ddd14033c293af4cd54c9881d`,
28,896 series, 2,488,320 gauge points, one v11 RAW UUID TimeSeries
table, TAGS/TimeRanges/SAMPLES each one physically frozen part.

For each native `prometheusQueryRange` selector `node_broad`,
`pod_namespace`, `pod_broad`, `burst15`, compare identical
SQL, changing only `FORMAT JSONEachRow` ↔ `FORMAT Null`.
ABBA order JSON/Null/Null/JSON, 2 warmup + 24 measured trials per
phase, 416 total queries, `max_threads=2`, same stock executable,
runner and on-disk data. Every JSON query must match the original
nonempty complete PromQL labels/timestamps/numeric oracle. Null
queries must return an empty HTTP body; physical read_rows and
selected marks must equal JSON, and before/after part counts/rows/
marks/bytes must match. All gates passed; all source/query SHA checks
and controls passed.

**Source semantics:** [`NullFormat.h`](https://github.com/moleus/ClickHouse/blob/52529f0c711566d696917678bf15e7068772fdde/src/Processors/Formats/Impl/NullFormat.h)
implements `NullOutputFormat::consume(Chunk) override {}`, and
[`NullFormat.cpp`](https://github.com/moleus/ClickHouse/blob/52529f0c711566d696917678bf15e7068772fdde/src/Processors/Formats/Impl/NullFormat.cpp)
states the query is still processed.
[`JSONEachRowRowOutputFormat.cpp`](https://github.com/moleus/ClickHouse/blob/52529f0c711566d696917678bf15e7068772fdde/src/Processors/Formats/Impl/JSONEachRowRowOutputFormat.cpp)
converts each column field to JSON. FORMAT Null removes *output work*
but can also change pipeline scheduling. It is **not a pure serializer
profiler**.

## Native measured results

Median QueryFinish `OSCPUVirtualTimeMicroseconds` in milliseconds.
The reported values average two phase medians; they are not
cross-runner comparisons.

| Selector | Native output bytes | JSON CPU | Null CPU | JSON wall p50 | Null wall p50 | JSON/JSON CPU A/A drift |
|---|---:|---:|---:|---:|---:|---:|
| node_broad | 1,530 | 411.338 | **440.113** | 256.781 | 277.783 | 1.99% |
| pod_namespace | 191 | **94.337** | 93.260 | 67.257 | 66.177 | 0.59% |
| pod_broad | 3,057 | 374.139 | 376.256 | 238.737 | 241.139 | 0.16% |
| burst15 | 767 | 67.899 | 68.010 | 51.443 | 50.918 | 0.53% |

Read rows and selected marks were exactly the same within each A/B/B/A:
- node_broad: 57,728 rows / 117 marks;
- pod_namespace: 102,368 / 229;
- pod_broad: 103,392 / 233;
- burst15: 66,016 / 87.

For `pod_namespace`, omitting **191 bytes** improves CPU only
**1.077 ms (1.14%)** on this benchmark. The p50 difference (~1.08ms)
is small. For `burst15`, removing the output causes no meaningful CPU
change despite emitting zero bytes. For broad `node_broad`, Null
**slows** wall by ~21ms and CPU by ~29ms even with identical
read_rows/marks. This cannot be interpreted as a *negative cost* of
JSON. The output sink changes a pipeline condition or execution
scheduling, and further source inspection is needed before assigning
causality.

Hence the 3–41× stock O1 ClickHouse versus optimized VM gap measured
in [R03](../ts-lab/evidence/R03-ch-vm-k8s-concurrent-41624246411.json)
is **not plausibly explained mainly by JSON serialization** on the
selective low-output-cardinality cases. R03's optimization and allocator
differences remain a major cross-engine confound.

## What the profiler actually shows

The artifact contains CPU trace samples and
`processors_profile_log` for **one representative measured query**
per format and selector. These are **not medians** and do not measure
mutex contention or the critical path.

| Selector (JSON reference) | CPU trace categories, selected samples | Processor work (sum across instances, not wall) |
|---|---|---|
| `pod_namespace` | `read_decode` 45/90, `other` 27/90, `grid` 8/90 | `ReadFromMergeTree` 40.874ms, `ExpressionTransform` ~9.8ms |
| `burst15` | `read_decode` 29/60, `other` 24/60 | `ReadFromMergeTree` 28.330ms, `ExpressionTransform` ~5.7ms |
| `node_broad` | `grid` 128/413, `expression` 121/413, `read` 54/413, `tags` 42/413 | `ExpressionTransform` ~165.5ms, `AdaptiveBucketMergingSource` ~73.3ms, `ReadFromMergeTree` ~55.1ms |
| `pod_broad` | `grid` 131/408, `tags` 107/408, `expression` 67/408, `read` 51/408 | `ExpressionTransform` ~154.2ms, `AdaptiveBucketMergingSource` ~80.5ms, `AggregatingTransform` ~47.6ms |

The hottest individual sampled leaf for all four JSON queries
includes `__llvm_libc::memset_gt128_baseline` (e.g. 28 samples on
`pod_namespace`, 204 on `node_broad`); its stack often passes
through `MergeTreeReaderWide` and sample-array deserialize/
decompress methods. This is evidence of memory writes during reads,
**not** a proof that changing one compression codec or replacing
`memset` will accelerate native PromQL. Other leaves include
ZSTD/ALP decode, malloc/free and sometimes
`ContextTimeSeriesTagsCollector` cleanup.

**Workload-dependent targets:** selective namespace/burst query
CPU is concentrated in physical read/decode; broad pod/node query
CPU additionally includes grid aggregation, expression evaluation
and tag registration. A single global output formatter optimization
cannot address either category of dominant work.

## Decision and next gate

**Scientific decision: REJECT output formatting as a primary CPU
optimization for these small PromQL responses.**
The correct next independent cost dimension is to isolate the actual
SAMPLES physical serialization codec / decompression and allocation,
or grid-array materialization with source-verified columns and
matched C++-free DDL. Before another expensive build, derive a
plausible CPU bound from the representative stacks, exact source
column CODEC handling and data-file substreams. Do not repeat
R03 cross-engine runs, R05 ID-mark optimization, R07 metadata sidecar
or R14 exact grid-rollup experiments under a new name.

No production, master, old branches or live builds changed.
