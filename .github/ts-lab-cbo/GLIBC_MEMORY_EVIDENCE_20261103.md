# Pinned O1 native TimeSeries: glibc allocator memory findings (2026-11-03)

## Scope and reproduction

This is **not a benchmark of a production ClickHouse build**. Lab binary: ClickHouse 26.11.1.1, pinned source `52529f0c711566d696917678bf15e7068772fdde`, fork build `f5e42456d7c09c56eb19e96e9ad6844623bc894e`, verified download artifact 13194461800 from run 41531656434. The executable was SHA256 checked after archive extraction.

Build capability on the actual executable:

- `system.build_options` reports `USE_JEMALLOC` empty.
- `system.asynchronous_metrics` contains no `jemalloc.*` fields.
- `SYSTEM JEMALLOC PURGE` fails with `SUPPORT_IS_DISABLED` (not compiled with jemalloc).
- Dynamic ELF symbols resolve `malloc` and `free` to glibc, and `libc.so.6` is present.
- Pinned top-level `CMakeLists.txt` says: `Non default allocator is disabled. This is not recommended for production builds.`

Previously suggested jemalloc metric debugging/purging does **not** apply to this exact O1 binary. This is an important confounder for any comparison against official production ClickHouse or VictoriaMetrics.

Four independent Ubuntu 24.04 GitHub Actions jobs in [run 41604969692](https://github.com/moleus/ClickHouse/actions/runs/41604969692). Fixed canonical TimeSeries fixture of 1,330,000 IDs, 14 days, SHA256 `8f3a36846f80d6be5a8604d142e934399f2687094766c57bf105e43977f52858`; 12 consecutive **full native PromQL** whole-metric 7-day queries, then whole 14-day query (38 stages × 14 = 532 points), same binary and frozen parts per job.

Each job records RSS/HWM, anonymous and file RSS, `/proc/PID/smaps_rollup` PSS/private dirty, server allocator environment, QueryFinish CPU/wall/peak query memory, `system.metrics` and `system.asynchronous_metrics`. OOM of final request is a successful *diagnostic collection* but `report.passed=false`, not a successful query.

## Results (different GitHub runners: speed comparisons unverified)

| Mode | `MALLOC_TRIM_THRESHOLD_` | `MALLOC_ARENA_MAX` | RSS after 12 × 7d, MiB | Whole 14d result | RSS after 14d, MiB | Median 7d wall, ms | Median 7d CPU, ms |
|---|---|---|---:|---|---:|---:|---:|
| default | unset | unset | 3528 | OOM | 4469 (post-failure) | 2881 | 5785 |
| trim | 131072 | unset | 2178 | PASS | 2740 | 5520 | 9993 |
| arena2 | unset | 2 | 1348 | PASS | 1578 | 2983 | 6868 |
| trim+arena2 | 131072 | 2 | 1166 | PASS | 1385 | 5395 | 11419 |

Reduction versus default RSS after twelve 7d requests: arena2 61.8%; trim+arena2 67.0%. The two successful arena2 variants show a *plateau* in RSS after roughly the third or fourth request, whereas default grows as queries repeat. All twelve 7d output shapes pass, and all successful 14d outputs have 532 points. Compare wall/CPU only after the same-runner paired controls.

Baseline default run ending in OOM is independently reproduced in [run 41589590350](https://github.com/moleus/ClickHouse/actions/runs/41589590350). This confirms repeat-query RSS retention is not a one-off runner anomaly.

## Memory interpretation

`RssAnon` and `Private_Dirty` dominate the increase; file cache is comparatively stable. A representative default job's `MarkCacheBytes` and `StatisticsCacheBytes` remain tiny, and `ColumnsCacheSizeLimit` **decreases** as pressure grows (it is a limit, not current cache contents). No evidence yet of an application object leak.

`src/Common/MemoryWorker.cpp` periodically corrects global memory tracking from resident memory if configured; thus `MemoryTracking` rising with RSS does *not* prove that all pages are live ClickHouse objects. The much smaller `MemoryTrackingUncorrected` is a useful control but is not a fully reliable count of live allocations either.

## Current CI and decision policy

- [Same-runner default vs trim ABBA, run 41605644182](https://github.com/moleus/ClickHouse/actions/runs/41605644182).
- [Same-runner default vs arena2 ABBA, run 41605807154](https://github.com/moleus/ClickHouse/actions/runs/41605807154).

Both use the same frozen 1.33m source fixture, four fresh ClickHouse restarts on one runner, phase order A/B/B/A, 12 native 7d requests plus 14d probe per phase, process memory and latency logs. Do not claim CPU speedup or slowdown from the independent-runner medians in the table above; compare ABBA phases and baseline drift.

If paired runs confirm bounded RSS and acceptable latency, `MALLOC_ARENA_MAX=2` is a valid **lab** mitigation for this glibc build. It should not be copied to a normal jemalloc-based production build without capability detection and separate tests. For proper ClickHouse vs VictoriaMetrics comparisons, build with the intended production allocator and match O1/O2 optimization settings and memory limits, or explicitly report the altered allocator as a confounder.

## Source and evidence

- Script: `.github/ts-lab-cbo/probe_glibc_retention.py`, commit `a4212469e78589841a8d5d48b1e9d24a58ef87b6`.
- Four-job workflow: `.github/workflows/ts-lab-cbo-glibc-factorial-20261103.yml`, commit `bac4cb69da039fa94467ad501d44773e2a00503b`.
- Individual artifact IDs: default 13216910388; trim 13216632151; arena2 13215859739; trim+arena2 13217300462.
- Same-runner trim workflow commit `f5b99bb4a5d6146c608b0b7614347f5094b76ef0`; arena2 workflow commit `0d62cd119838bd73f317d190ff5abd57cbb84cc1`.

No production, master, schedule or existing build mutations.
