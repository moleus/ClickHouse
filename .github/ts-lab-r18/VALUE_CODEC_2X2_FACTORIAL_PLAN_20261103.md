# R18 dependent gate: isolate ALP transform from ZSTD level (2×2)

**Status:** Pre-registered on 2026-11-03, prior to new CI. Same
canonical R18 root and single lab branch. No new root reservation:
this is a strictly narrower method follow-up for the existing
`R18-timeseries-samples-value-codec-v1` research question.

## Confound in prior R18 results

Original R18 benchmark compared the stock value subcolumn
`CODEC(ALP,ZSTD(3))` to `CODEC(ZSTD(1))`. This changes **two**
variables simultaneously:

1. The lossless ALP floating-point transform is removed.
2. The ZSTD compression level changes from 3 to 1.

The observed SAMPLES byte changes (−35.6% original repeating values,
−16.6% smooth, **+2.7%** high entropy) are **valid end-to-end results
for the two full codecs**, but do **not isolate the ALP transform**.
A source-level explanation about ALP alone is not justified.

## Controlled 2×2

| Target | ALP transform | ZSTD level |
|---|---|---|
| `alp_z3` | yes | 3 |
| `alp_z1` | yes | 1 |
| `raw_z3` | no | 3 |
| `raw_z1` | no | 1 |

All four **identically explicit** v11 TimeSeries SAMPLES inner DDL
columns, changing *only* `CODEC samples.value (...)`. Float64 type,
source version, hash ID, RAW UUID on all targets, 1h samples bucket,
timestamps `CODEC(Delta(8),T64,ZSTD(3))`, tags, other columns,
RecentSamples off, data and queries fixed.

Compute within-runner four paired A/B/B/A comparisons:

- ALP effect at level3: `alp_z3` vs `raw_z3`.
- ALP effect at level1: `alp_z1` vs `raw_z1`.
- ZSTD-level effect without ALP: `raw_z3` vs `raw_z1`.
- ZSTD-level effect with ALP: `alp_z3` vs `alp_z1`.

For storage report exact bytes and 2×2 interaction with additive and
multiplicative definitions. For native CPU/latency, report paired
effects, not a comparison of unpaired means.

Two separately pinned data distributions: original R01 aligned
repetitive-gauge SHA256
`ea9a8d43c9e58ed916f1bf43949556d8ed18169ddd14033c293af4cd54c9881d`,
and held-out R18 high-entropy SHA256
`4b81b190cbcff7af58682e8c023cebfad22e3bb98afb591d8b2d9928eade9d57`.
Each data distribution gets an independent runner. Absolute times
cannot be compared across runners, but within-distribution paired
factors can.

**Falsifiers:** effective `SHOW CREATE` lacks expected codec,
mismatched target DDL apart from codec, sample-element/ID hash count
changes, altered physical bucket rows/marks, full native PromQL labels/
timestamps/value mismatch, OOM, missing per-query CPU, missing
QueryFinish, or >25% baseline A/A drift.

Per query: `pod_namespace`, `node_broad`, `burst15`, same full
native 3d daily-grid `avg by(...)(avg_over_time(...[1d]))`,
2 warmup +16 measured requests per A/B/B/A phase. Profile
representative read/decode versus grid CPU but do not infer mutex
or per-function causal CPU shares from sampled stacks.

**Resource gate:** single stock O1 binary restored by SHA from
GitHub #41596421285, no C++ compiler; each runner creates 4 tables
(avoids the memory cost of 8 concurrent physical targets), <=70min
timeout, `max-parallel:2`, workflow triggered *only* by its own path,
run-unique concurrency, cancel false, artifacts even after failure.

The ongoing 1/4/8-client R18 stagger/codec CI is independent and
must not be cancelled. No old branches, scheduled workflows, master
or production changes.
