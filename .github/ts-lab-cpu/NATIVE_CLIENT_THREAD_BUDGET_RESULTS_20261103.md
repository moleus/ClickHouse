# Stock O1 native RAW UUID: per-query thread budget under client concurrency

**Date:** 2026-11-03. [Full CI #41608382567](https://github.com/moleus/ClickHouse/actions/runs/41608382567), **all three matrix jobs SUCCESS**.
[Benchmark](bench_native_client_thread_budget_20261103.py),
[self-path-only workflow](../workflows/ts-lab-native-client-thread-budget-20261103.yml);
workflow commit `358fbf58a77b96d489ac216aae68e2b65f8a247e`.

## Identity and design

- Immutable source `52529f0c711566d696917678bf15e7068772fdde`,
  **same unmodified O1 executable** SHA256
  `f0a2fa1f814c58853f43423705cac10ab9df3bfe5887e27ebe9620ba7cb9660e`,
  LLVM 22.1.8. No C++ build in this workflow.
- Frozen dataset SHA256
  `8f3a36846f80d6be5a8604d142e934399f2687094766c57bf105e43977f52858`,
  1.33m series, 14 days, native v11 `Tuple(UInt64,UUID)` ID across all three
  internal tables. The LC control fixture was also materialized to preserve
  exact stock ingestion and physical layout; read queries target RAW_UUID.
- Isolated 4-logical-vCPU Ubuntu Actions runner per case; max query memory
  1.8e9 bytes, server limit 4.8e9 bytes (4.47 GiB).
  Same native `prometheusQueryRange` input/output as [full multiclient matrix](NATIVE_MULTICLIENT_STOCK_RESULTS_20261103.md).
- **Fixed simultaneous client count per case**, change only SQL
  `max_threads` in A/B/B/A order **2/1/1/2**.
  Each phase 2 warmup and 8 measured waves with client start barrier:
  8 clients stage14d; 4 clients project14d (8 clients exceeded original RSS limit);
  8 clients whole1d.
- Every successful native query compared for **complete nonempty labels,
  timestamps and values** against the same RAW_UUID reference query.
  Physical part count/rows/marks/bytes are identical before and after.
  Query CPU, runnable scheduler delay, p50/p95/p99, throughput per measured
  wave and RSS measured separately. No all-thread polling overhead.

## Results (source-backed, no projected values)

Times in ms; `rate` is requests/s across measured waves;
`drift` = first vs last `max_threads=2` control p50 deviation.

| Native selector | Clients | Threads=2 p50 | Threads=1 p50 | p50 change | Threads=2 CPU/request | Threads=1 CPU/request | Threads=2 rate | Threads=1 rate | Drift | Verified |
|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---|
| stage14d | 8 | 1521.392 | 1451.735 | -4.58% | 736.668 | 695.521 | 4.9065 | 5.1485 | 4.65% | PASS |
| project14d | 4 | 3958.501 | 3849.201 | -2.76% | 3615.486 | 3522.305 | 0.9024 | 0.9533 | 3.75% | PASS |
| whole1d | 8 | 2701.645 | 2683.703 | -0.66% | 1233.735 | 1224.864 | 2.7211 | 2.7241 | 2.40% | PASS |

Native p95:
- Stage8: max_threads2 **1663.211 ms** versus threads1 **1568.762 ms**.
- Project4: max_threads2 **4709.448 ms** versus threads1 **4377.105 ms**.
- Whole8: max_threads2 **2959.432 ms** versus threads1 **2978.458 ms** (the one-thread candidate is **slower** at p95).

All baseline controls stayed below the 25% rejection gate, and all complete
queries returned equal native PromQL results. However stage/project p50
changes are **similar to the size of baseline A/A drift**; they are not
strong enough to establish a general throughput or latency benefit from
`max_threads=1`. Whole8 is effectively unchanged.

Stage8 baseline phase0 vs phase3 `max_threads=2` alone changed p50
1556.773→1486.011ms; and its `OSCPUWaitMicroseconds` changed
1611→700ms, so it would be an error to attribute all apparent runqueue
wait savings to the `max_threads` intervention.
Client concurrency at 4 vCPUs is a separate variable from max_threads.

## Memory

At stage8, maximum per-query memory is ~96.8 MB for both thread settings,
and whole8 ~136.2 MB for both. At project4 the A/B/B/A phase maxima differ:
thread2 starts at ~369.9 MB and ends at ~260.3 MB while
thread1 is ~322.1 and ~307.2 MB, showing that phase variation is substantial.
Do **not** conclude per-query memory saving from the maximum alone.
Per-variant process RSS maxima are likewise contaminated by allocator
retention in the same server. Project8 `max_threads=2` remains a
[reproduced capacity failure](NATIVE_FRESH8_MEMORY_FAILURE_20261103.md).

To decide whether `max_threads=1` **changes project8 survival**, an independent
fresh-process phase-isolated test is required:
[fresh project8 threads1 #41609140400](https://github.com/moleus/ClickHouse/actions/runs/41609140400).
It was running when this report was written; no result inferred.

## Evidence links

- [CI #41608382567](https://github.com/moleus/ClickHouse/actions/runs/41608382567), stage8 artifact `13218006740`,
  project4 artifact `13218760124`,
  whole8 artifact `13218271956`.
  Each contains full `report.json`, `report.compact.json`,
  query CPU profile samples, processor profiles, exact query IDs, server
  and manifest/binary SHA verification.
- [Full LC-versus-RAW ID concurrency matrix](NATIVE_MULTICLIENT_STOCK_RESULTS_20261103.md),
  source-safety [LC decoder audit](LC_DECODER_FASTPATH_SAFETY_AUDIT_20261103.md).

**Decision at tested loads:** the large gain is the RAW UUID ID type,
not a confidently demonstrated per-query `max_threads=1` tuning.
Do not change production query thread defaults based on this small
phase-drift-scale effect; use per-workload capacity tests and fresh-process
production-allocator parity first.
