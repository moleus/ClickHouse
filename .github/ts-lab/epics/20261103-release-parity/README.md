# Coordinated TimeSeries optimization campaign — 2026-11-03
This directory is a research epic registry because **GitHub Issues are disabled** for `moleus/ClickHouse` (API returned 410). Documents define scope and acceptance, but do **not** automatically reserve a canonical RNN experiment root or assign an agent.

## Epics and responsibility
| Epic | Workstream | Primary handoff |
|---|---|---|
| [E01](E01-optimized-build-and-cumulative-stack.md) | Release-like source-matched stock and cumulative builds | E02 artifacts |
| [E02](E02-end-to-end-ch-vm-benchmark.md) | Full CH vs VM ingest/read/retention benchmark | Engine decision |
| [E03](E03-cost-based-native-promql-planner.md) | Native CBO, statistics, adaptive plans | Validated planner to E01 |
| [E04](E04-victoriametrics-index-layout-parity.md) | VM postings/index/cache vs CH layout | Index candidates to E03/E01 |
| [E05](E05-victoriametrics-query-execution-fastpaths.md) | VM rollup/execution CPU fast paths vs CH | Native candidate to E01 |
| [E06](E06-fixtures-oracle-retention-and-reporting.md) | Shared fixtures/oracle/reducer/lifecycle | Correctness and comparable results |

## Execution order and non-duplication
1. Start **E06** and cheap source reviews of **E03/E04/E05** in parallel.
2. Let **E01** freeze optimized baseline and any individually validated candidates; existing builds run undisturbed.
3. Launch E02 matched source/optimized same-host pilot, then complete long-run mixed ingest/read/TTL once E06 is ready. Re-measure after integration; no multiplication of isolated speedups.
4. Move a new experiment to CI only after source proof, a cost upper bound, current root owner/claim coordination and runner budgeting.
5. Default ownership is **unassigned**. The first agent must coordinate with existing named RNN owner and report its epic owner token in a new append-only record. Epic number is **not** a substitute for RNN root ownership.

## Key evidence
- [INDEX](https://github.com/moleus/ClickHouse/blob/lab/ts-cbo-adaptive-20261103/.github/ts-lab/INDEX.md); [rules](https://github.com/moleus/ClickHouse/blob/lab/ts-cbo-adaptive-20261103/.github/ts-lab/AGENT_GUIDE.md); [stack](https://github.com/moleus/ClickHouse/blob/lab/ts-cbo-adaptive-20261103/.github/ts-lab/OPTIMIZATION_STACK.md); [coordination](https://github.com/moleus/ClickHouse/blob/lab/ts-cbo-adaptive-20261103/.github/ts-lab/RESEARCH_COORDINATION_20261103.md).
- [Stock RAW_UUID A/B/B/A](https://github.com/moleus/ClickHouse/actions/runs/41596921882).
- [R03 ClickHouse vs VM diagnostic](https://github.com/moleus/ClickHouse/actions/runs/41624246411).
- [R07 alternate query plan](https://github.com/moleus/ClickHouse/actions/runs/41634426255).
- [R19 old metadata CPU cost](https://github.com/moleus/ClickHouse/actions/runs/41635997049).

## Exit decision
An optimized ClickHouse stock/candidate vs optimized VictoriaMetrics comparison exists with exact fixture + resource parity, correctness, read+write concurrency, full p50/p95/p99/CPU/RSS/storage tradeoff, and an explicit decision that includes unknowns. All results are artifact-linked; no unproven direct inner TAGS deletion.
## Shared execution contract
- Only write to `lab/ts-cbo-adaptive-20261103`. Do not modify `master`, upstream or production, and do not cancel existing jobs. Preserve build caches.
- Before any costly job, read [INDEX](https://github.com/moleus/ClickHouse/blob/lab/ts-cbo-adaptive-20261103/.github/ts-lab/INDEX.md), [AGENT_GUIDE](https://github.com/moleus/ClickHouse/blob/lab/ts-cbo-adaptive-20261103/.github/ts-lab/AGENT_GUIDE.md), [OPTIMIZATION_STACK](https://github.com/moleus/ClickHouse/blob/lab/ts-cbo-adaptive-20261103/.github/ts-lab/OPTIMIZATION_STACK.md), [coordination](https://github.com/moleus/ClickHouse/blob/lab/ts-cbo-adaptive-20261103/.github/ts-lab/RESEARCH_COORDINATION_20261103.md), canonical RNN root claims and current queued/in-progress jobs.
- These are **epic plans**, not RNN experimental root reservations. Existing canonical owners remain responsible for their own claims. Request a disjoint subclaim through the owner or reserve a genuinely new root using the existing create-only rule.
- Only one variable per experiment, except a preregistered factorial. Pin exact source/binary/fixture hashes and full query/DDL. For timing use matched warm-up A/B/B/A, control drift and no observer-overhead mismatch.
- Preserve all results as immutable evidence with `PASS/REJECT/NO_EFFECT/INCONCLUSIVE/INVALID_METHOD/CORRECTNESS_FAIL`; GitHub job SUCCESS alone is not scientific success.
- Protect exact labels/timestamps/numeric values, stale NaN, absent labels, repeated/late samples, counter resets, ACL, memory/OOM. Do not infer production superiority from O1 vs release or multiply separate speedups.
- Source/review work can start in parallel. CI compiles must be budgeted and must not preempt other owners.
