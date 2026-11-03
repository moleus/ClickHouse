# Fresh-process project14d, eight clients, query max_threads=1

**Date:** 2026-11-03. [CI #41609140400](https://github.com/moleus/ClickHouse/actions/runs/41609140400) completed **FAILURE** (real HTTP 500 / Code 241 on three phases).
[Source script](bench_native_project_fresh8_thread1_20261103.py),
[isolated workflow](../workflows/ts-lab-native-project-fresh8-thread1-20261103.yml),
workflow commit `66931a67353ab42e768ad1721995d80186815c9a`.
Full artifact `tslab-native-project-fresh8-thread1-41609140400`, artifact ID
`13218426569`. No other workflow was cancelled.

## Identity and sole intervention

Same frozen 1.33m-series fixture SHA256
`8f3a36846f80d6be5a8604d142e934399f2687094766c57bf105e43977f52858`,
same unmodified stock O1 source `52529f0c711566d696917678bf15e7068772fdde`,
same binary SHA256
`f0a2fa1f814c58853f43423705cac10ab9df3bfe5887e27ebe9620ba7cb9660e`.
4-vCPU Ubuntu GitHub Actions, `max_server_memory_usage=4800000000` B
(4.47 GiB), `max_memory_usage=1800000000` B/query.

Unchanged full native `prometheusQueryRange` project14d selector,
19 stages x 14 days = 266 points and strict output parity.
8 clients per simultaneous burst with barrier.
Compared against [earlier freshly restarted `max_threads=2` baseline
#41607617562](NATIVE_FRESH8_MEMORY_FAILURE_20261103.md).
**Only intervention:** change per-query `max_threads` from 2 to 1.
Each LC/RAW/RAW/LC phase restarted ClickHouse against identical immutable
TAGS, SAMPLES, TimeRanges inner parts, verified physical identity, then one
oracle query, up to 2 warmup +8 measured bursts of 8 clients.

## Observed survival outcome: unstable; no complete ABBA

| Phase | Layout | Fresh server PID | Completed bursts | Next burst | Outcome |
|---|---|---:|---:|---|---|
| A0 | LC UUID | 2503 | 10: waves 0–9 | none | **PASS** |
| B1 | RAW UUID | 2781 | 8: waves 0–7 | wave 8 | **OOM** |
| B2 | RAW UUID | 3074 | 1: wave 0 | wave 1 | **OOM** |
| A3 | LC UUID | 3348 | 3: waves 0–2 | wave 3 | **OOM** |

First LC full phase (only complete phase):
- Native PromQL full output verified for all 80 requests, including 64
  measured requests across eight 8-client waves.
- Measured p50 **5102.359 ms**, p95 **5713.395 ms**, total CPU/request
  **2361.657 ms**, OS runnable delay/request **2651.707 ms**,
  measured-wave throughput **1.4257 requests/s**.
- Peak query memory **371,192,194 B** (~354 MiB);
  process VmRSS observed start **604,372,992 B**,
  peak **4,308,443,136 B** (4.01 GiB),
  end **3,804,889,088 B**. One-query CPU and process-RSS
  values are different types of memory accounting.
- RSS observer overhead under 0.1% of phase wall time.

Three other phases received `Code: 241 MEMORY_LIMIT_EXCEEDED`
at around 4.38–4.44 GiB current RSS against the 4.47GiB cap.
The failure text names `AggregatingTransform` as the terminating
operator; this does not prove it allocated the bulk of RSS.

## Causal and performance limits

At `max_threads=2`, four fresh processes with the same workload
failed on their **third** 8-client burst, regardless of LC/RAW layout
[#41607617562](https://github.com/moleus/ClickHouse/actions/runs/41607617562).
Reducing threads to 1 can allow **more bursts to complete in some
fresh processes** (one LC phase passed), but **does not guarantee survival**:
the second LC phase also failed, and both RAW phases failed with very
different burst counts. This is strong evidence of capacity sensitivity to
process memory dynamics and concurrent execution, not a stable advantage
for either physical schema. It is an invalid A/B/B/A speed comparison:
three out of four phases lack measured complete windows.

The benchmark's memory limit is specific to the lab's 4.47GiB cap and
glibc-linked stock O1 binary, **not the optimized production jemalloc build**.
The separate [production allocator build #41607779848](https://github.com/moleus/ClickHouse/actions/runs/41607779848)
must be SHA and `USE_JEMALLOC` verified before using it for any
production memory claim.

This finding is consistent with [fixed client thread-budget A/B/B/A
#41608382567](NATIVE_CLIENT_THREAD_BUDGET_RESULTS_20261103.md):
at 4 clients project14d, `max_threads=1` produces only a
small p50 change of the same scale as control drift. Do **not**
deploy a global `max_threads=1` policy on this basis.

## Further falsifiable work

- Repeat the same 8-client 1-thread capacity test with **explicitly
  verified jemalloc** and enough memory headroom; record both live
  allocator bytes and retained pages, `MemoryTracking` and process RSS,
  rather than guessing from an OOM termination site.
- Establish a deterministic memory envelope across different client
  counts and query threads; require full nonempty PromQL correctness,
  repeated phases and valid controls before comparing performance.
- An actual production resource budget must account for concurrent
  queries, cache, background workers and allocator retention.
