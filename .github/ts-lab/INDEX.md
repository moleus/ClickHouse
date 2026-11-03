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

## Verified shared-branch lock and scheduler studies

- [CI concurrency inventory #41596003933](https://github.com/moleus/ClickHouse/actions/runs/41596003933): **292 workflow files**, 40 push-enabled on the shared branch, **0 flagged unsafe** by the repo-local [audit](audit_workflows.py). Scope: static YAML branch/path/concurrency rules; not a guarantee about external tokens or future edits.
- [Native C++ tag collector prehash #41594366653](https://github.com/moleus/ClickHouse/actions/runs/41594366653): pinned O1 compiled, source change moves `TagsKey`/CityHash work before exclusive per-query collector mutex; same JOIN override as control.
- [Full native prehash A/B/B/A #41595298577](https://github.com/moleus/ClickHouse/actions/runs/41595298577): `project`14d **2224→1993ms** (11.6% faster), `branch` regex14d **2205→2273ms** (3.1% slower); `stage` baseline drift31.6% **invalid**, `whole` missing QueryFinish **invalid**. No promotion to final stack until lock profiling validates cause and mixed-workload gates pass.
- [Native built-in scheduler/lock events #41595899121](https://github.com/moleus/ClickHouse/actions/runs/41595899121): all three jobs successful (stage, project, whole). Stage14d: 1 thread1022ms/1031ms CPU vs4 threads ~516–554ms/~1385–1436ms CPU; 8 threads ~523ms, no stable gain. `OSCPUWaitMicroseconds` measures runnable CPU queue, not mutex. Built-in `GlobalThreadPoolLockWaitMicroseconds`, `LocalThreadPoolLockWaitMicroseconds`, `ContextLockWaitMicroseconds` near zero; no proof about collector mutex.
- [off-CPU v1 #41594014184](https://github.com/moleus/ClickHouse/actions/runs/41594014184) and [v2 #41594810932](https://github.com/moleus/ClickHouse/actions/runs/41594810932): **rejected as observer-polluted**. Sampling all threads every25ms cost up to81% of wave; even a100ms lighter sampler cost28–40%, one job hit total server RSS limit. Preserve these as negative measurement-method results.
- [Source lock audit](LOCK_CONTENTION_AUDIT.md): `storeTagsTyped` holds shared lock over O(N) lookup and exclusive lock over O(unresolved IDs) tag hash/allocation+two map inserts. **Collector is per query**, so client concurrency ≠ contention on the same collector.
- [Exact collector lock instrumentation](../ts-lab-cpu/apply_tagcollector_lock_events_cpp.py): adds per-query `ProfileEvents` wait/hold nanoseconds for shared and exclusive locks, plus acquisitions and unseen ID count. [Build](https://github.com/moleus/ClickHouse/actions/runs/41596232124) and [ABBA comparator](../ts-lab-cpu/bench_tagcollector_lock_profile.py). C++ first build failed to link event references because a translation unit needed explicit extern declarations; fixed and recompiled. **No lock timing conclusion yet**.
- [Stock unmodified O1 ClickHouse build](https://github.com/moleus/ClickHouse/actions/runs/41596421285) is needed for a matched source/LLVM/flags stock baseline before calling any cumulative stack faster than default ClickHouse.

## Further results: lock wait, stock baseline, and processor DAG

- [**Exact collector lock #41596955580**](https://github.com/moleus/ClickHouse/actions/runs/41596955580): `project`14d completed all three `max_threads=1/4/8` A/B/B/A checks, exact output parity. Four threads: exclusive hold383→366ms, exclusive wait4.57→3.36ms, shared wait348→333ms, but wall **1851→1862ms** (regression). Eight threads: wall **1877→1923ms** (regression). **Prehash not promoted**. Stage/whole jobs failed to find last QueryFinish; bounded log retry added after that run. Do not interpret sum of shared wait across query threads as wall critical path.
- [**Stock/unpatched ClickHouse O1 vs raw UUID schema #41596921882**](https://github.com/moleus/ClickHouse/actions/runs/41596921882): all four native A/B/B/A selectors passed; source `52529f0c711566d696917678bf15e7068772fdde`, unchanged C++, pinned LLVM22. `stage`14d **998→699ms** (1.43×), `project`14d **1799→1576ms** (1.14×), `branch` regex14d **1917→1688ms** (1.14×), `whole`1d **477→368ms** (1.30×). Matched immutable fixture, 4/4 exact native outputs; raw schema only recommended for this high-churn series distribution. [Tested opt-in new-table DDL](RAW_ID_HIGH_CHURN_V11.sql).
- [**Native processor graph #41601027396**](https://github.com/moleus/ClickHouse/actions/runs/41601027396): stock O1 baseline vs raw UUID, full `system.processors_profile_log` by processor ID/parent_ids with operator work, input starvation, output backpressure and trace CPU. Both `stage`14d and `whole`1d succeed after fixing root AGENTS symlink and adding [synthetic DAG unit tests](../ts-lab-cpu/test_native_pipeline_graph.py). Stage after raw UUID: MergeTree reads sum ~531ms CPU, Expression ~105ms, AdaptiveBucketMergingSource~62ms, MergeJoin~23ms. Whole1d: MergeTree~347ms, Expression~248ms, MergeJoin~145ms, adaptive bucket~60ms. `input_wait_elapsed_us` summed over all 39 expression processors is **not** query wall wait. Structural DAG longest sum uses accumulated processor work, not a real pipeline critical-path measurement.
- [**Next factorial #41601352971**](https://github.com/moleus/ClickHouse/actions/runs/41601352971): stock O1 + explicit raw UUID on both variants, `store_time_ranges=1` versus `0`, at 1/3/7/14 days. Isolates time metadata JOIN/scan cost **after** fixing ID type; decide selective vs broad/time-window crossovers with exact output check.
- **Global agents/checkout recovery:** root `AGENTS.md` has mode 120000 pointing to `.claude/CLAUDE.md`. Accidentally replacing symlink target with long Markdown broke checkout ([41597417015](https://github.com/moleus/ClickHouse/actions/runs/41597417015)); restored in `6d9143853b2c49c6bd7522b77f998e4418b5c695`, and placed shared-branch instructions in real `.claude/CLAUDE.md`. All subsequent checkout jobs validated clean symlink. Never edit `AGENTS.md` via GitHub Contents API.

## TimeRanges factorial and adaptive planner checkpoint

- [Full source-grounded cost model and A/B/B/A results](TIMERANGES_ADAPTIVE_COST_MODEL.md) — [CI #41601352971](https://github.com/moleus/ClickHouse/actions/runs/41601352971), **stock O1 and raw UUID in BOTH tables**, `TimeRanges` ON vs OFF; 1/3/7/14d, no C++ changes.
- Stage: 1d **ON**289ms vs OFF327ms (OFF slower13%), 3d 404 vs376ms (OFF faster7%), 7d 508 vs474ms (OFF faster7%), 14d 540 vs519ms (OFF faster4%). All stable.
- Project: 1d **ON**478 vs OFF928ms (OFF slower94%), 3d 717 vs1075ms (OFF slower50%), 7d 957 vs1289ms (OFF slower35%), 14d 1638 vs1576ms (OFF faster4%). All stable.
- Whole metric: 3d ON2041 vs OFF3205ms (OFF slower57%; stable), 7d **total-RSS OOM** around4.66GiB; 1d baseline drift45.7% invalid, 14d not run. **Do not disable TimeRanges globally**; memory constraints must be part of any adaptive planner.
- [Stock RAW_ID query parallelism #41602036007](https://github.com/moleus/ClickHouse/actions/runs/41602036007): native `max_threads=1,2,4,8`, exact query parity, CPU scheduler wait and thread-pool lock events after the raw UUID fix. This measures remaining parallelism vs CPU efficiency without `/proc` observer.

## RAW_ID scheduler efficiency results

[Native stock O1 after raw UUID ID #41602036007](https://github.com/moleus/ClickHouse/actions/runs/41602036007): `max_threads=1,2,4,8` varied **within one native query**, not concurrent client requests; 1/4 or 2/4 or 8/4 A/B/B/A, exact full PromQL equality, `query_log.ProfileEvents` CPU and OS runnable delay. These results refer only to this fixture and GitHub runner.

- Stage14d: 1 thread **573ms wall/578ms CPU**, 2 **386/736ms**, 4 **~331/880ms**, 8 **~331/882ms**. The 4→8 increase shows no meaningful gain. Values for four threads taken from the 4-vs-2 control, which has 0.73% drift. CPU vs latency is a policy choice; do not claim Amdahl serial fraction from these alone.
- Project14d: 1 thread **1448ms wall/1465ms CPU**, 2 **790/1474ms**, 4 **~762/1420ms** (4-vs-2 control), 8 **~760/1418ms**. More than four threads adds no stable latency benefit. Native built-in `GlobalThreadPoolLockWaitMicroseconds` and `LocalThreadPoolLockWaitMicroseconds` remain near zero; this does not measure `TimeSeriesTagsCollector` locks.
- Whole1d: outcome pending in the same workflow at the time this entry was written; do not invent results. Check current Actions job before promoting a threading default.

## Research priorities beyond UUID

Prioritize allocation and copying in `Map` label materialization, repeated ID set construction, stateful `timeSeriesStoreTags`, HASH build & reserve, `avg_over_time`/grid aggregation and scheduler overhead. For each hotspot: source-level call chain, exact CPU samples, off-CPU/on-CPU attribution, concurrent thread count, memory footprint, experimental intervention, and complete native PromQL parity.

Do not confuse `UUID` uniqueness per **series** with uniqueness per **sample**: a long-lived series repeats its identifier across buckets; dictionary encoding may help some dense workloads.
