# R28 — Query-slot admission: source proof and capacity model

Date: 2026-11-03. Source SHA 52529f0c711566d696917678bf15e7068772fdde. Research branch lab/ts-cbo-adaptive-20261103. **No production performance or safety claim yet.**

[Canonical R28 question](../ts-lab/claims/roots/R28.json), [claim](../ts-lab/claims/R28-native-query-slot-admission-v1.json), [native benchmark](bench_native_admission_capacity.py), [protocol tests](test_native_admission_capacity.py), [workflow](../workflows/ts-lab-r28-native-capacity-20261103.yml).

## Source facts

[ProcessList::insert](https://github.com/moleus/ClickHouse/blob/52529f0c711566d696917678bf15e7068772fdde/src/Interpreters/ProcessList.cpp#L126-L195) acquires the workload QUERY slot before query execution. The separate global limit and per-user limits may reject later; a slot is not a reservation of the query's actual peak memory.

[Integration tests](https://github.com/moleus/ClickHouse/blob/52529f0c711566d696917678bf15e7068772fdde/tests/integration/test_scheduler_query/test.py) establish CREATE RESOURCE query (QUERY), CREATE WORKLOAD all with max_concurrent_queries=N, admission queue sizes, and system.scheduler inflight metrics. [Settings](https://github.com/moleus/ClickHouse/blob/52529f0c711566d696917678bf15e7068772fdde/src/Core/Settings.cpp) give workload_admission_timeout_ms a pre-execution deadline, distinct from max_execution_time.

[Functional CI #41676439539](https://github.com/moleus/ClickHouse/actions/runs/41676439539) passed: all 32 synchronized 200 ms sleep requests returned correct results; slot limit eight produced 210.19 / 209.00 ms wall and limit two produced 812.30 / 810.76 ms. A waiting request returned QUERY_SLOT_ACQUISITION_TIMEOUT after 103.87 ms against a 100 ms deadline. This validates queueing, not native PromQL or memory safety.

[Fresh-server prior OOM #41607617562](https://github.com/moleus/ClickHouse/actions/runs/41607617562): all four 8-client phases failed at ~4.47 GiB, despite fresh servers. [Single-worker follow-up #41609140400](https://github.com/moleus/ClickHouse/actions/runs/41609140400): three of four phases still failed. Neither establishes a general glibc memory leak.

## Hypothesis, model, and limits

Eight clients start concurrently. Each native query has max_threads=2. Slot cap k=8 permits up to sixteen query workers, k=4 up to eight, and k=2 up to four, on a four-core CI host. Background threads add more work.

For identical requests, approximate batch wall time is ceil(8/k)*S(k), where S(k) is actual service time under k admitted queries. S(k) is NOT constant: CPU contention, allocator use and caches may change it. Limiting concurrency can make queue latency worse even if CPU efficiency improves. Count complete responses per full batch window; include admission waiting in client latency.

Memory is not bounded by k times the individual QueryFinish memory tracker. A useful descriptive decomposition is:

RSS_peak(k) = resident_baseline + active_query_pages(k) + retained_allocator_pages(k,t) + caches(k,t) + background(t)

The terms must be measured. A query-slot count does not reserve bytes or guarantee that process RSS stays below an operating-system/cgroup limit. Report sampled VmRSS and lifetime VmHWM independently of query memory. Do not infer global memory safety from a small number of successful waves.

## Native causal test

- Same immutable stock LLVM22 O1 executable SHA256 f0a2fa1f814c58853f43423705cac10ab9df3bfe5887e27ebe9620ba7cb9660e.
- Same original 1.33m-series, 95k/day, 14-day Jenkins fixture SHA256 8f3a36846f80d6be5a8604d142e934399f2687094766c57bf105e43977f52858.
- Same TimeSeries v11 RAW UUID on TAGS, TimeRanges, SAMPLES, same series hashes and physically frozen single parts.
- Full native PromQL avg by(stage)(avg_over_time(jenkins_ci_95k_daily{project="project-00"}[1d])) evaluated at 14 daily timestamps, exactly 266 output points.
- Same native max_threads=2, per-query max_memory_usage=1800000000, total server max_server_memory_usage=4800000000, same request payloads and query logging.
- Six fresh processes in mirrored sequence k=8,2,4,4,2,8. Each phase has two warmup and eight measured waves of eight synchronized clients.
- Every request is checked against the same nonempty native output, including labels, timestamps, and values. Record errors and stop a failed phase, but continue the independent next fresh-process phase. Never compute full-window p95/p99/QPS from partial/error phases.
- Collect process CPU, QueryFinish CPU/read rows/read bytes/marks/memory, phase-local sampled RSS, retained RSS, arrival-to-response p50/p95/p99 (including queue), completed QPS, A/A drift and error classes.
- Scientific ACCEPT_FOR_NEXT_GATE requires both k=2 and k=4 repeated phases complete every wave with equal native outputs, bounded overhead and stable control drift, while the repeated unrestricted k=8 baseline fails. This is only a capacity gate, not superiority to VictoriaMetrics.

## Required production gates

An optimized stock build and production allocator (E01), equal cgroups, open-loop offered-QPS overload curves, extended p99 tests, staggered Kubernetes start times, large and narrow selectors, actual live ingest and merge work, tenant fairness, bounded queue size, and semantic corner cases. R28 does not modify ClickHouse C++ or release binaries.
