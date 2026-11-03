# R18: v11 TimeSeries SAMPLES.value codec — native read, write and size

**Date:** 2026-11-03. **Research branch:** `lab/ts-cbo-adaptive-20261103`.
[Valid full native CI #41630585794](https://github.com/moleus/ClickHouse/actions/runs/41630585794),
[immutable machine-readable R18 evidence](../ts-lab/evidence/R18-samples-value-codec-41630585794.json).
Full [GitHub artifact #13228102138](https://github.com/moleus/ClickHouse/actions/runs/41630585794/artifacts/13228102138)
SHA256 `6d2723031f822b3aa9838cef66002b83291c133235f78d08e9ddc24d4f694a89`,
verified from ZIP contents; `r18/report.json` holds all 320 trial queries and
sampled operator/trace profiles.

## Controlled identity and intervention

- One unmodified ClickHouse 26.11 stock **LLVM 22.1.8 O1 glibc** executable
  SHA256 `f0a2fa1f814c58853f43423705cac10ab9df3bfe5887e27ebe9620ba7cb9660e`,
  source commit `52529f0c711566d696917678bf15e7068772fdde`.
  Runner **AMD EPYC 9V45 96-Core Processor, 4 logical CPUs**.
- Same deterministic original R01 **aligned** 400-node / 3-day fixture SHA256
  `ea9a8d43c9e58ed916f1bf43949556d8ed18169ddd14033c293af4cd54c9881d`.
  28,896 series, 2,488,320 finite gauge points and 86,688 hourly SAMPLES rows.
- Three v11 TimeSeries tables. **Only** `samples.value` subcolumn codec differs:
  default `CODEC(ALP,ZSTD(3))`, `CODEC(ZSTD(1))`, `CODEC(LZ4)`.
  Same Float64, RAW UUID id `Tuple(UInt64,UUID)` in TAGS/TimeRanges/SAMPLES,
  same hash/label index, `samples.timestamp CODEC(Delta(8),T64,ZSTD(3))`,
  one-hour buckets, recent samples disabled.
- Actual effective inner SAMPLES DDL checked with `SHOW CREATE TABLE`.
  `OPTIMIZE FINAL` all inner targets, stop merges, exactly one part per target.
  Equal ID count/digest `3199471147651921930` and exact sample-element
  count, all query labels/timestamps/values equal to ALP reference.
- Eight `ALP/candidate/candidate/ALP` tests: four native
  `avg by(project|namespace)(avg_over_time(metric[1d]))` selectors × two
  codecs. Each of four phases has **2 warmup and 8 measured** requests;
  `max_threads=2`. Total 320 requests plus four oracle queries.
  Full QueryFinish CPU, p50/p95, read marks/rows/bytes, query memory,
  representative CPU trace profiles and immutable part-layout checks.
  All eight A/A controls pass ≤25% drift and no OOM.

## 1. Exact compressed storage after merge

| Total compressed bytes | ALP + ZSTD(3) | ZSTD(1) | LZ4 |
|---|---:|---:|---:|
| SAMPLES | 5,303,391 | **3,414,486 (−35.6%)** | 3,773,263 (−28.9%) |
| TAGS | 1,486,557 | 1,486,557 | 1,486,557 |
| TimeRanges | 634,858 | 634,858 | 634,858 |
| **All three** | **7,424,806** | **5,535,901 (−25.4%)** | **5,894,678 (−20.6%)** |

All three have 86,688 SAMPLES rows, **340 marks** and identical metadata
parts. The substantial compressed-size saving of simpler codecs is
**a measured property of this highly patterned synthetic gauge input**,
not proof that ALP is generally inferior on production Float64 series.
Time-series `ALP` is a lossless float transform (including exceptions);
a different value distribution may reverse this storage result.

## 2. Native PromQL median wall and CPU

CPU in milliseconds per request, from
`QueryFinish.ProfileEvents.OSCPUVirtualTimeMicroseconds`.
Paired same-runner values use independent ABBA for each alternative.

| Selector | ALP → ZSTD(1) wall p50 | ALP → ZSTD(1) CPU | A/A wall / CPU drift |
|---|---|---|---|
| node_broad | 75.744 → 72.347 ms (**−4.5%**) | 115.136 → 108.109 ms (−6.1%) | 1.32% / 3.85% |
| pod_namespace | 27.940 → 29.730 ms (**+6.4%**) | 36.886 → 40.558 ms (**+10.0%**) | 2.10% / **0.80%** |
| pod_broad | 81.790 → 77.555 ms (−5.2%) | 124.929 → 117.763 ms (−5.7%) | 4.28% / 8.42% |
| burst15 | 24.939 → 25.835 ms (+3.6%) | 31.552 → 32.954 ms (+4.4%) | 8.57% / 6.64% |

The **namespace CPU regression** from ZSTD1 is much larger than
its CPU A/A control drift. Broad-node performance may improve
slightly, but it is not a compelling universal effect.
Broad-pod and burst changes are comparable with normal control variation.

| Selector | ALP → LZ4 wall p50 | ALP → LZ4 CPU | A/A wall / CPU drift |
|---|---|---|---|
| node_broad | 72.906 → 71.525 ms (−1.9%) | 108.827 → 107.138 ms (−1.6%) | 2.58% / 3.01% |
| pod_namespace | 29.111 → 29.003 ms (−0.4%) | 39.000 → 38.910 ms (−0.2%) | 4.15% / 8.28% |
| pod_broad | 87.602 → 87.936 ms (+0.4%) | 131.662 → 133.156 ms (+1.1%) | 5.20% / 6.06% |
| burst15 | 26.686 → 25.625 ms (−4.0%) | 33.357 → 32.255 ms (−3.3%) | 2.71% / 1.28% |

LZ4 shows no **large stable** overall latency/CPU advantage. The
small burst15 effect needs independent replay and a different value
distribution before promotion.

**Important:** [R17 output-format run](../ts-lab/evidence/R17-output-format-cpu-41628966700.json)
used **AMD EPYC 9V74**, while R18 uses **AMD EPYC 9V45**.
Do **not** compare absolute CPU or wall times from R17 to R18 as if
they were matched; all R18 conclusions use same-runner pairs.

## 3. Write and merge — descriptive only

One sequential fixture ingest and one `OPTIMIZE FINAL` per codec,
not randomized repeated writer ABBA:

| Codec | Ingest wall | Whole-server CPU during ingest | OPTIMIZE wall | Whole-server CPU during OPTIMIZE |
|---|---:|---:|---:|---:|
| ALP+ZSTD3 | 1.5892s | 0.9686s | 0.2202s | 0.2533s |
| ZSTD1 | 1.5029s | 0.8815s | 0.1724s | 0.1710s |
| LZ4 | 1.5063s | 0.8855s | 0.1718s | 0.2010s |

Process-wide CPU snapshots include background work. These
single-shot measurements cannot establish steady-state ingest
CPU savings, natural merge amplification or production write QPS.
Reuse R02's steady-write methodology for the next gate.

## 4. Diagnostic CPU stacks, not a critical-path proof

Representative **one-query** sample counts in this runner:
- `node_broad`: default ALP `read_decode` 17, grid44,
  tags16; ZSTD1 read14, grid45, tags11.
- `pod_namespace`: ALP read19; ZSTD1 read21.
- `pod_broad`: ALP read22, grid37, tags39; ZSTD1
  read15, grid36, tags35.
- `burst15`: ALP read15; ZSTD1 read14.

These tiny samples cannot establish the precise cause of
the namespace regression or the broad-node improvement. The
`MergeTreeSelect` processor includes decoding all relevant
SAMPLES and metadata columns, not only `samples.value`.
`input_wait`, `output_wait`, scheduler runnable delay and
summed processor work are **not mutex contention or query wall time**.

Source proof:
[`StorageTimeSeries.cpp`](https://github.com/moleus/ClickHouse/blob/52529f0c711566d696917678bf15e7068772fdde/src/Storages/StorageTimeSeries.cpp#L1055-L1117)
permits the value subcolumn codec override in `SAMPLES INNER COLUMNS`.
[`CompressionCodecALP.cpp`](https://github.com/moleus/ClickHouse/blob/52529f0c711566d696917678bf15e7068772fdde/src/Compression/CompressionCodecALP.cpp)
implements the lossless float transform.

## 5. Preserved invalid first CI and decision

[First CI #41630357537](https://github.com/moleus/ClickHouse/actions/runs/41630357537)
failed a **test assertion before ingest**, not a source/DDL failure:
the benchmark looked for unquoted `samples.value` and `Delta`,
while `SHOW CREATE` prints backticks and `Delta(8)`. The
corrected parser removes quoting and accepts equivalent timestamp
codec rendering, and run [#41630585794](https://github.com/moleus/ClickHouse/actions/runs/41630585794)
passed every gate. Keep both histories; the first supplies no timing.

**Scientific decision: ACCEPT_NEXT_GATE_ONLY.** Do not change the
global code for value codec. A good follow-up changes only the **value
distribution** (constant/smooth/high-entropy Float64, possibly
counter resets) on the same fixed timestamps/labels to check whether
ZSTD1's 35.6% storage saving is tied to the repetitive-gauge fixture,
and repeats reader ABBA plus write/merge. Keep source, ID and bucket
fixed. No new C++ build or production mutation.
