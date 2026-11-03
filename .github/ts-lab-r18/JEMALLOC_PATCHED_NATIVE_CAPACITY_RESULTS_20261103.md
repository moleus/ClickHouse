# R18 patched-O1 jemalloc native query and capacity gate

Date: 2026-11-03. Unified branch `lab/ts-cbo-adaptive-20261103`.
[Successful full-native CI #41637869309](https://github.com/moleus/ClickHouse/actions/runs/41637869309),
[immutable exact-run evidence](../ts-lab/evidence/R18-jemalloc-patched-native-capacity-41637869309.json),
[binary source and runtime verification](../ts-lab/evidence/R18-jemalloc-patched-cxx-artifact-readiness-41607779848.json),
[preregistered gates](JEMALLOC_PATCHED_NATIVE_CAPACITY_GATE_20261103.md).
Workflow `.github/workflows/ts-lab-r18-jemalloc-native-capacity-20261103.yml`
does not compile ClickHouse; it restores the already completed
three-and-a-half-hour O1+jemalloc source build
[#41607779848](https://github.com/moleus/ClickHouse/actions/runs/41607779848).

## Reproducible executable provenance

The reused build artifact ZIP 13230128180 SHA256
`9e716f524c0802a29d42cc542accc3b388b2766920ae04936687ad101e6e2295`
contains `clickhouse-cbo-jemalloc-o1-linux-amd64.tar.zst` SHA256
`9c20ccdad5a689765e1945c1af2a9985507c536f7c121229831d987f2a2bd21f`.
Its unpacked ClickHouse executable SHA256
`26b1577cea637b98a1d0b3c9efc996cd266234bf6ef89bd1912c75c56098eed3`
was confirmed by both local extraction and independent GitHub CI.
Source pin `52529f0c711566d696917678bf15e7068772fdde`,
LLVM 22.1.8 `-O1`, `-DNDEBUG`, `ENABLE_JEMALLOC=ON`.

**Important non-isolated variable:** unlike original stock O1 binary
SHA256 `f0a2fa1f814c58853f43423705cac10ab9df3bfe5887e27ebe9620ba7cb9660e`,
the jemalloc executable also has two exact C++ source patches:
(1) conditional user JOIN override in `StorageTimeSeriesSelector.cpp`;
(2) narrow `sum(rate(exact_metric))` fastpath in PromQL `Converter.cpp`.
Its full patch SHA256 is
`9b276978f7824e1b0e368c873e06ee0f3a4ccccbd8f6bdd90b28b497bf1a848c`.
The R18 queries are `avg by(...)(avg_over_time(...[1d]))`
with no explicit join algorithm override, and do not match the fastpath.
This source observation **reduces semantic concern** but it
does **not** turn cross-run glibc-vs-jemalloc comparisons into
a rigorously isolated allocator A/B. Both are O1, not
standard optimized production binaries.

## Proved runtime jemalloc, not only CMake enabled

The **running isolated ClickHouse server** (not a separately
launched binary) returned:
- `system.jemalloc_bins` **232 rows** before and after workload,
  `sum(curregs)` **135,344** before and **931,064** after.
- `system.jemalloc_stats` includes jemalloc 5.3-RC, active
  profile and background worker, at end of workload:
  allocated **274,486,128 bytes**, active **319,328,256 bytes**,
  resident **709,820,416 bytes**, retained virtual
  **1,027,268,608 bytes**.
- Jemalloc's `retained` is **not allocated resident memory**.
  These allocator internal snapshots are taken after workload,
  not at exactly the instant of peak process RSS.

Full artifact from the **successful** query run:
[ZIP 13231197618](https://github.com/moleus/ClickHouse/actions/runs/41637869309/artifacts/13231197618),
name as actually uploaded
`tslab-r18-stagger-codec-concurrency-41637869309`,
SHA256 `0f3e92b6ea153f63c1ebbac4789310eebee16b7a65575502f0cd55fe1b424a4f`.
It includes 7,419,912-byte `r18-jemalloc/report.json`,
`report.compact.json`, full native client logs,
source SHA and binary SHA, before/after server jemalloc stats,
runner CPU and memory, fixture manifests and physical layer counts.
The artifact name contains the previous experiment's
`stagger-codec-concurrency` prefix because it was copied from
that workflow; **identify it by the run ID and hash**.
This name has no impact on the test or data.

## Exact 1/4/8-client full native proof

CPU model in this runner: **AMD EPYC 9V74 80-Core Processor**,
four logical CPUs; host RAM reported16.76GB.
ClickHouse server `max_server_memory_usage=4,800,000,000`
bytes. **This setting is not a cgroup hard limit**.
Original R01 aligned fixture SHA
`ea9a8d43c9e58ed916f1bf43949556d8ed18169ddd14033c293af4cd54c9881d`;
R10 deterministic stagger SHA
`ae85496efcab3417634ac812cbd50a40c4eea297a397b50792efd024838e1b9a`.
Both verified in CI. 28,896 metric-label identities,
2,488,320 input points per fixture and
2,464,992 intentionally shifted timestamps, value+tag digest
`2c185986f50c585b2262c40c53bec57c6e106d090c9177dc42500dce3cf7caba`.

Four v11 TimeSeries targets: aligned/staggered starts ×
`samples.value` ALP+ZSTD3/ZSTD1, all inner IDs RAW UUID,
one-hour SAMPLES buckets, recent table disabled, one stable
part for each inner layer. Exact compressed bytes and
SAMPLES rows/marks match original stock R18:
- aligned: ALP 5,303,391 B vs ZSTD1 3,414,486 B,
  86,688 rows,340 marks;
- staggered: ALP 5,952,971 B vs ZSTD1 4,126,572 B,
  113,760 rows,446 marks.

Three complete `prometheusQueryRange` selectors
(`pod_namespace`, `burst15`, `node_broad`) ×1/4/8
synchronized clients × outer aligned/stagger/stagger/aligned
× inner ALP/ZSTD1/ZSTD1/ALP ×2 warmup+6 measured waves.
**4,992 native HTTP requests**, 3,744 measured and1,248 warmup,
144 completed phases, 36 valid codec ABBA panels, nine valid
repeated alignment interactions. Every JSON full-output
labels, evaluation timestamps, Float64 values matches the
exact nonempty native oracle for that alignment.
All p50/CPU/QPS control drift gates **PASS**, no OOM,
HTTP error, missing QueryFinish or physical part mutation.

The peak observed process RSS **after a phase** was
**1,105,264,640 bytes** (1.105GB decimal).
The process lifetime **VmHWM** across phases was
**1,166,323,712 bytes** (1.166GB decimal).
VmHWM includes more than ClickHouse's per-query tracked
memory and is not a measurement of peak *simultaneous*
`jemalloc_stats` allocated bytes. These results establish
capacity for **this small 3-day synthetic fixture**, not 14-day
1.33-million-series Jenkins data or production high-cardinality
8-client OOM safety.

## Representative CPU/latency within patched jemalloc run

Four source-phase medians averaged over two repeated outer panels
for default ALP+ZSTD3 at 8 clients:

| Selector | Aligned p50 / QueryFinish CPU | Staggered p50 / QueryFinish CPU |
|---|---:|---:|
| `node_broad` | 494–550ms / 240–280ms (see artifact) | **823.607ms / 400.075ms** |
| `pod_namespace` | 180–210ms / 80–110ms (see artifact) | **222.638ms / 105.415ms** |
| `burst15` | 118–150ms / 59–75ms (see artifact) | **151.569ms / 73.318ms** |

These descriptive single-binary numbers are not an allocator
speedup claim. Even the prior stock R18 runner reported
the same model name, but it was a **different GitHub runner
and a different compiled patch set**, without one-run
alternating A/B. Never use a percentage of those two
absolute latency values as proof of jemalloc overhead or
benefit.

## Decision / next source-backed gate

**PASS** patched-O1+jemalloc runtime and native query correctness/
capacity for this specific 3d/28.9k-series fixture. This is
a useful portability and allocator-runtime gate.
**NOT PROVED**: pure jemalloc versus glibc CPU cost or
RSS advantage, optimizer-neutral production performance,
14d/1.33m-series memory safety, naturally merging
concurrent writes or any global codec recommendation.

To isolate the allocator, first check for an existing
*exactly patch-identical glibc binary* with the same
LLVM22/O1, libraries and all C++ patches.
If missing, producing one would be a new >3h build;
only justify that after source-cost math and reusing cache.
Do not cancel any running build or modify production.
For R18, mixed metric value entropy and HPA starts,
ingest/merge write amplification (coordinate with R02)
and 14-day high-cardinality memory remain unproven.
