# TimeSeries performance lab — index

**Single write branch:** `lab/ts-cbo-adaptive-20261103`. All agents should commit their research here. Keep one append-only history; do not reset, amend, rebase, or force-push.

## Entry points

| Purpose | Resource |
|---|---|
| Shared agent workflow and conflict rules | [AGENT_GUIDE.md](AGENT_GUIDE.md) |
| Known experimental failures and source caveats | [METHODOLOGY_AND_FAILURES.md](METHODOLOGY_AND_FAILURES.md) |
| Exact baseline vs cumulative candidate plan | [OPTIMIZATION_STACK.md](OPTIMIZATION_STACK.md) |
| CBO, JOIN, projection research checkpoints | [CBO checkpoint](../ts-lab-cbo/RESEARCH_CHECKPOINT_20261103.md) |
| Native CPU stack attribution, hash reserve investigations | [CBO CPU attribution](../ts-lab-cbo/NATIVE_CPU_ATTRIBUTION_20261103.md) |
| CPU profiling, physical reads, ID-type experiments | [CPU checkpoint](../ts-lab-cpu/RESEARCH_CHECKPOINT_20261103.md) |
| Physical layout and sparse-index review | [critical-path indexing](../../results/2026-11-02-critical-path-indexing-review.md) |
| Earlier 2026-11-02 campaign evidence | [perf digest](../../results/2026-11-02-perf-evidence-digest.md) |
| Query/proc off-CPU probe | [off-CPU collector](../ts-lab-cpu/probe_offcpu_parallelism.py) |

## Branch consolidation and provenance

The shared branch already descends from `lab/timeseries-fastpath-ci` and `lab/timeseries-layout-research-20261102`. Three merge commits retained **original commit history**:

- [PR #1](https://github.com/moleus/ClickHouse/pull/1): the separate CPU branch, including profiler source, source-pinned A/B/B/A tests and C++ prototype.
- [PR #2](https://github.com/moleus/ClickHouse/pull/2): `lab/timeseries-layout-results` raw data, 4 commits.
- [PR #3](https://github.com/moleus/ClickHouse/pull/3): `lab/timeseries-perf-results` raw data, 7 commits.

Existing older branches remain valid read-only historical pointers and may contain running workflow executions. Do not delete or cancel their jobs. **New writes and experiment workflows must target the single shared branch.**

## Baseline

- ClickHouse pinned source commit: `52529f0c711566d696917678bf15e7068772fdde`; v11 `TimeSeries`.
- Canonical high-churn Jenkins fixture: 1,330,000 series, 95,000/day for 14 days; SHA256 `8f3a36846f80d6be5a8604d142e934399f2687094766c57bf105e43977f52858`.
- Common **O1 JOIN override binary with default settings unchanged**: build [41531656434](https://github.com/moleus/ClickHouse/actions/runs/41531656434). Compare two variants **within the same executable** when possible.
- Stock ClickHouse vs cumulative lab patches requires a **separate same-source, same-compiler, same-fixture** paired control. An O1 patched binary compared directly with an O2 stock binary does not isolate an optimization.
- Verify actual artifact archive SHA and inner binary SHA, not just the GitHub workflow status.

## Replicated findings and open questions

| Finding / candidate | Status | Evidence |
|---|---|---|
| Default `Tuple(UInt64,LowCardinality(UUID))` has high `ReverseIndex` CPU on sparse high-churn IDs | Native PromQL A/B/B/A validated for this fixture | [41590051439](https://github.com/moleus/ClickHouse/actions/runs/41590051439) |
| Explicit `Tuple(UInt64,UUID)` for v11 TAGS/TimeRanges/SAMPLES reduces native latency 1.09–1.52× depending on selector | Verified on 1.33m churn fixture; **not source default / not universal** | [41590051439](https://github.com/moleus/ClickHouse/actions/runs/41590051439) |
| Dense 960 samples/ID and sparse 1 sample/ID differ, raw ID not always faster | Contradictory cases documented | [41590057398](https://github.com/moleus/ClickHouse/actions/runs/41590057398) |
| TAGS, TimeRanges, SAMPLES independently rebuild expensive dictionary state | Direct physical path, not additive wall times | [41590864544](https://github.com/moleus/ClickHouse/actions/runs/41590864544) |
| Native HASH JOIN can cost more than ordered merge JOIN | Native PromQL confirmed on selective stage 14d | [41587498126](https://github.com/moleus/ClickHouse/actions/runs/41587498126) |
| TAGS stage/Map stats can change SQL join order | Diagnostic SQL A/B/B/A, not same as native PromQL gain | [41535099397](https://github.com/moleus/ClickHouse/actions/runs/41535099397) |
| TimeRanges costly for selective selectors but helps broad short-window queries | Workload-dependent; never remove unconditionally | [41510101527](https://github.com/moleus/ClickHouse/actions/runs/41510101527) |
| Local `LowCardinality` dictionary size `INSERT SETTINGS` did not change bytes | Invalid optimization intervention; file SHA before and after merge identical | [41591296942](https://github.com/moleus/ClickHouse/actions/runs/41591296942) |
| Exact source path for hash rebuild & allocator clear | Confirmed with native CPU stacks and pinned C++ | [CBO CPU attribution](../ts-lab-cbo/NATIVE_CPU_ATTRIBUTION_20261103.md) |
| Mutex and futex contention vs scheduling and parked threads | **Next experiment**, no causal conclusion yet | [collector](../ts-lab-cpu/probe_offcpu_parallelism.py) |
| Cumulative compatible improvement stack | **Not validated**; use gated matrix | [stack](OPTIMIZATION_STACK.md) |

## Research priorities beyond UUID

Prioritize allocation and copying in `Map` label materialization, repeated ID set construction, stateful `timeSeriesStoreTags`, HASH build & reserve, `avg_over_time`/grid aggregation and scheduler overhead. For each hotspot: source-level call chain, exact CPU samples, off-CPU/on-CPU attribution, concurrent thread count, memory footprint, experimental intervention, and complete native PromQL parity.

Do not confuse `UUID` uniqueness per **series** with uniqueness per **sample**: a long-lived series repeats its identifier across buckets; dictionary encoding may help some dense workloads.
