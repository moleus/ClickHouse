# E06 — Shared fixtures, semantic oracle, lifecycle and benchmark reporting
**Owner lane:** test infrastructure/evidence. Coordinate with R01 (K8s), R02 (writer), R08 (semantics), R19/R21/R22 (retention/cache).
**Depends on:** none. **Unblocks:** E01–E05 common gates.

## Goal
Stop each agent building a different benchmark harness and reporting incompatible numbers. Supply versioned fixture manifests, independent PromQL correctness oracle, common measurement schema and artifact reducer; maintain aging/write/caching cases.

## Work
1. Inventory existing successful R01/R02/R03/R07/R08/R10/R18/R19/R21 fixtures and runners; retain original hashes, snapshots, source and exact query text. Parameterize a shared runner instead of 300 new YAML workflow files.
2. Freeze two families: (A) 1.33m/14d high-churn Jenkins; (B) 400-active-node 3h churn with stagger/HPA, mixed 15s/60s/300s, stable+short-lived time series, finite gauges/counters. Add required retention/late/duplicate/stale variants with deterministic seeds.
3. Independent oracle: exact nonempty labels/timestamps/cardinality, numeric tolerance, stale NaN vs ordinary NaN, absent/empty labels, regex/negative matches, counter resets, last/rate/avg, multiple output steps, boundary semantics, repeated/out-of-order/late samples and null-result handling. Cross-engine query-range parity, not just comparing two potentially wrong engines.
4. Define and validate immutable evidence JSON: fixture source/generator SHA, runner CPU/cgroup, engine binary and CMake flags, DDL, query AST, index caches, client load model, warmup/ABBA phases, achieved/offered QPS, p50/p95/p99, process CPU/RSS/HWM, cache counters, bytes/part/mark/merge, errors/OOM, correctness gate and decision.
5. Add a workflow inventory and deduplication report; reserve canonical roots, reconcile active owners, don't reactivate scheduled workflows. Provide one short human-readable digest generated from immutable evidence with links.
6. Long-running aging/ingest harness must separate steady-state total CPU/write bytes from QueryFinish CPU and support concurrent reads, merges, restarts and TTL without deleting TAGS while writer cache is warm.

## Acceptance
- One reproducible shared runner + reducer with schema validation and existing benchmark adapters; pinned manifest for every E02 testcase.
- Cross-engine semantic regression suite; failing correctness blocks promotion.
- Evidence output has sufficient fields to reproduce and compare same-runner variants and declare mismatches; controls drift/observer impact.
- Public summary that includes successful validated results only in its 'gains' section, with invalid/inconclusive evidence retained elsewhere and not misreported.
## Shared execution contract
- Only write to `lab/ts-cbo-adaptive-20261103`. Do not modify `master`, upstream or production, and do not cancel existing jobs. Preserve build caches.
- Before any costly job, read [INDEX](https://github.com/moleus/ClickHouse/blob/lab/ts-cbo-adaptive-20261103/.github/ts-lab/INDEX.md), [AGENT_GUIDE](https://github.com/moleus/ClickHouse/blob/lab/ts-cbo-adaptive-20261103/.github/ts-lab/AGENT_GUIDE.md), [OPTIMIZATION_STACK](https://github.com/moleus/ClickHouse/blob/lab/ts-cbo-adaptive-20261103/.github/ts-lab/OPTIMIZATION_STACK.md), [coordination](https://github.com/moleus/ClickHouse/blob/lab/ts-cbo-adaptive-20261103/.github/ts-lab/RESEARCH_COORDINATION_20261103.md), canonical RNN root claims and current queued/in-progress jobs.
- These are **epic plans**, not RNN experimental root reservations. Existing canonical owners remain responsible for their own claims. Request a disjoint subclaim through the owner or reserve a genuinely new root using the existing create-only rule.
- Only one variable per experiment, except a preregistered factorial. Pin exact source/binary/fixture hashes and full query/DDL. For timing use matched warm-up A/B/B/A, control drift and no observer-overhead mismatch.
- Preserve all results as immutable evidence with `PASS/REJECT/NO_EFFECT/INCONCLUSIVE/INVALID_METHOD/CORRECTNESS_FAIL`; GitHub job SUCCESS alone is not scientific success.
- Protect exact labels/timestamps/numeric values, stale NaN, absent labels, repeated/late samples, counter resets, ACL, memory/OOM. Do not infer production superiority from O1 vs release or multiply separate speedups.
- Source/review work can start in parallel. CI compiles must be budgeted and must not preempt other owners.
