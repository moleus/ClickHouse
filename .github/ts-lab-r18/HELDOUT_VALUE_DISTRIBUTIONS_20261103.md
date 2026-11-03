# R18 held-out Float64 distributions: codec ordering reverses

Date: 2026-11-03. Same unified lab branch, research-only.
[Two-job CI #41631747983](https://github.com/moleus/ClickHouse/actions/runs/41631747983),
[full structured evidence](../ts-lab/evidence/R18-value-pattern-holdout-41631747983.json).
Both jobs **SUCCESS**. No C++ build or production/old-branch changes.

## Matched experimental identity

Same unmodified pinned stock O1 ClickHouse binary SHA256
`f0a2fa1f814c58853f43423705cac10ab9df3bfe5887e27ebe9620ba7cb9660e`,
source `52529f0c711566d696917678bf15e7068772fdde`.
Same R01 labels, metric names, row order, sample timestamps, 400 active slots,
3-hour node lifetimes, 28,896 distinct IDs, 2,488,320 total samples,
86,688 hourly SAMPLES buckets, 340 marks, TAGS/TimeRanges metadata.
Each new fixture derives **only** Float64 sample values from the
original SHA256 `ea9a8d43c9e58ed916f1bf43949556d8ed18169ddd14033c293af4cd54c9881d`.

- **Smooth:** rounded, small-step finite gauge values 0.125001–0.127216.
  Fixture SHA256 `fd380b0fb4b60f62ef3d100ffe6e0c865935ce5f71f585202b670439f22ad2b2`.
- **Entropy:** deterministic SplitMix64-derived 53-bit finite Float64
  in (0,1), deliberately poor for repeated-value compression.
  SHA256 `4b81b190cbcff7af58682e8c023cebfad22e3bb98afb591d8b2d9928eade9d57`.

Per value family a **different isolated GitHub runner** (both AMD EPYC
7763 model, four logical CPUs). Within each runner: default
`samples.value CODEC(ALP,ZSTD(3))` versus explicit
`CODEC(ZSTD(1))`; exact effective DDL, identical RAW UUID/Float64/
Delta(8)-T64-ZSTD3 timestamp codecs and 3600-second bucket.
Four full native PromQL selectors, each ALP/ZSTD/ZSTD/ALP with
2 warmups +8 measured query trials per phase, 160 requests
plus four oracles per fixture. Strict same-pattern nonempty labels,
timestamps and numeric values; sample counts, tag digest and
physical part equality. Eight total ABBA pairs PASS and no OOM.
Source [fixture generator](generate_value_pattern_fixture.py),
[bench runner](bench_samples_value_codec.py) and
[self-path workflow](../workflows/ts-lab-r18-value-pattern-holdout-20261103.yml).

## The compressed-size crossover

| Distribution | SAMPLES ALP+ZSTD3 | SAMPLES ZSTD1 | ZSTD1 vs ALP |
|---|---:|---:|---|
| Original low-range repeating gauges [#41630585794](https://github.com/moleus/ClickHouse/actions/runs/41630585794) | 5,303,391 B | 3,414,486 B | **−35.6%** |
| Smooth/quantized gauges (heldout) | 4,718,192 B | 3,937,064 B | **−16.6%** |
| Full-precision high-entropy gauges (heldout) | **19,686,139 B** | 20,216,284 B | **+2.7%** |

The ID/tag/timestamp shape is identical. Only Float64 values vary.
Thus the **compressed SAMPLES storage winner flips**: using
uncompressed sample count, cardinals or 1h bucket count alone
cannot predict the value-codec winner.

Total compressed three-target bytes:
- Repetitive: 7,424,806 ALP versus5,535,901 ZSTD1;
- Smooth: 6,839,607 ALP versus6,058,479 ZSTD1;
- High entropy: **21,807,554 ALP versus22,337,699 ZSTD1**.

This is a direct, checked counterexample to replacing the
default ALP codec globally based on the first repetitive fixture.

## Native read CPU is also not determined by bytes alone

Values are paired within each fixture's runner. No absolute
cross-runner timing comparisons are valid.

| Heldout distribution and native selector | ALP p50 → ZSTD1 p50 | ALP CPU/query → ZSTD1 CPU/query | CPU A/A drift |
|---|---|---|---:|
| smooth node_broad | 235.425→226.281ms (−3.9%) | 375.483→359.450ms (−4.3%) | 0.41% |
| smooth pod_namespace | 75.672→69.297ms (−8.4%) | 105.299→95.799ms (−9.0%) | 5.83% |
| smooth burst15 | 54.957→51.166ms (−6.9%) | 74.233→67.902ms (−8.5%) | 2.98% |
| entropy node_broad | 251.766→238.296ms (−5.4%) | 398.822→379.944ms (−4.7%) | **11.39%** |
| entropy pod_namespace | 75.091→70.717ms (−5.8%) | 107.805→99.317ms (−7.9%) | **1.60%** |
| entropy pod_broad | 230.385→243.039ms (+5.5%) | 358.039→376.731ms (+5.2%) | **14.26%** |

On the ORIGINAL repetitive-gauge fixture `pod_namespace` instead
had ZSTD1 CPU **36.886→40.558ms (+10.0%)**, CPU A/A drift **0.80%**.
This is a genuine workload-specific sign reversal. Do not extrapolate
an observed CPU benefit to production without value distribution
and source/allocator parity.

For the entropy node-broad and pod-broad selectors, measured
changes are **much smaller than their baseline drift**, so those
CPU deltas are inconclusive. Same-runner matched samples and
same marks/rows are necessary but not sufficient to claim robust
benefits of only a few percent.

## Write/merge and remaining gates

On each heldout fixture both ALP and ZSTD1 loaded 28,896 rows,
2,488,320 points and merged into one part per layer.
One unpaired ingest/OPTIMIZE showed lower whole-server CPU and
wall for ZSTD1, but these are **single sequential measurements**;
they do not prove natural rolling merge or repeated-write performance.

**Decision: do not change the default value codec globally.**
Both compressed storage and native CPU have verified counterexamples
across controlled synthetic distributions. Next research should
measure real-like value entropy/quantization and NaN/Inf/counter
semantics, then run bounded write/merge plus multi-client tests under
same optimized allocator/flags. No C++ compiler rebuild is justified
by these results alone.

Artifacts independently downloaded and ZIP SHA verified:
- smooth `13228487441`: `91390698faf5afa5239ebaa460b371487d15fc0e49d5a59415cb62bd1ebd7cd5`;
- entropy `13229225029`: `00e55c4ea87083f83e41f91e7a171e5e044c8ae69e402f770750305dba56b1e2`.
