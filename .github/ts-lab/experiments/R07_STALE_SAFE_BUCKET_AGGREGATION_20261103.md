# R07 source-pinned no-index per-bucket sum/count: strict stale semantics and CPU path

Canonical owner: [R07](../claims/roots/R07.json). Source \`52529f0c711566d696917678bf15e7068772fdde\`, stock unmodified **O1** SHA256 \`f0a2fa1f814c58853f43423705cac10ab9df3bfe5887e27ebe9620ba7cb9660e\`. **No C++ change, sidecar, new index, production source, schedule or third-party telemetry.** All data synthetic.

## Mathematical hypothesis and invariants

A TimeSeries v11 SAMPLES physical row contains a whole time bucket's \`Array(Tuple(timestamp, Float64))\`. For a group of \`N\` eligible full-original series IDs and evaluation \`e\` with lookback \`w\`, define **per series**, not per metric/group:

- \`F_i,b = [x in SAMPLES bucket b for full ID i : e-w < x.t <= e AND reinterpretAsUInt64(x.value) != 0x7ff0000000000002]\`.
- \`S_i,b = Σ_{x∈F_i,b} x.value\`; \`C_i,b = |F_i,b|\`.
- \`M_i = (Σ_b S_i,b)/(Σ_b C_i,b)\` when \`Σ_b C_i,b > 0\`. No candidate ID when count is zero.
- \`group_avg = (Σ_i M_i)/(count eligible series IDs i with M_i)\`.

Associativity of real-number sum/count proves equivalence of bucket-level and flattened per-series averages **in exact arithmetic**, given SAMPLES is the authoritative, fully deduplicated physical source. Floating-point order, ordinary NaNs, compensated native sums and arrival/merge-order can break naïve approximate equivalence; numerical tolerance and a separate source-pinned NaN oracle are mandatory. The **stale-NaN predicate is exact by bits**, not \`isNaN()\`: ordinary NaNs must be retained. This condition is required by [pinned source, fromSelector.cpp lines19–84](https://github.com/moleus/ClickHouse/blob/52529f0c711566d696917678bf15e7068772fdde/src/Storages/TimeSeries/PrometheusQueryToSQL/fromSelector.cpp).

**Key SE cost model:** original safe SQL expands \`P\` samples via \`arrayJoin\` and updates the full-ID per-series aggregation for each (potentially \`O(P)\` hash-table operations). New SQL does \`arrayFilter\` + \`arraySum\` + \`length\` **per original stored bucket** and updates the group/ID aggregation at most \`B\` times. Both must inspect samples \`O(P)\`; only per-ID hash aggregation potentially shrinks \`O(P)→O(B)\`. In staggered frozen R01, \`P=2,488,320\` and \`B=113,760\`, so \`P/B=21.8734\`. **This is not a predicted 22× whole-query speedup.** IO/decompression, tag selection, time-grid transforms, allocation and expression overhead remain. Further, \`arrayFilter\`/arrayMap may allocate extra arrays and slow short/small buckets.

## Preflight correctness is independently verified

1. [Native-vs-raw-SQL stale-NaN counterexample, CI #41635230738](https://github.com/moleus/ClickHouse/actions/runs/41635230738): native5 vs raw avg NaN for one real5 plus exact stale marker; native duplicate timestamp max7 with later3 yields avg5.
2. [Exact safe flattened SQL single 5-tick test, CI #41641109486](https://github.com/moleus/ClickHouse/actions/runs/41641109486) **PASS**, [immutable evidence](../evidence/R07-stale-safe-five-tick-41641109486.json): 22 native label/tick/NaN class/finite value results exactly matched; original unfiltered SQL mismatched **six** ticks. NaN payload \`0x7ff8000000000001\` is retained; stale-only series absent; same-ID duplicates across INSERT batches tested.
3. [Exact safe bucket SUM/COUNT single 5-tick test, CI #41642126884](https://github.com/moleus/ClickHouse/actions/runs/41642126884) **PASS**, [immutable evidence](../evidence/R07-bucket-sumcount-native-grid-41642126884.json): 22/22 native outputs match new per-bucket reduction and original corrected per-point SQL. This is *not* a speed measurement.

## Same-binary 2.49m staggered R01 performance and falsifiers

Frozen synthetic R01 generator SHA \`ae85496efcab3417634ac812cbd50a40c4eea297a397b50792efd024838e1b9a\`, 400 node slots, 3d ×3h lifetimes, 28,896 full-stable ID series, sampling 15s/60s/300s; 2,488,320 point values, 113,760 stored SAMPLES bucket rows, one merged part per target, **same original TAGS/TimeRanges/SAMPLES read by all plans**. No extra storage. Native output parity across **18 separate evaluation-grid checks** before benchmark and on each measured request.

A. [Exact bit-filter cost vs old incorrect SQL and native, #41641536895](https://github.com/moleus/ClickHouse/actions/runs/41641536895) **PASS**, [evidence](../evidence/R07-exact-stale-filter-cost-41641536895.json). On this runner the filter increased direct SQL wall p50 for pod1d **100.05→104.43ms (+4.38%)**, pod3d **126.24→166.32ms (+31.75%)** and SQL CPU **114.75→132.77ms (+15.7%)** pod1d, **160.09→201.69ms (+26.0%)** pod3d. Full-native vs corrected SQL broad pod1d **135.29→104.43ms (1.30×)**, pod3d **211.32→166.32ms (1.27×)**. Node1d corrected SQL **145.08ms** versus native **136.24ms** (regression). **Unfiltered SQL is invalid with stale-NaNs; do not compare across runners as if same hardware.**

B. [Original corrected point-SQL vs corrected fused per-bucket SQL vs native, #41642508119](https://github.com/moleus/ClickHouse/actions/runs/41642508119) **PASS**, [immutable full compact evidence](../evidence/R07-bucket-sumcount-R01-ABBA-41642508119.json). Four cases each \`native / point / bucket / bucket / point / native\`, two warmups plus four measured per phase, **96 measured**, full output parity each time, A/A drifts all ≤25%, unchanged physical marks/rows between SQL variants.

| Case | Native p50 ms | Correct point-SQL p50 | Correct bucket-SQL p50 | Native/bucket | Point/bucket CPU |
|---|---:|---:|---:|---:|---:|
| Broad pod1d | 59.458 | 32.180 | **28.624** | **2.077×** | 38.351→27.980ms (**1.371×**) |
| Broad pod3d | 76.797 | 34.071 | **32.493** | **2.363×** | 44.835→31.906ms (**1.405×**) |
| Broad node3d | 110.902 | 76.879 | **58.357** | **1.900×** | 105.134→58.413ms (**1.800×**) |
| burst15 1d | 22.984 | **17.986** | 18.258 | 1.259× | 18.215→17.605ms (CPU marginally lower) |

**Scientific significance:** Broad node3d point→bucket wall **1.317×** with low control drifts (~2.2%,6.3%) is the strongest same-plan evidence. Broad pod3d point→bucket wall **1.049×** is modest, though QueryFinish CPU reduction1.405×. Broad pod1d point A/A drift **21.88%**, near the predeclared 25% validity gate; **do not promote the 1.124× point→bucket wall ratio as a stable improvement** without another controlled replication. burst15 bucket wall was **~1.5% slower** than point SQL. All SQL variants show **identical selected SAMPLES marks and read_rows/read_bytes** in each case. Thus improvements are execution/decode/aggregation differences, NOT pruning. Query tracker peak reductions are not process RSS evidence. Sample-by-sample filter is still \`O(P)\`.

**Decision:** retain a **guarded no-index source/query-path candidate** for broad finite/stale/ordinary-NaN gauge windows, with extra eligibility/correctness checks; no universal SQL rewrite or native C++ modification. In particular, earlier source-gauge R01 showed node1d nearly neutral; one-point/ID 70k broad1d/3d SQL regression remains a valid negative. A bucket-aggregation policy likely depends on \`P/B\` (points per stored bucket), actual matched ID count, label selectivity and number of time-grid evaluations, but no exact threshold has been derived under production load.

## Outstanding gates

- Independent **held-out aligned R01** same values and 2.49m points but 86,688 stored sample bucket rows, before claiming stable bucket benefit; see [held-out workflow](../../workflows/ts-lab-r07-r01-bucket-fused-aligned-20261103.yml).
- Full range function parity for multiple grid evaluation steps at R01 scale (tiny 5-tick was checked), stale-only/ordinary NaN under all merges/late writes, cross-INSERT duplicate arbitration, regex project scopes and arbitrary group labels. Unknown selectors must use native path.
- Native code path integration would require preserving \`timeSeriesStoreTags\`, TimeRanges eligibility, no data races or incorrect result cache, and strict source revision/ACL conditions. **No C++ compilation until semantics/CPU/memory and workload threshold hold**.
- Independent O2/jemalloc 1/4/8 concurrent-client throughput/p95/p99 and memory with fair comparison to native, not just single-client O1 synthetic latency. Source code already implements native vectorized \`avgForEach\`; do not claim native query lacks aggregation support.

## Independent aligned held-out: #41643057088 SUCCESS

[GitHub Actions](https://github.com/moleus/ClickHouse/actions/runs/41643057088), [immutable machine evidence](../evidence/R07-bucket-sumcount-R01-aligned-41643057088.json), original ZIP digest \`sha256:00ae09edb1138f732957eafba483f2ebcd63e485319af856611a9a0212677331\`. Same 28,896 distinct full-ID synthetic series, 2,488,320 samples at 15s/60s/300s, same values and tags, but **node start phase aligned**: frozen source fixture SHA \`ea9a8d43c9e58ed916f1bf43949556d8ed18169ddd14033c293af4cd54c9881d\`, **86,688 physical SAMPLES bucket rows** versus 113,760 staggered, so \`P/B=28.70\`. Source and binary identical; different GitHub-hosted runner.

All **18 native grid output parity checks**, 4 cases of 6-phase A/B/B/A with 2 warmup+4 measured per phase, full response parity per query, frozen parts, **all control drifts under25%**. No sidecar, C++ compilation or ACL change.

| Query | Aligned native p50 | Aligned safe point p50 | Aligned safe bucket p50 | Point/bucket wall ratio | Point/bucket CPU ratio |
|---|---:|---:|---:|---:|---:|
| broad pod1d | 71.976ms | **36.739ms** | 37.814ms | 0.972x (**bucket 2.9% slower**) | 44.747→37.148ms (1.205x less CPU) |
| broad pod3d | 94.929ms | 47.183ms | **42.498ms** | **1.110x** | 63.265→41.928ms (**1.509x**) |
| broad node3d | 94.266ms | 83.112ms | **63.476ms** | **1.309x** | 108.220→63.401ms (**1.707x**) |
| burst15 1d | 29.059ms | 24.096ms | 22.784ms | 1.058x (effect within ~10.2% bucket baseline drift) | 24.722→21.985ms (1.124x) |

**Most robust new claim:** broad node3d *correct per-bucket vs correct per-point SQL on exactly the same stock data and runner* p50 **1.317x (staggered) and 1.309x (aligned)**. QueryFinish CPU **1.80x and1.71x lower**, while selected marks/rows/bytes are unchanged in each pair. This is a credible execution-path improvement independent of physical source bucket placement, not a general 22x gain. Broad pod3d bucket SQL saves CPU ~1.4–1.5x in both layouts but latency improvement is smaller, depending on context switching and the rest of the critical path. Pod1d bucket wall is not universally better, even with less CPU. **No global forced planner selection.**

Next bounded gate is [1/4/8 client matched native-vs-safe-bucket ABBA #41643759040](https://github.com/moleus/ClickHouse/actions/runs/41643759040): queueing/throughput, client latency, QueryFinish CPU and coarse 4Hz RSS under parallel load; no production performance conclusion yet.

## 2026-11-03 capacity gate: 1/4/8 synchronized clients, same stock O1

[GitHub Actions #41643759040](https://github.com/moleus/ClickHouse/actions/runs/41643759040) **SUCCESS**, [immutable evidence](../evidence/R07-bucket-sumcount-capacity-41643759040.json), ZIP artifact digest \`sha256:33581b3e54217dcf4e394c257d55c73d71c07b60a1fc59252b39ce2272d88d9c\`. This **new** same-runner experiment uses the frozen staggered 2.488m R01 fixture, one stock O1 ClickHouse instance, original full-ID TAGS/SAMPLES, no new index/sidecar/C++ build, 2 broad three-day native PromQL selectors, and **exact stale-safe bucket SQL** from the validated source/math gate.

For each of **2 selectors × (1,4,8 clients)**, \`native/bucket/bucket/native\` A/B/B/A, **5 synchronized waves per phase (first warmup, 4 measured)**. Every single request checks the **entire native label/time/value output**, plus **18 separate time-grid correctness checks before stress**. This totals **520 client calls, 104 warmups, 416 measured calls and 120 synchronized waves**. All six A/B/B/A baseline drifts under 25%; no OOM or fatal errors; frozen physical parts.

The reported QPS equals \`n/(latest client completion - earliest client start)\` **for a synchronized wave** and is the median of measured wave rates. It is a *short burst capacity estimate*, **NOT** sustained throughput, production rate limits, long-run arrival load or a stable latency percentile. For each n, the p95 is computed on only 8/32/64 client timings **per variant** across repeated A/B/B/A phases, so it is not a p99/SLA measurement.

| Selector | Clients | Native p50 → bucket p50 ms | Native p95 → bucket p95 ms | Native → bucket wave QPS | Native/bucket p50 |
|---|---:|---:|---:|---:|---:|
| node broad3d | 1 | 143.32→80.34 | 146.46→81.29 | 6.97→12.43 | **1.784x** |
| node broad3d | 4 | 419.51→117.81 | 460.42→130.46 | 9.16→32.77 | **3.561x** |
| node broad3d | 8 | 845.61→273.41 | 964.68→328.62 | 8.39→26.17 | **3.093x** |
| pod broad3d | 1 | 135.76→48.65 | 139.13→53.31 | 7.36→20.54 | **2.790x** |
| pod broad3d | 4 | 296.75→94.09 | 358.25→108.56 | 12.44→37.73 | **3.154x** |
| pod broad3d | 8 | 549.93→135.61 | 615.22→154.42 | 13.13→52.17 | **4.055x** |

At eight clients, pod native QueryFinish CPU **263.47ms** versus fused **69.34ms per query** (CPU ratio **3.80x**), while synchronized burst QPS rises **3.97x**. Node eight-client CPU **378.85→115.66ms** (3.28x), QPS8.39→26.17 (3.12x). This is *consistent with* CPU saturation and reduced per-ID aggregation/grid materialization work, but **does not isolate a specific CPU stage** and cannot prove saturation without CPU utilization/off-CPU profiling. Physical SAMPLES marks are **302 both** for broad pod and **154 both** for broad node. This is not a mark-pruning or storage-codec effect. The full multi-client evidence includes per-query QueryFinish CPU/bytes/query tracked memory and whole-server 4Hz RSS; **allocator retention means do not infer production RSS savings from observed per-phase maxima**.

### Updated decision

**Research acceptance, not an upstream or production deploy:** independently verified 22/22 challenging native grid outputs, 18/18 exact R01 grids in two 2.49m physical layouts, 4-case single-client matched A/B/B/A, and 1/4/8 concurrent-client same-stock source with full output checks. For the narrow tested query family \`avg by(project|namespace)(avg_over_time(metric{literal filter}[1d|3d]))\`, exact stale-NaN bit filtering and original full-ID aggregation are necessary.

Before any C++ patch, a source-level integration plan must preserve (i) native time-series grid behavior, (ii) TimeRanges/tag and duplicate-ID semantics, (iii) stale-NaN vs ordinary NaN and duplicate timestamps across inserts, (iv) read safety during concurrent writes/TTL/backfill, (v) access control and arbitrary matcher fallback. Then a pre-registered admission rule must select the fused path only when \`P/B\`, matched series count, range width and label group cardinality predict a benefit. Broad pod1d showed an **aligned-layout per-bucket wall regression** relative to point SQL even though CPU fell; general force-routing is wrong. Need independent **optimized production allocator O2/jemalloc**, sustained randomized read/write arrivals, independent replicated result and p99 before any default change.

Do not touch R02/R05/R18/R19 question-owner claims. No master, scheduled workflows, production infrastructure, force push, build cancellations or private dataset was modified.
