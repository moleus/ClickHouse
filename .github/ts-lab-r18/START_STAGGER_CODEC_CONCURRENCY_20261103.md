# R18: node-start alignment × value codec × native concurrent clients

Date: 2026-11-03. Unified `lab/ts-cbo-adaptive-20261103` branch.
[Successful CI #41633815009](https://github.com/moleus/ClickHouse/actions/runs/41633815009),
[immutable structured evidence](../ts-lab/evidence/R18-codec-start-stagger-concurrency-41633815009.json),
[exact benchmark source](bench_codec_stagger_concurrency.py),
[self-path isolated workflow](../workflows/ts-lab-r18-stagger-codec-concurrency-20261103.yml).
Artifact ID **13230495191**, `tslab-r18-stagger-codec-concurrency-41633815009`,
ZIP SHA256 `a29dc6cdbf6c3594c2deec7ed436853cb2e10b78ef585868adbc0fd206981dce`.
Full `r18-concurrent/report.json` (7.4MB before ZIP) retains QueryFinish
CPU/latency/marks and physical part audits for **all 4,992 requests**.

## Orthogonal source-controlled layout

The pinned unmodified LLVM22.1.8 **-O1** stock ClickHouse binary is
`f0a2fa1f814c58853f43423705cac10ab9df3bfe5887e27ebe9620ba7cb9660e`,
source SHA `52529f0c711566d696917678bf15e7068772fdde`.
One runner, unchanged TimeSeries v11 schema:
`Tuple(UInt64,UUID)` ID in all three targets,
`samples_bucket_step_seconds=3600`, identical full tag index,
`samples.timestamp CODEC(Delta(8),T64,ZSTD(3))`, no RecentSamples.
Only two experimental factors:
1. **Start time alignment:** original R01 fixture SHA256
   `ea9a8d43c9e58ed916f1bf43949556d8ed18169ddd14033c293af4cd54c9881d`
   versus deterministic offset `(slot*317)%10800` fixture
   SHA256 `ae85496efcab3417634ac812cbd50a40c4eea297a397b50792efd024838e1b9a`.
2. **Value codec:** stock `ALP,ZSTD(3)` versus `ZSTD(1)`.

All four tables have the **same** 28,896 metric-label identities,
2,488,320 original sample values and original per-ID sample counts.
The independent *pairwise full-fixture audit* verified their
combined label/value SHA256
`2c185986f50c585b2262c40c53bec57c6e106d090c9177dc42500dce3cf7caba`.
Exactly **2,464,992 original timestamps shift**. There is no
false cross-alignment numeric-oracle equality claim:
different timestamps can give different native PromQL time grids.

## 1. Actual physical layout and compression

| Fixture | Hourly SAMPLES rows | Hourly SAMPLES marks | ALP+ZSTD3 bytes | ZSTD1 bytes | ZSTD1 saving |
|---|---:|---:|---:|---:|---:|
| Aligned | 86,688 | 340 | 5,303,391 | 3,414,486 | **35.62%** |
| Staggered | **113,760** | **446** | 5,952,971 | 4,126,572 | **30.68%** |

Staggering raised SAMPLES row count **31.23%** on identical values,
while compressed ALP bytes increased **12.25%** and ZSTD1 bytes
**20.85%**. ZSTD1 still stores this repetitive input smaller, but
its advantage narrows by nearly five percentage points.

Physical TAGS, TimeRanges, SAMPLES each have one frozen active part
after OPTIMIZE FINAL. Before/after part rows, marks and bytes match.
No data updates or background merges occurred during queries.
This result is *not* a rolling ingest+natural-merge benchmark.

## 2. Complete native ABBA with real client concurrency

Three full native `prometheusQueryRange`
`avg by(...)(avg_over_time(...[1d]))` selectors:
`pod_namespace`, `burst15`, `node_broad`.
Client counts 1, 4, 8; each wave starts simultaneously through a
Python barrier, with `max_threads=2` inside ClickHouse.

For every selector×client pair: outer
**aligned/stagger/stagger/aligned** panels and inner
**ALP/ZSTD1/ZSTD1/ALP** phases. Each phase has two warmup waves
and six measured waves.

Measured **4,992 full native requests** (3,744 measured plus 1,248
warmups), 144 complete phases, 36 valid codec panels and **nine**
repeated-alignment interactions. All native JSON labels/time/values
match a *nonempty reference within the same alignment*. All control
p50/CPU/QPS drifts ≤25%, and all repeated alignment interactions
pass. No OOM, HTTP error, missing QueryFinish or part mutation.

Shown below is the **average candidate/base ratio from repeated outer
panels**; these are same-runner comparisons, not absolute cross-runner
timings. Ratio below1 is a smaller latency/CPU cost for ZSTD1.

| Selector | Clients | ALP→ZSTD1 p50 ratio aligned | Staggered | ALP→ZSTD1 CPU ratio aligned | Staggered |
|---|---:|---:|---:|---:|---:|
| `pod_namespace` | 1 | 1.0033 | 1.0473 | 0.9994 | 1.0425 |
| `pod_namespace` | 4 | 0.9692 | 1.0515 | 0.9747 | 1.0124 |
| `pod_namespace` | 8 | 0.9880 | 1.0266 | 0.9825 | 0.9918 |
| `burst15` | 1 | 1.0354 | 0.9423 | 1.0386 | 0.9521 |
| `burst15` | 4 | 0.9921 | 1.0053 | 0.9942 | 1.0041 |
| `burst15` | 8 | 0.9845 | 0.9978 | 0.9932 | 1.0030 |
| `node_broad` | 1 | 1.0055 | 0.9960 | 0.9892 | 0.9894 |
| `node_broad` | 4 | 0.9861 | 0.9743 | 0.9878 | 0.9801 |
| `node_broad` | 8 | 0.9691 | 1.0038 | 0.9822 | 0.9955 |

**At eight clients there is no large CPU improvement**:
all codec CPU ratios stay between about 0.982 and 1.003.
Throughput ratios similarly stay close to 1, with some 2–3%
differences. Individual improvements of 1–3% should not be claimed
as robust when repeat ratio drift is of similar or larger size.

For `burst15` at one client, ZSTD1 seems to switch from slower
(aligned) to faster (staggered), but the staggered result has
**8.58% repeated outer-ratio drift**, larger than its ~5.8% benefit.
For `pod_namespace` four clients, aligned p50 seems 3.1% faster
and staggered 5.1% slower; staggered repeat drift **13.0%**.
Do **not** call either a proven optimization reversal under these
controls. The full evidence contains each individual phase median
and CPU/wall/QPS A/A drift.

CPU includes
`OSCPUVirtualTimeMicroseconds` in QueryFinish, separately from
process-global CPU, sampled RSS (only before/after phase) and
per-thread `schedstat` running CPU. Runqueue latency is **not**
mutex waiting. The source and experiment do not isolate native
per-codec decompression cost from cache and array materialization.

## 3. Decision and limits

**Decision: KEEP GLOBAL DEFAULT; physical-storage improvement does
not imply a useful 8-client CPU improvement.** Start alignment affects
the codec storage ratio, and a 3d synthetic aligned workload is not a
reliable production-like series-lifetime model. The shifted fixture
also shifts last sample timestamps past the nominal evaluation
horizon and does not simulate true constant 400-active-node
population; this is a *start alignment probe*, not a production HPA
simulator.

The full-codec comparison still changes ALP transform **and**
ZSTD level simultaneously. See [the separately pre-registered
2×2 codec factorial](VALUE_CODEC_2X2_FACTORIAL_PLAN_20261103.md);
it isolates transform vs compression-level effects with matched
`ALP,ZSTD3`, `ALP,ZSTD1`, `ZSTD3`, `ZSTD1` on identical explicit
SAMPLES schemas. No production or master code change and no C++
compile or cancellation occurred.
