# TimeSeries performance investigation: broad search and experiment automation

Research fork only, 2026-11-02. Pinned ClickHouse source 52529f0c711566d696917678bf15e7068772fdde. No cron, no production defaults, no upstream writes. See RESEARCH_CHECKPOINT_20261102.md in the adjacent ts-lab-bench directory.

## Measurement layers

| Layer | Mechanism | Useful evidence | Limit |
|---|---|---|---|
| Micro | Standalone optimized C++ or Go benchmark | nanoseconds/op, cache misses, instructions, allocations | Proxy algorithms cannot establish actual ClickHouse behavior |
| Native component | ClickHouse test binary or clickhouse local, actual collector/selector code | metadata/ID sets, allocator, memory locality | Full ClickHouse linking may be needed; clickhouse local excludes serving/auth |
| Full query | Same persisted TimeSeries dataset across matched binaries | query plan, per-operator work and waits, result parity, actual latency/RSS | Processor times overlap; do not sum as wall time |
| Cross-system | ClickHouse and VM single-node, then replicas/shards and concurrency | p50/p95/p99, CPU/query, disk, I/O, network, OOM | Single-node numbers do not prove cluster/HA performance |

The full server is not required for isolated hashing, C++ memory representations, sample-bucket compression or Go indexdb tests. The PromQL planner, dynamic roles, real query latency and disk/network path require full services.

## Instrumentation map

| Subject | Instruments | Caveat |
|---|---|---|
| SQL plan and index | EXPLAIN PLAN indexes=1 and EXPLAIN PIPELINE | Index counters apply to the relevant inner table, not just the outer TimeSeries |
| Query | system.query_log with ProfileEvents and memory_usage; VM trace=1 | Some counters include internal subquery work |
| Pipeline | system.processors_profile_log: elapsed_us, input_wait_elapsed_us and output_wait_elapsed_us | Cumulative overlapping wall time across pipeline workers, not exclusive CPU time |
| Native C++ | system.trace_log CPU/real-time stack profiler, perf record with matching Build ID/debug symbols | CPU sampling statistical; symbolization may be unavailable |
| PMU | perf stat cycles, instructions, cache/branch/LLC misses, faults, migrations | GitHub hosted runners may forbid perf_event; missing is NOT zero |
| Syscalls | strace -c -f or perf trace, optional eBPF when permitted | HIGH overhead: run separately from latency benchmarks |
| Process | /proc/pid/status, /io, /stat, /smaps_rollup, process children and page faults | rchar counts logical reads; read_bytes counts physical reads; sampled RSS can miss peaks |
| Disk/page cache | /proc/diskstats, /proc/vmstat, PSI, free bytes/inodes, system.parts and merges | System counters include other processes; merges change physical parts |
| Network | /proc/net/dev, TCP stats, optional tcpdump/eBPF, cross-node links | Local single-node timing contains no meaningful multi-host RTT |
| VictoriaMetrics | trace=1; /metrics; /debug/pprof/profile and /heap, analyzed by go tool pprof | VM trace child times are nested, not disjoint; heap and CPU profiles have overhead |

Each diagnostic stores fixture and binary SHAs, source commit, CPU model, kernel/governor, disk data path, query ID, SQL/PromQL, version/configuration, raw counters and errors. Lightweight sampling and detailed tracing are explicitly labeled INSTRUMENTED. Collect baseline latency without profilers separately.

## Measured bottlenecks as of 2026-11-02

Fresh-process A/B/B/A run 41486787371 SUCCESS, same 1.33 million one-sample series, 14d, 4 threads, 4 trials each:
- Whole 14d original v4 full label collector 2868.110ms and 1383696KiB peak process RSS; stage-only projected 1589.869ms and 847034KiB; VictoriaMetrics 2491.231ms.
- Exact stage filtered original 511.446ms, projected 483.933ms, VictoriaMetrics 77.504ms.
- Regex filtered original 634.173ms, projected 531.893ms, VictoriaMetrics 342.888ms.
- Full results passed numerical comparisons; 3,990,000 total ClickHouse read_rows regardless of full/stage/regex filter. Need break down by inner TAGS, TIMERANGES and SAMPLES and EXPLAIN selected granules. Aggregate read_rows alone does not prove missing index pushdown.
- Earlier sequential run 41483980771 generated 2.85x timing but suffered allocator/RSS drift; do not use that as general speedup.
- 41453548480 dense 40d counterexample: v4 +28% latency and +91% memory, whereas sparse -39.2% latency. No universal AST-only fastpath.
- 41436423717 rejects general scalar average due Float64 cancellation. 41458721287 shows direct SAMPLES grants can bypass TAGS row policy.

## Main optimization families: plan tests in parallel

| ID | Candidate and experiment | Evidence goal | Priority |
|---|---|---|---|
| P01 | Tag collector stage-only vs full; native class bench with matching ID counts, fresh-process A/B/A | CPU/RSS/allocations and exact selected IDs | Confirmed sparse benefit, semantic limits remain |
| P02 | TAGS/TIMERANGES matching, text/posting index/regex; EXPLAIN inner granules, labels projection | Index pruning, per-target bytes, selectivity | VERY HIGH: filtered CH 484ms vs VM 78ms |
| P03 | ID IN set and TimeRanges merge join; sweep N, parts, labels and time pruning | CPU cache misses, hash and join work | VERY HIGH |
| P04 | ID-to-group data structure: compact hash, maps and grouped vector | Allocations, locality, stable lookup semantics | HIGH |
| P05 | Sparse/dense dispatch using N, E, M, P, estimated activity bound | Crossover and no dense/small regression | HIGH |
| P06 | SAMPLES bucket size, granularity, compression, physical part count | Decode vs disk/pruning overhead | HIGH if I/O confirmed |
| P07 | Page cache, warm/cold, system and user-space cache | Disk wait and major faults | HIGH, environment-specific |
| P08 | Compensated per-ID and grouped Float64 math, SIMD | Error bounds, instructions, NaN/Inf/stale behavior | CONDITIONAL |
| P09 | Allocator/hashes/string copies from flamegraph; jemalloc vs system allocator | CPU and peak RSS source | HIGH only if profile supports |
| P10 | VM label index and rollup path with query trace and Go pprof | Reference speed and memory model | VERY HIGH |
| P11 | Threads 1/2/4/8 and concurrent QPS 1/2/4/8 | Throughput saturation, p95, OOM | After single-query |
| P12 | Result encoding and HTTP overhead vs query computation | CPU/network bytes | If profiler supports |
| P13 | Multi-region read, distributed CH, HA, replication and S3 cold | RTT, network, remote I/O, failover | Later |
| P14 | O1 vs pinned O2 binaries, jemalloc and no jemalloc | Equal-source compiled speed and allocation profiles | HIGH, existing builds pending |
| P15 | Merge interference, duplicate writes and late samples | Ingest cost vs query correctness | HIGH for deployment |

## Mathematical optimization objective

Let N=selected IDs, E=evaluation points, G=output groups, M=stored samples read, P=nonempty (ID,window) pairs, L=labels per series, B=physical buckets or parts; rho=P/(N*E).

A schematic CPU-cost model (not an actual latency equation):
FULL = Cfilter(N,L) + N*Chash(full_labels) + N*Cid_group + Crollup(M,E) + Caggregate(P,G).
PROJECTED = Cfilter(N,L) + N*Chash(stage) + N*Cid_group + G*Cgroup + Crollup(M,E) + Caggregate(P,G).
Delta = N*(Chash(full_labels)-Chash(stage)) - G*Cgroup - extra_overhead.
A positive estimate demands measured unit costs, not just the syntax tree. It is only semantically valid if all original matchers are evaluated first and no subsequent operation requires discarded labels.

Original native grid processing scales approximately with N*E, whereas the sparse per-ID grouping scales with P plus hashing/dedup. For rho near 1/E there is potential; for rho near 1 it can regress. The data-independent optimal dispatch does not exist. A synchronous extra tag-count probe has already exceeded the latency budget for small queries (41437764684). A conservative activity bound collected INSIDE existing selector may be feasible, but is not measured.

For a hotspot with truly measured exclusive fraction f of the critical path, perfect elimination has wall speedup at most 1/(1-f). Fractions obtained from CPU samples instead give an ideal CPU-work limit, not wall time under parallel execution. The total wall time is the length of the critical path through the parallel execution graph, not the sum of operator elapsed times. Optimize multiple objectives: p50/p95 latency, CPU/query, memory peak, disk read and total infrastructure cost, subject to correct PromQL, ACL, no OOM and acceptable tail at target QPS. Keep a Pareto frontier. Estimate daily resource savings as query-rate * CPU-s/query * measured fraction * achievable reduction. Do not chase low-leverage submillisecond operations.

## Experiment loop and stop conditions

1. Before implementation, write a semantics proof, cost inequality, predicted range, falsifying counterexample and acceptance criteria.
2. Execute many INDEPENDENT cheap component tests concurrently on separate runners. A heavy end-to-end job must run alone on its runner to avoid disk/CPU contention.
3. Interleave A/B/B/A and recheck baseline; capture individual observations and uncertainty, identical fixtures/part layouts and optimized compiler flags. Restart process when isolating allocator state.
4. Collect both uninstrumented and instrumented runs. Profiler overhead, system-wide I/O, thermal/frequency scaling and background merges are never silently treated as patch effects.
5. Flag low disk free (<20%), low inodes, CPU steal/PSI saturation, changed parts/active merges, VM not fully searchable, mixed CPU topology, high page faults, missing PMU permission, OOM, output mismatch or ACL failure.
6. Every candidate must pass 14d/40d/90d, dense and sparse, exact and broad regex, duplicate/late/nonfinite samples and permissions before production. Conditional Jenkins allowlist is not general PromQL proof.
7. Save complete artifacts (raw output, querylogs, trace, profiling, environment, stderr) with the immutable source/binary/fixture hashes. The reducer prints compact JSON per case and creates human/machine-readable summary.
8. Stop a candidate when measured upper bound or semantics fail. Continue independent hypotheses. Do not repeatedly build ClickHouse for fixture or SQL changes.
9. Use finite push/manual-triggered campaign with cap on job minutes and runner parallelism, no cron or auto-rescheduling loop. After the batch, the reducer ranks the next experiments and retains failures.
10. Optional results branch receives only compact reports and artifact manifest hashes. Raw evidence remains in CI artifacts or explicitly retained Releases for selected successful runs.

## Instrumentation reference

- ClickHouse query profiler and trace_log: https://clickhouse.com/blog/10-best-practice-tips
- ClickHouse EXPLAIN/threads/concurrency: https://clickhouse.com/resources/engineering/high-concurrency-sizing-user-analytics
- ClickHouse processors_profile_log source test: https://github.com/ClickHouse/ClickHouse/blob/master/tests/queries/0_stateless/02210_processors_profile_log.sql
- VictoriaMetrics Query tracing: https://docs.victoriametrics.com/victoriametrics/#query-tracing
- VictoriaMetrics Go CPU/heap pprof: https://docs.victoriametrics.com/cluster-victoriametrics/#profiling
- VictoriaMetrics disk/page cache and slow queries: https://docs.victoriametrics.com/victoriametrics/troubleshooting/
