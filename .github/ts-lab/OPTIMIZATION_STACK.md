# Cumulative ClickHouse TimeSeries optimization stack

**Goal:** obtain a reproducible same-compiler, same-source, same-fixture **stock baseline** and compare it against each proven improvement and the combined variant. Do not claim gains from stacking incompatible or unverified changes.

## Control identities

| Control | Source | Binary, optimization and experimental status |
|---|---|---|
| `STOCK` | ClickHouse source `52529f0c711566d696917678bf15e7068772fdde` | Must build with exactly same LLVM, CMake definitions, optimization level and linked libraries as candidate; stock v11 TimeSeries defaults; no extra C++ lab patches |
| `LAB_INSTRUMENTED` | Same pinned source with native JOIN-override lab patch | O1 run [41531656434](https://github.com/moleus/ClickHouse/actions/runs/41531656434), default behavior preserved; enables controlled internal JOIN tests |
| `RAW_ID` | Same pinned stock or lab binary, changed v11 **table schema only** | For high-churn tests use `Tuple(UInt64,UUID)` on TAGS, TimeRanges and SAMPLES; preserve the original hash expression and text index; proven for that fixture |
| `CBO_STATS` | Same source and type; per-table stats / query setting | CBO statistics can reorder SQL JOIN; do not assume benefit to all native PromQL queries |
| `CXX_DECODER_LAB` | Same source plus lab-only `SerializationLowCardinality` dictionary-adoption experiment | **REJECTED** after exact matched O1 C++ ABBA [41620022189](https://github.com/moleus/ClickHouse/actions/runs/41620022189): stage p50 +11.6% and CPU +10.7%, while unsafe on malformed index streams. Never combine this patch into `FINAL_STACK`. |
| `CXX_NDV_LAB` | UniqV2 LC dictionary referenced-keys patch | Only correctness data from [41589359472](https://github.com/moleus/ClickHouse/actions/runs/41589359472); independent performance and memory gates required |
| `TIME_PROJECTION_LAB` | TimeRanges projection with correct AggregatingMergeTree settings | Direct SQL/experimental CBO probes exist; verify native results and repeated-update correctness |
| `FINAL_STACK` | `STOCK` + individually validated compatible optimizations | **Not yet built / not yet benchmarked**. Only promote after all gates below pass |

## Promotion status as of 2026-11-03

| Candidate | Stock-matched native performance | Promotion decision |
|---|---|---|
| Explicit `Tuple(UInt64,UUID)` in all three v11 inner targets for high churn | [All four stock O1 CI jobs passed #41596921882](https://github.com/moleus/ClickHouse/actions/runs/41596921882), speedups 1.14–1.43×, identical PromQL values and hashes | **Approved as opt-in new-table DDL only for this workload**: [DDL](RAW_ID_HIGH_CHURN_V11.sql). Do not change global default blindly. Dense/other churn distributions still need tests. |
| Move `TagsKey` CityHash work before collector's exclusive mutex | [Exact lock profile #41596955580](https://github.com/moleus/ClickHouse/actions/runs/41596955580): shared waiting decreases but native project14d wall regresses slightly at 4/8 threads | **Rejected from cumulative stack**. Revisit only with lower memory/copy cost and source-exact causality. |
| Remove TimeRanges metadata join for selective selectors | [Raw-ID TimeRanges ON/OFF factorial #41601352971](https://github.com/moleus/ClickHouse/actions/runs/41601352971): `stage` OFF wins 3–14d by4–7%, `project` OFF wins only14d by4%, but whole3d OFF is57% slower and whole7d OOM | **Rejected as a global schema change**. Potential future cost-based **query-time** choice with strict memory bound; [model](TIMERANGES_ADAPTIVE_COST_MODEL.md). |
| CBO/JOIN alternatives or optimizer statistics | Branch checkpoint and earlier SQL diagnostic tests exist; no matching full native matrix on final raw schema | **Experimental only**; do not promote without parity and read/write/RSS gates. |
| Source-level LC dictionary decoder patch | [Both O1 compiled #41531656434 and #41591617794](https://github.com/moleus/ClickHouse/actions/runs/41620022189) matched by source, flags and actual patch diff. Stage14d p50 916→1023ms, CPU1695→1876ms, baseline drift <0.11%; project/regex/whole effects near drift. [Immutable evidence](evidence/R12-lc-decoder-matched-41620022189.json). | **REJECTED**: verified stage CPU/latency regression and known missing index bounds/duplicate-key guards. This is not a correctness-safe optimization. No further recompile of unguarded patch. |
| Full `FINAL_STACK` | Need direct stock vs combined runs across high churn, dense stable and broad 14d | **Not produced**; do not multiply isolated speedups. |

Note: the **stock** baseline was built without C++ patches, same LLVM22.1.8, same source `52529f0c7`, O1: [#41596421285](https://github.com/moleus/ClickHouse/actions/runs/41596421285). Matching this baseline resolved the earlier O1-versus-O2 confounder.

## Promotion gates

1. **Same binary contract:** verify source SHA, compiler version, O1/O2 options, `BUILD_INFO`, archive + binary SHA, and no unintended C++ changes.
2. **Semantic parity:** exact tags, label order and timestamps, numeric tolerance including NaN/Inf and empty results, duplicated samples, mixed metrics, ACL/readonly context, concurrent queries, repeated updates and merges.
3. **Physical parity or declared layout change:** fixed record count, series identity, timestamp/bucket, parts, marks, compressed bytes, id generator and index definitions; otherwise classify as a schema experiment and report storage/ingest effect.
4. **A/B/B/A:** 2 warmups plus 4+ measured repetitions, valid control drift, fresh process comparisons where allocator retention matters, per-case p50/p95/p99, CPU, memory/RSS and direct IO.
5. **Workload matrix:** 1/3/7/14d; whole/stage/project/branch regex plus real Kubernetes metric/namespace/pod/job selectors; high churn 95k new/day and dense stable 15s or hourly samples; 1/4/8 concurrent clients.
6. **CPU and wait:** full CPU trace taxonomy, operator work/input/output wait, actual `/proc/*/schedstat` CPU/runqueue wait, futex/condition-variable/lock-site evidence and overhead control.
7. **Write path:** steady small inserts, merge cost, physical compression, total CPU and process RSS. Guard against broad-query OOM and 14d cardinality regressions.
8. **Promotion decision:** if a candidate is slower, less safe, or fails gates, log that outcome in [METHODOLOGY_AND_FAILURES.md](METHODOLOGY_AND_FAILURES.md) and leave it out of `FINAL_STACK`. Never interpret a tested fix for statistics as a proven query speedup.

## Implementation order

- Preserve the same-source stock baseline artifact; do not compare a patched O1 binary with an old O2 binary.
- Use the proven `RAW_ID` schema **only when the workload has high distinct IDs per part and low repeat count**. It is a workload-specific candidate, not a global engine default.
- Run `CBO_STATS` + `RAW_ID` as a 2×2 factor experiment to detect synergy or interaction (both query and write paths).
- Measure and fix the largest **remaining** CPU/off-CPU bottleneck in `RAW_ID`+stats rather than continuing to optimize a disappearing `ReverseIndex` hotspot.
- Promote C++ code only after the full parity matrix and full native latency/CPU proof, then compare stock vs `FINAL_STACK` over the same 1.33m and dense fixtures.

## Prior primary results

- [Native raw-ID 4-case A/B/B/A](https://github.com/moleus/ClickHouse/actions/runs/41590051439): stage14d 918→618ms; whole1d 1341→884ms; project14d 1008→922ms; regex14d 2054→1679ms. CPU down 20–31% across cases. **Not stock-vs-all optimization**, just isolated schema variable.
- [Read-CPU by internal table](https://github.com/moleus/ClickHouse/actions/runs/41590864544): TAGS, TimeRanges, SAMPLES decomposed, not additive critical-path time.
- [CPU write/merge A/B/B/A](https://github.com/moleus/ClickHouse/actions/runs/41591016459): merge CPU declines on raw UUID; does not cover constant-interval ingest.
- [Dense/sparse counterexample](https://github.com/moleus/ClickHouse/actions/runs/41590057398): raw UUID improved dense full query ~7%, but sparse-wide ~6% worse.

## R03: external VictoriaMetrics comparison, not a cumulative ClickHouse optimization

[Validated 12-case full-native 64-wave A/B/B/A #41624246411](https://github.com/moleus/ClickHouse/actions/runs/41624246411),
[exact evidence](evidence/R03-ch-vm-k8s-concurrent-41624246411.json), one same 400-node / 3d / 2.49m-point fixture and full output equivalence. VM v1.134.0 optimized release had **3.07–41.12× lower p50** than stock ClickHouse **LLVM22 -O1/glibc** on the 12 tested selector × concurrency combinations. This is a valid configuration-specific diagnostic, **not** a performance claim about production-equivalent ClickHouse versus VictoriaMetrics. Do not mix this reference with gains in `RAW_ID`, `FINAL_STACK` or C++ patch speedups.

A production-like engine choice needs matched optimized build/allocator/hardware memory limits, 14d cardinality and query-limit failures, staggered node starts, read/write concurrency and cost. The [jemalloc O1 build #41607779848](https://github.com/moleus/ClickHouse/actions/runs/41607779848) is independently in progress; preserve its cache/run, do not duplicate it.
