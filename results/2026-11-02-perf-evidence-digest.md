# TimeSeries performance campaign: evidence digest (2026-11-02)

Every numeric finding below is tied to a GitHub Actions run and frozen fixture. Profiled times are not bare benchmark latency. No production promotion.

## 1.33m fresh-process A/B/B/A

Run: https://github.com/moleus/ClickHouse/actions/runs/41486787371

- 14d whole: original v4 2868.110ms (median 4 trials), stage-projected 1589.869ms; VM 2491.231ms. Process peak RSS medians 1383696 vs 847034 KiB.
- 14d stage exact: original 511.446ms, projected 483.933ms, VM 77.504ms.
- 14d branch regex: original 634.173ms, projected 531.893ms, VM 342.888ms.
- All 24 ClickHouse variants in this experiment succeeded and matched VM, including 14-point and 532-point output checks. Data: 14x95k unique one-sample series, fixture SHA256 8f3a36846f80d6be5a8604d142e934399f2687094766c57bf105e43977f52858.
- Output ratio (whole) ~1.80x. Four independent trials per candidate are not a confidence interval or production SLO proof.

## Per-query ClickHouse/VM profiler

Successful run: https://github.com/moleus/ClickHouse/actions/runs/41489366871
Artifact: https://github.com/moleus/ClickHouse/actions/runs/41489366871/artifacts/13163542293

- Full original 6235ms instrumented, projected 4461ms, projected exact-stage 1191ms. These are SLOWER than ordinary benchmark because internal CPU/real-time trace profiling is enabled. Do not compare to non-profiled VM without annotation.
- All output timestamps/tags matched VM within numerical tolerance; max absolute error <4e-13 on tested positive finite series.
- Linux /proc process physical read_bytes=0 on the first profiler run 41488314960 for all 3 CH cases, 0 major page faults, ~164MB logical rchar, free disk ~56%, no active merges during first snapshots. Hot page-cache workload: optimize CPU before claiming physical SSD is the critical path.
- Full CH original ExpressionTransform cumulative processor work 7984ms; projected 3636ms. In contrast AdaptiveBucketMergingSource ~2357 vs ~2380ms. Those are overlapping processor times, not wall-clock slices. Trace_log hot leaf includes libc memset, ReverseIndex insertion, group/tag hash operations.
- VM trace full14: root3302ms; fetch-matching-series2529.8ms (inside: TSID search806.4ms, fetch series1495.7ms), rollup772.1ms. Stage14 root83.33ms; TSID search23.7ms, fetch series39.6ms, rollup17.8ms. Nested nodes must not be added to parent.
- VM Go pprof CPU 12s collected, 4 full queries during profile; top flat frames include runtime maps, memmove, fastcache.Get; binary Go Build ID 3b1ba8fda02d8780167302c864927f265acb8920.
- Only data table parts showed no pre/post difference on local reinspection. Older reporter flagged PARTS_LAYOUT_CHANGED because it included logging tables; fixed to exclude system database in subsequent version.
- Correctness and cold disk/VM cache states need independent verification; no general PromQL correctness claim.

## Physical TAGS index test

Run: https://github.com/moleus/ClickHouse/actions/runs/41489795764
Artifact: https://github.com/moleus/ClickHouse/actions/runs/41489795764/artifacts/13162913494

- Source CH v11 TimeSeries, schema: TAGS ReplacingMergeTree PRIMARY KEY metric_name ORDER BY (metric_name,id); text tags_idx on Map token keyValuePairs with GRANULARITY 100000000; SAMPLES AggregatingMergeTree ORDER BY (id,bucket), hour bucket, 256 granularity; TimeRanges AggregatingMergeTree ORDER BY id.
- Direct TAGS count metric 4.3ms due _exact_count_projection (NOT full scan); stage exact 22.6ms for 35k matching series; project exact15.2ms for166250; branch regex103.6ms for190008.
- EXPLAIN confirms stage/project text index is used but skip selection still shows 167/167 granules, 8/8 parts: row-level postings/filters help output without reducing granule count. Stage exact read_rows 1.33m, read_bytes 2.66MB, 22.6ms; branch regex read_rows 1.33m and 103.6ms. TextIndexReaderTotalMicroseconds stage16ms; this is NOT entire selector search and cannot explain native stage 484ms.
- TAGS internal table and TimeRanges/SAMPLES target share same inner UUID for the selected table, but further component isolation should verify tenant-safe semantics. Direct access to inner tables with admin privileges is NOT a production tenant isolation test.
- Background merge was active in this diagnostic. Results are for discovery, not rigorous A/B speed comparison.

## C++ micro proxy (NOT ClickHouse collector)

Run: https://github.com/moleus/ClickHouse/actions/runs/41489511997
Artifact: https://github.com/moleus/ClickHouse/actions/runs/41489511997/artifacts/13163102904

90/90 independent process trials passed checksum equality.

At N=1,330,000, G=38, randomized ID lookups (3-trial medians):
- Full label hash: register1192.99ms, lookup179.22ms, peakRSS222076KiB.
- Stage-only std::unordered_map: register345.14ms, lookup102.56ms, peakRSS60928KiB.
- Stage-only custom flat open address: register154.44ms, lookup58.05ms, peakRSS78592KiB.
Potential tradeoff: faster hash table has ~30% higher RSS than stage-only std::unordered_map in this proxy. This is a hypothesis for native code, not a ClickHouse speedup claim.

## Hardware CPU profiling capability

Initial run 41490148998 successfully confirmed on a *fresh GitHub Ubuntu runner*:
- perf_event_paranoid started at 4.
- sudo perf stat succeeded.
- sudo sysctl perf_event_paranoid=1 succeeded.
- perf stat without sudo then succeeded.
- sudo perf record -g --call-graph dwarf succeeded.
- perf report succeeded.
- The run's artifact upload failed only because sudo-owned perf.data was unreadable to the uploading action. Follow-up run 41490537656 succeeded after chown.

Actual ClickHouse PMU and DWARF call stack workflow 41490797988 was started after proof; check its result before making IPC/cache-miss claims. Distinguish hardware from process IO, and do not compare instrumented latency against the bare baseline.

## Next steps

1. Check actual CH perf stat and DWARF profiles; rank exclusive CPU samples, memory-source symbols and cache misses.
2. Isolate native TimeRanges merge join, ID-set building, SAMPLES seek/scan and tag lookup for an exact stage. Direct TAGS text search is only ~23ms, so optimizing that in isolation cannot explain a native 484ms exact-stage query.
3. Run native C++ collector microbench on actual shared code path, using the proxy's flat hash only as one candidate. Pre-prove ID collision and ACL semantics.
4. Run optimized O2+jemalloc/no-jemalloc once existing 41476488794 and 41467151457 builds finish; do not cancel.
5. Preserve raw artifacts and incrementally publish compact JSON/Markdown evidence. No new scheduled workflows.

## Source files in fork lab branch

- .github/ts-lab-perf/PROGRAM.md
- .github/ts-lab-perf/capture.py and analyze.py
- .github/ts-lab-perf/micro_collector_proxy.cpp and bench_micro_collector.py
- .github/ts-lab-perf/run_vm_profiles.py
- .github/ts-lab-perf/physical_index_suite.py
- .github/ts-lab-perf/run_hardware_query.py
