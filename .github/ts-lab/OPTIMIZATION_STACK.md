# Cumulative ClickHouse TimeSeries optimization stack

**Goal:** obtain a reproducible same-compiler, same-source, same-fixture **stock baseline** and compare it against each proven improvement and the combined variant. Do not claim gains from stacking incompatible or unverified changes.

## Control identities

| Control | Source | Binary, optimization and experimental status |
|---|---|---|
| `STOCK` | ClickHouse source `52529f0c711566d696917678bf15e7068772fdde` | Must build with exactly same LLVM, CMake definitions, optimization level and linked libraries as candidate; stock v11 TimeSeries defaults; no extra C++ lab patches |
| `LAB_INSTRUMENTED` | Same pinned source with native JOIN-override lab patch | O1 run [41531656434](https://github.com/moleus/ClickHouse/actions/runs/41531656434), default behavior preserved; enables controlled internal JOIN tests |
| `RAW_ID` | Same pinned stock or lab binary, changed v11 **table schema only** | For high-churn tests use `Tuple(UInt64,UUID)` on TAGS, TimeRanges and SAMPLES; preserve the original hash expression and text index; proven for that fixture |
| `CBO_STATS` | Same source and type; per-table stats / query setting | CBO statistics can reorder SQL JOIN; do not assume benefit to all native PromQL queries |
| `CXX_DECODER_LAB` | Same source plus lab-only `SerializationLowCardinality` experiment | Build [41591617794](https://github.com/moleus/ClickHouse/actions/runs/41591617794) may still be running; **not confirmed correct** |
| `CXX_NDV_LAB` | UniqV2 LC dictionary referenced-keys patch | Only correctness data from [41589359472](https://github.com/moleus/ClickHouse/actions/runs/41589359472); independent performance and memory gates required |
| `TIME_PROJECTION_LAB` | TimeRanges projection with correct AggregatingMergeTree settings | Direct SQL/experimental CBO probes exist; verify native results and repeated-update correctness |
| `FINAL_STACK` | `STOCK` + individually validated compatible optimizations | **Not yet built / not yet benchmarked**. Only promote after all gates below pass |

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
