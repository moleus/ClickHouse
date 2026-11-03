# E01 — Release-like ClickHouse builds and cumulative candidate
**Owner lane:** artifact/build integrator (new owner must self-register in a new coordination record).
**Depends on:** existing RAW UUID, R07, R10, R18 evidence; E03/E04/E05 proven candidates.
**Unblocks:** E02 same-resource engine comparison.

## Goal
Create stock and cumulative candidate ClickHouse artifacts with the **same source baseline, LLVM, optimization flags, allocator, cgroup and runtime configuration**, and a versioned candidate manifest. Existing O1 research results are not a production-like engine comparison.

## Existing validated inputs
- [RAW UUID matched unmodified source](https://github.com/moleus/ClickHouse/actions/runs/41596921882): 1.14–1.43x on high churn.
- [R07 query rewrite](https://github.com/moleus/ClickHouse/actions/runs/41634426255): ~2x for selected multi-sample pod query, but not complete native semantics.
- [R10 bucket size](https://github.com/moleus/ClickHouse/actions/runs/41616086196): smaller compression and explicit merge cost at 3h; not universally better for reads.
- [R18 codec factorial](https://github.com/moleus/ClickHouse/actions/runs/41635457586): value-dependent crossover.
- [Jemalloc build](https://github.com/moleus/ClickHouse/actions/runs/41607779848): O1 and contains two C++ patches; never treat as a pure allocator A/B.

## Work packages
1. Freeze `SOURCE_SHA/LLVM/CMAKE/build_type/allocator/patch_series/DDL/settings/binary_SHA/artifact_SHA` for optimized stock and opt-in candidates. Record reproducible commands, runner CPU, cache keys and cgroup.
2. Build source-equivalent optimized ClickHouse stock binary (release-like O2/O3 as validated), then a cumulative binary with **only independently gated compatible C++ changes**. Schema, bucket, codec, statistics and planner-choice settings remain declared independent configuration factors.
3. Use existing verified artifacts/ccache and do not stop existing builds. Compare matched build/allocator controls before attributing effects.
4. Apply sequential same-host A/B/B/A gates, then a preregistered interaction/factorial test. Include exact native PromQL, ingest, merge, read, p95/p99, CPU/query, peak process RSS, bytes/sample, and OOM.
5. Version immutable stock and candidate manifests for E02.

## Acceptance
- SHA-verified stock-optimized and cumulative-candidate binaries; exact source diff; reproducible manifest and cached build.
- Every promoted change has an isolated causal gain, complete semantics and no unbounded memory/write regression. Invalid/unsafe LC decoder and collector prehash patches stay excluded.
- No broad-query OOM at a declared resource cap; E02 can rerun artifacts without recompilation.
## Shared execution contract
- Only write to `lab/ts-cbo-adaptive-20261103`. Do not modify `master`, upstream or production, and do not cancel existing jobs. Preserve build caches.
- Before any costly job, read [INDEX](https://github.com/moleus/ClickHouse/blob/lab/ts-cbo-adaptive-20261103/.github/ts-lab/INDEX.md), [AGENT_GUIDE](https://github.com/moleus/ClickHouse/blob/lab/ts-cbo-adaptive-20261103/.github/ts-lab/AGENT_GUIDE.md), [OPTIMIZATION_STACK](https://github.com/moleus/ClickHouse/blob/lab/ts-cbo-adaptive-20261103/.github/ts-lab/OPTIMIZATION_STACK.md), [coordination](https://github.com/moleus/ClickHouse/blob/lab/ts-cbo-adaptive-20261103/.github/ts-lab/RESEARCH_COORDINATION_20261103.md), canonical RNN root claims and current queued/in-progress jobs.
- These are **epic plans**, not RNN experimental root reservations. Existing canonical owners remain responsible for their own claims. Request a disjoint subclaim through the owner or reserve a genuinely new root using the existing create-only rule.
- Only one variable per experiment, except a preregistered factorial. Pin exact source/binary/fixture hashes and full query/DDL. For timing use matched warm-up A/B/B/A, control drift and no observer-overhead mismatch.
- Preserve all results as immutable evidence with `PASS/REJECT/NO_EFFECT/INCONCLUSIVE/INVALID_METHOD/CORRECTNESS_FAIL`; GitHub job SUCCESS alone is not scientific success.
- Protect exact labels/timestamps/numeric values, stale NaN, absent labels, repeated/late samples, counter resets, ACL, memory/OOM. Do not infer production superiority from O1 vs release or multiply separate speedups.
- Source/review work can start in parallel. CI compiles must be budgeted and must not preempt other owners.
