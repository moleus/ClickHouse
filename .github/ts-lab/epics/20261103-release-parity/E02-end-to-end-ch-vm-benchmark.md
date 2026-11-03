# E02 — End-to-end optimized ClickHouse vs VictoriaMetrics
**Owner lane:** engine performance comparison integrator (coordinate with existing R03 owner).
**Depends on:** E01; E06 workload/oracle; existing R01/R02/R03/R19.
**Unblocks:** evidence-based platform decision.

## Goal
Compare optimized stock ClickHouse, cumulative ClickHouse and optimized VictoriaMetrics with identical realistic query/data/resource contracts. Keep diagnostic comparisons distinct from production claims.

## Prior successful evidence
- [R03 12/12 full native VM/CH cases](https://github.com/moleus/ClickHouse/actions/runs/41624246411): VictoriaMetrics faster in a **non-release-equivalent** test (VM release vs CH O1/glibc).
- [Jenkins 1.33m 14d results](https://github.com/moleus/ClickHouse/blob/lab/ts-cbo-adaptive-20261103/results/2026-11-02-perf-evidence-digest.md): separate workload and source binaries; do not combine absolute wall times.
- [R19 retention penalty](https://github.com/moleus/ClickHouse/actions/runs/41635997049): old metadata can add 28–43% latency to the tested case.

## Required workloads
A. **CI/Jenkins:** 95k new series/day, 1.33m distinct IDs/14d, sparse one-point rows, stage/project/branch regex, full Grafana-derived query AST; 1/3/7/14/40/90d if fixture/data allow.
B. **Kubernetes:** ~400 active nodes, ~3h node lifetime, realistic *staggered* startups/HPA, pods/namespaces/project labels, repeated stable series, gauges and counters, 15s/60s/300s data.
C. **Age/retention:** late/backfill, SAMPLES TTL, metadata retention, natural merges, multiple query age ranges, warm/cold cache and restart.

## Benchmark contract
1. Same host/class with equal CPU quota, process RSS/cgroup cap, disk and OS cache policy; declare VM index/tag cache separately from query response cache. Record VM `-search.maxUniqueTimeseries=165000` and every other engine cap.
2. Pinned optimized binaries (E01) and same generator seed/hash; data equivalence independent of implementation. Continuous comparable remote write plus reads, not just final-optimized static data.
3. Read-only and concurrent-write phases, 1/4/8/16 clients as feasible, both open and closed load; throughput saturation curve, p50/p95/p99, CPU seconds/query + per ingested sample, RSS/HWM/retained RSS, bytes/sample, merge cost and error rates.
4. Native PromQL and full values/timestamps/labels oracle: broad/selective, exact/regex/negative, different ranges/steps, counter reset, stale NaN, empty labels, duplicate/late, ingestion-order variation.
5. A/B/B/A same-host warm and cold phases, explicit control drift and instrumentation overhead, source and run IDs. Do not equate QueryFinish insert CPU with server process ingest CPU.
6. Cost report: compute $/million ingested samples, $/query at a stated mix, storage GB-day, memory footprint and capacity **only from actual measured deployment resource use** and price input; never fabricate prices.

## Acceptance
- Reproducible source/fixture/binary/config/run manifest and immutable raw artifacts.
- Three-engine performance and correctness Pareto table, with error/OOM cells preserved.
- At least one mixed workload/soak with concurrent writes and merges, lifecycle churn and bounded memory; explicit uncertainty/production gaps.
- A decision table that shows if ClickHouse meets the workload SLO and cost constraints; do not conclude superiority on one broad selector.
## Shared execution contract
- Only write to `lab/ts-cbo-adaptive-20261103`. Do not modify `master`, upstream or production, and do not cancel existing jobs. Preserve build caches.
- Before any costly job, read [INDEX](https://github.com/moleus/ClickHouse/blob/lab/ts-cbo-adaptive-20261103/.github/ts-lab/INDEX.md), [AGENT_GUIDE](https://github.com/moleus/ClickHouse/blob/lab/ts-cbo-adaptive-20261103/.github/ts-lab/AGENT_GUIDE.md), [OPTIMIZATION_STACK](https://github.com/moleus/ClickHouse/blob/lab/ts-cbo-adaptive-20261103/.github/ts-lab/OPTIMIZATION_STACK.md), [coordination](https://github.com/moleus/ClickHouse/blob/lab/ts-cbo-adaptive-20261103/.github/ts-lab/RESEARCH_COORDINATION_20261103.md), canonical RNN root claims and current queued/in-progress jobs.
- These are **epic plans**, not RNN experimental root reservations. Existing canonical owners remain responsible for their own claims. Request a disjoint subclaim through the owner or reserve a genuinely new root using the existing create-only rule.
- Only one variable per experiment, except a preregistered factorial. Pin exact source/binary/fixture hashes and full query/DDL. For timing use matched warm-up A/B/B/A, control drift and no observer-overhead mismatch.
- Preserve all results as immutable evidence with `PASS/REJECT/NO_EFFECT/INCONCLUSIVE/INVALID_METHOD/CORRECTNESS_FAIL`; GitHub job SUCCESS alone is not scientific success.
- Protect exact labels/timestamps/numeric values, stale NaN, absent labels, repeated/late samples, counter resets, ACL, memory/OOM. Do not infer production superiority from O1 vs release or multiply separate speedups.
- Source/review work can start in parallel. CI compiles must be budgeted and must not preempt other owners.
