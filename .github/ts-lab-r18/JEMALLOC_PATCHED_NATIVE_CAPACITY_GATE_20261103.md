# R18: patched-jemalloc binary native capacity gate

Date: 2026-11-03. **Test design before CI**.
Question remains R18 under original root; no new unrelated claim.

## Artifact and interpretation boundaries

[Independent C++ build #41607779848](https://github.com/moleus/ClickHouse/actions/runs/41607779848)
published verified binary SHA256
`26b1577cea637b98a1d0b3c9efc996cd266234bf6ef89bd1912c75c56098eed3`,
ZIP artifact SHA256
`9e716f524c0802a29d42cc542accc3b388b2766920ae04936687ad101e6e2295`,
archive SHA256
`9c20ccdad5a689765e1945c1af2a9985507c536f7c121229831d987f2a2bd21f`.
Source pin `52529f0c711566d696917678bf15e7068772fdde`,
LLVM22.1.8 `-O1`, `ENABLE_JEMALLOC=ON`. Local executable
runs and `system.jemalloc_bins` returns232 rows and
`system.jemalloc_stats` displays jemalloc 5.3-RC and active
allocated/resident counters. [Immutable provenance record](../ts-lab/evidence/R18-jemalloc-patched-cxx-artifact-readiness-41607779848.json).

**Important confound:** This binary is **not stock-source**.
Its exact 5,560-byte `evidence/combined.patch` changes
`StorageTimeSeriesSelector.cpp` (preserve user-selected JOIN/read
settings) and `Converter.cpp` (narrow `sum(rate(exact_metric))`
fastpath). The R18 native expressions are
`avg by(namespace|project)(avg_over_time(...[1d]))`.
These do not match the narrow `sum(rate())` path and have no
explicit JOIN override; the relevant C++ branches should retain
stock default semantics. But a stock-glibc versus this
patched-jemalloc executable benchmark **cannot isolate allocator
causality**. It remains an end-to-end production-allocator
*operational and capacity gate*, not a matched compiler/source
allocator A/B.

## CI gate

Use exactly the R18 [validated 4992-request source](bench_codec_stagger_concurrency.py)
and workload (3d, 28,896 IDs, 2,488,320 points, original and
staggered node clocks, ALP+ZSTD3 versus ZSTD1, 1/4/8 clients).
Before importing data:
- Download the already-built C++ artifact; verify ZIP-derived
  ARCHIVE_SHA256SUMS and binary SHA256, pinned BUILD_INFO
  and version; do not compile new binary.
- Start isolated localhost server with same 4.8 GB memory cap,
  same query thread and profile settings.
- Query **running server** `system.jemalloc_bins` and demand
  nonzero entries and allocated registers, capture a small stats
  excerpt; fail early if this is not actual jemalloc runtime.
- Preserve original and shifted input hashes, exact complete
  native label/time/numeric parity on every request, four
  frozen physical parts, QueryFinish CPU/wall, 1/4/8 client
  concurrency, process RSS and VmHWM, p95/p99, throughput.
- Capture any fatal OOM, output mismatch, A/A drift >25%,
  all phases, and uploaded logs with `if:always()`.

**Falsifiers:** missing/mismatched C++ binary hash, no jemalloc
runtime bins, missing native output or changed labels/time/values,
OOM before completion, physical part mutation, >25% control
or repeated effect drift. A passing run proves only native
operability/capacity for this patched O1+jemalloc binary, not
isolated jemalloc speedup or production safety.

Workflow is self-path-only push/dispatch, unique run ID
concurrency `cancel-in-progress: false`. No source/production,
master, old lab branch or other experiment changes.

## Expected interpretation after run

Keep R18 stock O1/glibc original native 1/4/8 run
[#41633815009](https://github.com/moleus/ClickHouse/actions/runs/41633815009)
as a **non-matched diagnostic** to compare rough envelopes.
Do not subtract its p50/CPU as a pure allocator effect.
If jemalloc native parity and memory safety pass, the next
scientific allocator comparison requires *the exact same C++
patches and compiler flags with jemalloc disabled* in a matched
non-jemalloc build. Defer that expensive compile until the
other agent's cached builds and claimed owners have been
inspected, never interrupt an active build.
