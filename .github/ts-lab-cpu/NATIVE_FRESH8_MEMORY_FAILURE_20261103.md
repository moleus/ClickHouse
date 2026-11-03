# Fresh-process project14d eight-client memory capacity: negative result

Date: 2026-11-03. **No production or master changes.**

[CI #41607617562](https://github.com/moleus/ClickHouse/actions/runs/41607617562), workflow commit
[`030844620`](https://github.com/moleus/ClickHouse/commit/0308446202e502b93586418c71b0c2a565385ada).
[Runner](../workflows/ts-lab-native-project-fresh8-20261103.yml);
[benchmark script](bench_native_project_fresh8_20261103.py);
original old-process OOM [#41605951284](https://github.com/moleus/ClickHouse/actions/runs/41605951284).
Complete artifact `tslab-native-project-fresh8-41607617562`, artifact ID `13217871315`.

## Controlled experiment

- Same unmodified stock O1 LLVM22 ClickHouse binary from [#41596421285](https://github.com/moleus/ClickHouse/actions/runs/41596421285).
  Source `52529f0c711566d696917678bf15e7068772fdde`;
  executable SHA256 `f0a2fa1f814c58853f43423705cac10ab9df3bfe5887e27ebe9620ba7cb9660e`.
- Frozen 1.33m-series 14-day data, SHA256
  `8f3a36846f80d6be5a8604d142e934399f2687094766c57bf105e43977f52858`.
  Same ID generator and same source samples; all TAGS, SAMPLES and TimeRanges inner parts
  optimized to **one part with 1.33m rows** each. No merges during read.
- Identical full native project selector:
  `avg by(stage)(avg_over_time(jenkins_ci_95k_daily{project="project-00"}[1d]))`
  using `prometheusQueryRange` for 14 daily ticks, 19 stages, 266 output points.
- 8 concurrent independent clients, query `max_threads=2`; query memory limit
  1.8e9 bytes, total server limit **4.8e9 bytes = 4.47 GiB**, Ubuntu GitHub Actions 4 vCPUs.
- Every LC/RAW/RAW/LC phase starts a new ClickHouse server from the same on-disk
  data using a controlled stop/start and verifies part-layout identity. Different
  server PIDs are recorded in the report. An initial oracle query returns exactly
  266 native PromQL points and matches other phases.
- Then 2 warmup bursts of 8 simultaneous queries, followed by up to
  8 measured bursts. A barrier aligns each burst. Every successful request
  has complete labels/timestamps/numeric-parity validation.

## Result: all four phases fail after two completed 8-client bursts

| Phase | Fresh server PID | Layout | Wave 0 | Wave 1 | Wave 2 |
|---|---:|---|---|---|---|
| A0 | 3005 | LC UUID | PASS ~8.99s | PASS ~9.08s | OOM |
| B1 | 3274 | RAW UUID | PASS ~7.43s | PASS ~7.35s | OOM |
| B2 | 3562 | RAW UUID | PASS ~7.39s | PASS ~7.61s | OOM |
| A3 | 3831 | LC UUID | PASS ~8.83s | PASS ~9.05s | OOM |

All failures: HTTP 500, `Code: 241 MEMORY_LIMIT_EXCEEDED`, observed server RSS
around **4.47–4.49 GiB**, server configured maximum **4.47 GiB**, ClickHouse
`OvercommitTracker` picked/failed to free a query, with message
`While executing AggregatingTransform`.

**No measured 8-client p50/p95/CPU/throughput comparison exists.**
Passing two initial bursts is not a valid throughput or long-run survival
measurement. The workflow and all four phases correctly report FAILURE.
The failure site `AggregatingTransform` is not proof that the aggregate
operator is the origin of all resident allocations.

### What this eliminates

The old [#41605951284](https://github.com/moleus/ClickHouse/actions/runs/41605951284)
ran 1, 4, then 8 clients on one long-lived server. OOM at 8 could have come
entirely from memory retained after 1/4 clients. This new test shows that
**the same 8-client OOM appears in all four freshly restarted processes**
even without preceding 1/4-client measured phases. Therefore cross-load
retention is **not necessary** for the failure. Within-phase warmup retention,
active-concurrency allocations, glibc allocator fragmentation and process
RSS tracking remain possibilities; this test does not distinguish them.

Unlike an ordinary heap live-bytes limit, ClickHouse's global server memory
accounting may reflect RSS and other allocations; compare `query_log.memory_usage`
with process `VmRSS` rather than equating them.
This binary uses glibc, while production ClickHouse commonly enables jemalloc:
**glibc-specific results are not production allocator parity**.
The [production-allocator pinned build #41607779848](https://github.com/moleus/ClickHouse/actions/runs/41607779848)
is a separate ongoing experiment; no jemalloc result is implied.

### Next falsifiable controls

1. On this pinned binary and fixture, keep 8 clients but reduce
   per-query `max_threads` from 2 to 1. Require full native parity,
   stable throughput windows, and query-level CPU and total RSS.
   The separate client-thread-budget [#41608382567](https://github.com/moleus/ClickHouse/actions/runs/41608382567)
   tests this knob at load levels that passed the prior memory limit,
   before attempting project8 capacity.
2. Test total RSS and native correctness under controlled fresh-process
   allocator variants (not equivalent to production jemalloc); do not
   recommend `MALLOC_ARENA_MAX=2` globally based solely on RSS:
   [paired glibc results](../ts-lab-cbo/GLIBC_MEMORY_EVIDENCE_20261103.md)
   show a large CPU/time penalty under some loads.
3. With enough physical RAM and the production allocator, profile live
   allocations, allocator retained pages, peak simultaneous query
   `MemoryTracker` values, and whether application cache or reader
   buffers grow between bursts. Add an explicit capacity envelope
   rather than infer from one failed benchmark.

No current or completed CI was cancelled; no prior branch changed.
