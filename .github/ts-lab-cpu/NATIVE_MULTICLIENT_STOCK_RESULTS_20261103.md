# Stock ClickHouse O1 native TimeSeries under concurrent clients

**Date:** 2026-11-03. **Only write branch:** `lab/ts-cbo-adaptive-20261103`.

**Primary CI:** [41605951284](https://github.com/moleus/ClickHouse/actions/runs/41605951284). Workflow commit `e93a43054346877eb66e6c9a80fa3ec495bea569`.
[Benchmark](bench_native_multiclient_stock_20261103.py) and [self-path-triggered workflow](../workflows/ts-lab-native-multiclient-stock-20261103.yml).

## Pinned, verified experiment identity

- Source: upstream stock `52529f0c711566d696917678bf15e7068772fdde`, no lab C++ patches.
- Executable: binary SHA256 `f0a2fa1f814c58853f43423705cac10ab9df3bfe5887e27ebe9620ba7cb9660e`; LLVM 22.1.8, O1. CI verified the artifact ZIP SHA, bundled `SHA256SUMS`, `BUILD_INFO.txt` and executable SHA on **every** job.
- Dataset: SHA256 `8f3a36846f80d6be5a8604d142e934399f2687094766c57bf105e43977f52858`; 1,330,000 distinct Jenkins series, 95,000 new series/day, one sample/series, 14 days. Both tables contain identical complete tags and logical ID digests (e.g. `4890579600240267689`), same ID hash expression, text index and sample values.
- Variable: original stock `Tuple(UInt64,LowCardinality(UUID))` versus explicit `Tuple(UInt64,UUID)` in all three v11 TimeSeries inner targets (TAGS, TimeRanges, SAMPLES). The stock code and executable stay the same.
- Runner: Ubuntu 24.04 GitHub Actions, **4 logical vCPUs**, per-job isolated server/data. Query max_threads=2, max_memory_usage=1.8e9 bytes, server max_server_memory_usage=4.8e9 bytes (4.47 GiB). CPU trace sample period 1ms.
- Full native query: `avg by(stage)(avg_over_time(jenkins_ci_95k_daily{selector}[1d]))` via `prometheusQueryRange`, daily step. Stage selector `{stage="stage-0007"}` 14 days (14 output points); project selector `{project="project-00"}` 14 days (266 points); whole metric no extra selector 1 day (38 points).
- Both tables merged into single immutable inner parts for SAMPLES, TAGS and TimeRanges; `SYSTEM STOP MERGES`. Final physical part count/rows/marks/bytes equals initial for the two complete successful cases; project run stopped on OOM before final check.
- Each concurrency level has independent clients `n=1,4,8`; for each `n`, A/B/B/A means LC/RAW/RAW/LC, each phase 2 warmup waves + **8 measured waves** of `n` simultaneous requests. Barriers align client starts. Complete nonempty native PromQL labels, time grids and numeric values compared for every successful request. Baseline control drift rejected if >25%.
- QueryFinish CPU, runnable scheduling delay, explicit thread-pool lock counters, compressed bytes, selected marks, query memory, p50/p95/p99, wave throughput and process VmRSS 4Hz recorded. RSS observer overhead around 0.08%-0.11% of measured phase time, far below the prior all-TID sampler's 28%-81% overhead. `OSCPUWaitMicroseconds` is **runnable scheduling delay**, not mutex wait. A per-query tag collector is not shared by distinct clients.

## Results: all values from full native PromQL ABBA

Times in ms. `CPU` is mean QueryFinish `OSCPUVirtualTimeMicroseconds` per request, **not wall**.
`rate` is measured-wave throughput in requests/second, not a multi-hour steady-state capacity claim.
`drift` compares first and last LC control phases.

| Case | Clients | LC p50 | Raw p50 | Ratio LC/raw | LC p95 | Raw p95 | LC CPU | Raw CPU | LC rate | Raw rate | Drift | Valid |
|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---|
| stage 14d | 1 | 396.955 | 321.957 | 1.2329 | 426.977 | 339.034 | 754.434 | 602.010 | 2.488 | 3.085 | 0.87% | PASS |
| stage 14d | 4 | 1171.886 | 968.804 | 1.2096 | 1289.043 | 1039.083 | 1109.611 | 919.984 | 3.227 | 3.927 | 6.60% | PASS |
| stage 14d | 8 | 2291.185 | 1714.749 | 1.3362 | 2438.577 | 1848.743 | 1091.705 | 819.817 | 3.294 | 4.356 | 0.66% | PASS |
| project 14d | 1 | 1762.620 | 1470.650 | 1.1985 | 1793.099 | 1492.017 | 3029.422 | 2417.657 | 0.567 | 0.682 | 0.16% | PASS |
| project 14d | 4 | 4622.808 | 3605.247 | 1.2822 | 4909.220 | 3972.570 | 4080.617 | 3217.019 | 0.830 | 1.046 | 7.94% | PASS |
| project 14d | 8 | — | — | — | — | — | — | — | — | — | — | **OOM / not measured** |
| whole 1d | 1 | 647.806 | 476.255 | 1.3602 | 669.350 | 492.657 | 1092.160 | 856.306 | 1.540 | 2.105 | 0.10% | PASS |
| whole 1d | 4 | 1699.160 | 1284.119 | 1.3232 | 1790.108 | 1402.992 | 1538.777 | 1148.283 | 2.260 | 2.916 | 0.10% | PASS |
| whole 1d | 8 | 3396.162 | 2655.913 | 1.2787 | 3676.419 | 2899.880 | 1574.802 | 1204.642 | 2.187 | 2.794 | 1.65% | PASS |

### Validated CPU attribution

On a **single representative native query trace for each variant**, the disjoint `01_lowcard_dictionary` CPU category had the following sampled counts:

| Case/load | LC dictionary samples: original | LC dictionary samples: raw |
|---|---:|---:|
| stage14d 1 client | 152 / 757 total CPU samples | 5 / 587 |
| stage14d 4 clients | 193 / 1183 | 6 / 850 |
| stage14d 8 clients | 201 / 1050 | 11 / 738 |
| project14d 1 client | 650 / 2999 | 12 / 2488 |
| project14d 4 clients | 970 / 3944 | 17 / 3012 |
| whole1d 1 client | 237 / 1135 | 60 / 848 |
| whole1d 4 clients | 308 / 1458 | 57 / 1200 |
| whole1d 8 clients | 356 / 1552 | 57 / 1187 |

These single-query samples are **not medians**. The raw table still uses LowCardinality(String) for tags, so the category is not necessarily zero. The remaining raw CPU is predominantly physical read/decode, hash-set membership checks, tags registration and grid aggregation. Per-processor elapsed_us sums overlap across threads and must **not** be read as query wall time.

### CPU saturation, not proved collector contention

For stage14d at 8 clients on the 4-vCPU runner, the measured-wave CPU demand `rate * mean_CPU/request / 4` is about **90%** of CPU capacity for both layouts (LC 89.9%, raw 89.3%). At 4 clients stage it is ~89.5%-90.3%. Raw has higher rate largely because each query consumes fewer CPU core-seconds.
At 8 clients, runnable queue delay is 1132ms LC versus 841ms raw for stage; 1750ms LC versus 1408ms raw for whole. This indicates scheduling pressure but is **not** proof of a mutex bottleneck.
Global/local thread pool lock wait counters are near zero. The per-query `ContextTimeSeriesTagsCollector` mutex was **not directly timed** in this run; refer to separate exact-lock experiment [41596955580](https://github.com/moleus/ClickHouse/actions/runs/41596955580).

### Memory and the invalid project8 conclusion

The original `project14d` 4-client phases reached **4,482,797,568 B** observed RSS (about 4.17 GiB), close to the configured 4.8e9 B (4.47 GiB) server limit. Entering the 8-client A phase, an actual query failed with
`Code: 241 MEMORY_LIMIT_EXCEEDED ... current RSS: 4.47 GiB, maximum: 4.47 GiB ... While executing AggregatingTransform`.
This is a **real negative capacity result** and makes the overall project job and overall CI run FAILURE, despite passed 1- and 4-client ABBA subsets. Do not publish an 8-client speedup or survival claim.

RSS is affected by long-lived server allocator retention across LC/RAW/RAW/LC and the progression 1→4→8. The matched A/B/B/A query p50/CPU comparison is valid for completed cases, but the observed phase RSS maxima **cannot** isolate a raw-ID memory saving. This is why a fresh-process follow-up matters.

**Follow-up queued:** [fresh-process 8-client project14d experiment](../workflows/ts-lab-native-project-fresh8-20261103.yml), [script](bench_native_project_fresh8_20261103.py), CI [41607617562](https://github.com/moleus/ClickHouse/actions/runs/41607617562). Restarts the same stock O1 server before each LC/RAW/RAW/LC phase with unchanged on-disk parts and the same memory limit. Failure is preserved per variant. Do not assume this removes the OOM until the full run completes.

## Evidence locations

- Primary stock O1 binary build [41596421285](https://github.com/moleus/ClickHouse/actions/runs/41596421285), ZIP artifact ID `13212367936`.
- Native load run [41605951284](https://github.com/moleus/ClickHouse/actions/runs/41605951284): stage complete PASS artifact `13217525841`, project partial A/B/B/A PASS at 1/4 but OOM at 8 artifact `13216373973`, whole complete PASS artifact `13217537064`. Each contains full `report.json`, compact report, CPU/Memory trace samples, processor profiles and server logs where generated.
- Source audit of unrelated staged fast-path C++ decoder: [blocking dictionary-index validation](LC_DECODER_FASTPATH_SAFETY_AUDIT_20261103.md).

No master, older branches or production resources changed; no existing build cancelled.
