# R03 — source-pinned ClickHouse versus VictoriaMetrics concurrent native PromQL

Date: 2026-11-03. Only working branch `lab/ts-cbo-adaptive-20261103`.
[Question root](../ts-lab/claims/roots/R03.json),
[canonical claim](../ts-lab/claims/R03-k8s-400node-native-vm-concurrency-v1.json).
Source code is the benchmark protocol, not a production deployment.

## Hypothesis and existing evidence

VM was faster on narrow tag filtering in earlier standalone runs, while
a ClickHouse optimized inner path could win some broad or sparse cases.
But those historical workflows used other ClickHouse compilation/patch
variants and had no matched 1/4/8-client native TimeSeries comparison.

The independent question is:
**Does a pinned, unchanged ClickHouse O1 binary with an explicitly opted-in
RAW UUID TimeSeries schema complete the same finite-window PromQL result
as VictoriaMetrics v1.134.0 on the identical 400-node data, and what is
its p50/p95/p99/CPU/throughput/RSS cost under 1/4/8 concurrent clients?**

The experiment does not tune schema, compressions or indexes, and makes no
claim about which engine is "generally faster." The only cross-engine
variable is the whole query engine and storage implementation.

## Reproducible identities

- ClickHouse source SHA
  `52529f0c711566d696917678bf15e7068772fdde` compiled with
  LLVM22.1.8 `-O1`, stock no C++ lab patch, glibc allocator.
  Existing [binary build #41596421285](https://github.com/moleus/ClickHouse/actions/runs/41596421285),
  expected executable SHA256
  `f0a2fa1f814c58853f43423705cac10ab9df3bfe5887e27ebe9620ba7cb9660e`.
- VictoriaMetrics **single-node** v1.134.0 production release, existing
  [archive #41288854734](https://github.com/moleus/ClickHouse/actions/runs/41288854734),
  compressed tarball SHA256
  `cd6e109bae85ff7c138d838b32be9a7fe2a6d7a9b3a387a7cf9234f22991da10`.
  VM uses `-search.disableCache` and `nocache=1`, 165,000
  matching-series cap, 6-month synthetic retention.
- Frozen R01 deterministic source fixture
  `ea9a8d43c9e58ed916f1bf43949556d8ed18169ddd14033c293af4cd54c9881d`,
  400 node slots with 3-hour lifetimes for three days, 28,896
  time series, 2,488,320 original gauge samples (60s/300s/15s).
  Three timestamps per query, one-day range vector, exact label
  matcher or broad selector.
- Identical metric names, timestamps, source sample values and tags.
  ClickHouse uses native `prometheusQueryRange` on v11 TimeSeries
  with `recent_samples_ttl_seconds=0` and explicit RAW UUID ID on
  TAGS, TimeRanges and SAMPLES. The VM importer turns **every original
  sample**, not a downsampled mean, into Prometheus line protocol;
  the source fixture SHA, point count, and last-day VM visibility are
  checked before benchmarking.

## Test conditions

- Four selectors: all node gauges grouped by project; pod gauges in
  one namespace; broad pod gauges grouped by namespace; and 15-second
  burst gauges.
- For 1, 4, 8 synchronous independent HTTP clients, use engine order
  VM/CH/CH/VM. The **corrected 41624246411** run uses 2 warmup waves and **64 measured waves** (the first 8-wave attempt was censored by a 4Hz RSS observer and A/A CPU clock granularity);
  each wave releases clients with one `threading.Barrier`.
- Compare full native results after **each** request: identical sorted
  labels, all expected evaluation timestamps and finite numeric
  values with the historical lab tolerance. Partial, empty, missing,
  timed out, or error responses invalidate that comparison.
- Metrics: HTTP p50/p95/p99 over completed measured requests,
  wave-based throughput QPS, Linux process CPU delta per request
  (including background worker work), high-resolution per-thread
  `/proc/<pid>/task/<tid>/schedstat` CPU delta around measured waves,
  RSS snapshots *outside* timed queries and cumulative (not phase-local)
  VmHWM, ClickHouse QueryFinish CPU/rows/bytes and
  query-memory peak. No false "VM per-query CPU" claim: VM does not
  provide the exact same query-log counter.
- CI must reject A/A p50, schedstat CPU/request or QPS drift >25% **for both engines**, and
  retain all partial logs. Fixed ClickHouse physical parts are compared
  before/after. Report whether VM had background compaction during
  the same phase.

## Limitations that matter for interpreting the outcome

1. **Optimization parity is intentionally absent.** ClickHouse is
   LLVM O1 with glibc and chosen RAW UUID v11 opt-in, while VM is a
   production-release binary. Differences measure *these exact
   executables*, not an intrinsic engine superiority or an optimized
   production deployment.
2. **The memory limit is not an identical cgroup.** CH server
   memory tracking and `-memory.allowedBytes` on VM control different
   quantities. Compare measured per-process RSS and errors, not
   configured caps as if they were equal hard RSS ceilings.
3. **Both services remain on one runner.** Only one is actively
   queried in each phase; however, idle process memory, page cache
   competition, VM background compaction, GitHub runner sharing
   and CPU scheduling can still affect timing. The A/B/B/A control
   detects some but not all interference. Do not call this a
   perfectly isolated capacity test.
4. **The fixture is synchronized synthetic churn.** Node starts
   align to three-hour boundaries; R10 demonstrates this can change
   relative bucket performance. Follow-up on staggered starts is
   essential before any production cost recommendation.
5. **One 3-day synthetic range is insufficient** to prove 14/40-day
   dashboard memory/latency behavior, 1.33m Jenkins cardinality,
   multi-shard/multi-region VM topology, or stale/NaN/ACL semantics.
   VM's cap of 165k does not exclude the 28,896-series fixture;
   it must be accounted for separately on high-cardinality tests.
6. **Query concurrency != query internal parallelism.**
   ClickHouse uses per-query `max_threads=2`, while VM uses its
   native scheduler. This is a realistic end-user comparison for
   the stated config but not an equal-worker algorithm microbenchmark.
7. **Compacted bulk ingest is not continuous ingest+query.** Data
   are loaded and ClickHouse OPTIMIZE FINAL occurs before measured
   read trials. Existing R02 owns continuous-write costs and R10
   owns bucket-step layout. Do not duplicate them inside R03.

## Next promotion gates

The first CI should establish **correctness and diagnostic cost curves**,
not a deployment choice. If all four selectors pass, repeat with
**staggered node starts** and with high-cardinality 14-day Jenkins,
and then match binary optimization level, allocator, CPU/memory cgroups,
cold/warm caching and the VM 165k limit. Prefer one measured change
at a time. If a selector produces different VM/CH values, inspect
range/staleness semantics instead of averaging an invalid speed ratio.

All experimental workflows are self-path triggered and run-unique.
Preserve the running jemalloc [#41607779848](https://github.com/moleus/ClickHouse/actions/runs/41607779848)
without cancellation or duplicate compilation.
