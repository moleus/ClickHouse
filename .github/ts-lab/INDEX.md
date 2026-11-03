# TimeSeries performance lab — index

**Single write branch:** `lab/ts-cbo-adaptive-20261103`. All agents should commit their research here. Keep one append-only history; do not reset, amend, rebase, or force-push.

## Entry points

| Purpose | Resource |
|---|---|
| Shared agent workflow and conflict rules | [AGENT_GUIDE.md](AGENT_GUIDE.md) |
| Root-level question owners and collision gate | [R01 owner](claims/roots/R01.json), [R04 owner](claims/roots/R04.json), [R08 owner](claims/roots/R08.json); [validated CI](https://github.com/moleus/ClickHouse/actions/runs/41612252018) |
| ChatGPT web restarts, GitHub auth, token limits, VM scratch and hourly monitoring | [SESSION_BOOTSTRAP.md](SESSION_BOOTSTRAP.md) |
| Research coordination, prioritized next hypotheses, atomic claims and evidence protocol | [RESEARCH_COORDINATION_20261103.md](RESEARCH_COORDINATION_20261103.md) |
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
- Whole1d: 1 thread **885ms wall/893ms CPU**, 2 **564/1037ms**, 4 **~561/1346ms** (2-vs-4 control), 8 **557/1047ms** (8-vs-4 control ~570/1100ms). The two-thread variant keeps approximately the same wall latency as four while requiring **23% less CPU** in the paired run. That test has 18.3% control drift, so effects smaller than this need retesting; 8 threads provide no robust benefit. All three jobs in [#41602036007](https://github.com/moleus/ClickHouse/actions/runs/41602036007) passed.

## Research priorities beyond UUID

Prioritize allocation and copying in `Map` label materialization, repeated ID set construction, stateful `timeSeriesStoreTags`, HASH build & reserve, `avg_over_time`/grid aggregation and scheduler overhead. For each hotspot: source-level call chain, exact CPU samples, off-CPU/on-CPU attribution, concurrent thread count, memory footprint, experimental intervention, and complete native PromQL parity.

Do not confuse `UUID` uniqueness per **series** with uniqueness per **sample**: a long-lived series repeats its identifier across buckets; dictionary encoding may help some dense workloads.

## 2026-11-03: native client concurrency and LC decoder safety

- [Native multi-client stock O1 #41605951284](https://github.com/moleus/ClickHouse/actions/runs/41605951284): source and fixture pinned, same stock O1 binary for both v11 schemas, strict full native PromQL parity and A/B/B/A at **1/4/8 simultaneous clients**. Measures burst throughput, p50/p95/p99 HTTP latency, query CPU, runnable wait, pool-lock counters, query memory, low-overhead process RSS and physical parts. [Script](../ts-lab-cpu/bench_native_multiclient_stock_20261103.py), [self-triggered isolated workflow](../workflows/ts-lab-native-multiclient-stock-20261103.yml). No result or gain assumed until all jobs and drift gates finish.
- [LC decoder prototype safety audit](../ts-lab-cpu/LC_DECODER_FASTPATH_SAFETY_AUDIT_20261103.md): proposed fresh dictionary adoption **bypasses stock checkIndexesAreLimited** in the no-global branch. The shared-dictionary range insert copies index values without the original bounds validation; malformed indexes can escape stock `INCORRECT_DATA` handling. Do not promote prototype until validation and corruption/nullable/continuous-read tests pass. This audit does not cancel the preexisting C++ build.

## 2026-11-03: completed stock O1 1/4/8-client native matrix

[Full evidence and failure log](../ts-lab-cpu/NATIVE_MULTICLIENT_STOCK_RESULTS_20261103.md) — [CI #41605951284](https://github.com/moleus/ClickHouse/actions/runs/41605951284), pinned stock O1 source `52529f0c7`, executable SHA256 `f0a2fa1f814c58853f43423705cac10ab9df3bfe5887e27ebe9620ba7cb9660e`, 1.33m SHA-verified fixture, one stock executable for both schemas. Native PromQL full value/time/label parity and control drift <8% on every completed A/B/B/A combination.

- Stage14d LC→RAW UUID p50 at **1/4/8 clients**: 397→322 / 1172→969 / 2291→1715ms; 8-client CPU/request 1092→820ms, measured burst throughput 3.294→4.356 req/s.
- Project14d at **1/4 clients**: 1763→1471 / 4623→3605ms, each full valid A/B/B/A. At 8 clients: `MEMORY_LIMIT_EXCEEDED` at 4.47 GiB total RSS; **no eight-client comparison** and overall project job FAILURE. The memory ceiling after 4-client phases can be affected by allocator retention.
- Whole metric1d **1/4/8 clients**: 648→476 / 1699→1284 / 3396→2656ms; all parity/drift gates PASS. At 8 clients CPU/request 1575→1205ms.
- CPU stacks show original LC UUID dictionary work persists under concurrent load (e.g. stage 8-client representative 201/1050 CPU samples vs RAW 11/738). The 4-vCPU runner reaches about 90% burst CPU demand at 4–8 clients. OS runnable wait is scheduling pressure, **not mutex wait**. Separate [fresh-process project8 CI #41607617562](https://github.com/moleus/ClickHouse/actions/runs/41607617562) tests whether server process retention causes the 8-client OOM; outcome pending.

## 2026-11-03: fresh-process project8 OOM reproduced; thread budget follow-up

[Fresh-process 8-client project14d full failure record](../ts-lab-cpu/NATIVE_FRESH8_MEMORY_FAILURE_20261103.md), [CI #41607617562](https://github.com/moleus/ClickHouse/actions/runs/41607617562): same stock O1 and 1.33m fixture, identical native PromQL and 4.47GiB memory budget, but a **new ClickHouse server for each LC/RAW/RAW/LC phase**. Every phase completed exactly two warmup bursts of eight requests; the next burst hit genuine `MEMORY_LIMIT_EXCEEDED`. Thus 8-client failure is **not solely caused by RSS retained from previous 1/4-client experiments**. No valid 8-client timing comparison. The terminating `AggregatingTransform` is not a proven allocation root cause.

[Native RAW_ID client thread-budget CI #41608382567](https://github.com/moleus/ClickHouse/actions/runs/41608382567): source-pinned A/B/B/A `max_threads=2/1/1/2` at fixed 8 clients for stage14d/whole1d and fixed 4 clients for project14d. Same full native PromQL parity, query CPU, scheduler wait, memory/RSS, p50/p95 and burst throughput. [Script](../ts-lab-cpu/bench_native_client_thread_budget_20261103.py), [workflow](../workflows/ts-lab-native-client-thread-budget-20261103.yml). Do not claim benefit before CI result.

## 2026-11-03: all native per-query thread-budget A/B/B/A controls completed

[Full source-pinned evidence](../ts-lab-cpu/NATIVE_CLIENT_THREAD_BUDGET_RESULTS_20261103.md) — [CI #41608382567](https://github.com/moleus/ClickHouse/actions/runs/41608382567), all 3 jobs PASS; 1.33m canonical fixture and same raw UUID stock O1 binary. Fixed simultaneous client count, compare `max_threads=2/1/1/2` with complete native PromQL parity.

| Workload and clients | p50 threads2→threads1 | CPU/request threads2→threads1 | Control drift |
|---|---|---|---|
| stage14d, 8 | 1521→1452ms (-4.6%) | 737→696ms (-5.6%) | 4.65% |
| project14d, 4 | 3959→3849ms (-2.8%) | 3615→3522ms (-2.6%) | 3.75% |
| whole1d, 8 | 2702→2684ms (-0.7%) | 1234→1225ms (-0.7%) | 2.40% |

These small p50 deltas are comparable to A/A baseline drift; no robust universal gain from reducing per-query threads. At stage8 runqueue wait baseline alone changes markedly between A phases; do not assign it entirely to the thread setting. Do not use these results to claim project8 memory survival: [fresh-process project8 threads1 CI #41609140400](https://github.com/moleus/ClickHouse/actions/runs/41609140400) tests it independently.

## 2026-11-03: fixed-clients project8 threads1 memory result also fails

[CI #41609140400](https://github.com/moleus/ClickHouse/actions/runs/41609140400), [full source-pinned artifact analysis](../ts-lab-cpu/NATIVE_FRESH8_THREAD1_CAPACITY_20261103.md):
same pinned stock O1 and fixture, new ClickHouse process before each LC/RAW/RAW/LC phase,
8 concurrent project14d clients at **max_threads=1** (instead of 2).
Phase LC A0 passes all 10 bursts (p50 5102ms, 4.01 GiB RSS peak);
RAW B1 passes 8 bursts then Code241 OOM;
RAW B2 passes 1 then OOM;
LC A3 passes 3 then OOM. **Overall FAIL.**
The second LC phase fails despite the first LC success, proving phase-dependent
memory survival. No valid RAW/LC A/B/B/A 8-client latency comparison.
Reducing max_threads to 1 delays the capacity failure in some phases but is **not a
safe reliable fix**, and its capacity effects cannot be generalized to production jemalloc.

## 2026-11-03: R01 canonical mixed-cadence 400-node native results

[Immutable R01 evidence](evidence/R01-k8s-mixed-v1-41612120162.json) /
[full bucket-aware cost model](../ts-lab-r01/STORAGE_ID_COST_MODEL_20261103.md) —
[stock O1 full native A/B/B/A CI #41612120162](https://github.com/moleus/ClickHouse/actions/runs/41612120162)
PASS, complete PromQL labels/timestamps/numeric parity, same 64-bit ID digest and unchanged physical parts.
Fixture SHA256 `ea9a8d43c9e58ed916f1bf43949556d8ed18169ddd14033c293af4cd54c9881d`:
400 node slots, 3h life, three days, 28,896 series, 2,488,320 points, 86,688 hourly SAMPLES bucket rows.
**R01 primary root claim** already coordinated with a separate limited HPA/lifecycle-fixture support agent;
do not start duplicate large R01 experiments.

- `node_broad` p50 LC242.7→RAW218.3ms, CPU455.5→431.3ms; control drift **8.66%** versus 11.2% speedup (weak).
- `pod_namespace` p50 75.3→66.3ms (1.135×), CPU126.2→110.2ms; drift **1.50%** (stronger).
- `pod_broad` p50 212.4→194.4ms (1.092×), CPU396.0→374.1ms; drift **8.58%** (weak).
- `burst15` p50 54.4→46.9ms (1.158×), CPU89.2→74.2ms; drift **2.60%** (stronger).
- RAW compressed three-target footprint **7,424,806 B vs LC 7,569,815 B** (1.92% smaller on this fixture).
- **Source correction:** v11 SAMPLES stores arrays of samples per **hourly bucket**; ID repetition is
  only **3 SAMPLES bucket rows per series**, not 86 sample points per series. A prior invalid benchmark
  [#41611838191](https://github.com/moleus/ClickHouse/actions/runs/41611838191)
  mistakenly equated physical rows and sample count; corrected to `sum(length(samples))` for R01 rerun.
  Preserve both runs; earlier invalid run supplies only physical evidence, not native latency.
- Current decision: **ACCEPT_FOR_NEXT_GATE**, not production/HA/VM proof.
  Next distinct hypothesis is ID type × SAMPLES bucket-step factor (changes ID repetition and time pruning);
  it needs a separate root claim and no concurrent duplicate. The other R01 fixture's stagger/HPA/counter
  semantics and 14-day/concurrent/write production parity remain untested here.

## 2026-11-03: R10 2×3 TimeSeries SAMPLES bucket x ID factorial

[Immutable 20-pair native A/B/B/A evidence](evidence/R10-bucket-id-factorial-v1-41613268106.json) /
[GitHub Actions #41613268106](https://github.com/moleus/ClickHouse/actions/runs/41613268106) **SUCCESS**.
Same pinned stock O1 source/binary, same R01 fixture SHA256 `ea9a8d43c9e58ed916f1bf43949556d8ed18169ddd14033c293af4cd54c9881d`, same full native PromQL outputs, same TAGS ID digest and 2,488,320 points/table. 12 LC↔RAW ID comparisons at 900/3600/10800s plus 8 within-ID bucket-step comparisons, all 20 A/B/B/A controls valid (max drift 12.944%, no OOM). Root claim R10 is independent of R01.

| Step | SAMPLES bucket rows | LC three-table disk | RAW three-table disk |
|---:|---:|---:|---:|
| 900s | 346,752 | 7,955,846 B | 7,798,682 B |
| 3600s | 86,688 | 7,569,815 B | 7,424,806 B |
| 10800s | 28,896 | 6,966,960 B | 6,833,238 B |

- Strong negative control: **RAW pod_namespace 1d** at 900s vs3600s: p50 **37.270→48.052ms (+28.9%)**, CPU **61.184→88.962ms (+45.4%)**, read_rows **102,368→218,592 (+113.5%)**; baseline control drift only **0.142%**. LC also slows 28.7% on the same selector.
- 10800s RAW versus3600s reduces physical disk **~8%** and node-broad read_rows **57,728→38,592**, but node-broad p50 **91.906→95.784ms (+4.2%)**, while namespace p50 **37.526→36.709ms (-2.2%, close to 1.7% drift)**. **No universal best bucket**.
- RAW UUID remains mildly faster than LC at every step on this fixture (~1.03–1.07× by selector); benefit is smaller than original one-point-per-series Jenkins fixture.
- Decision **ACCEPT_FOR_NEXT_GATE** only. Need **15m/1h/3h lookback** and bucket-boundary parity; 1d avg windows alone cannot justify changing the global 3600s SAMPLES bucket setting. Profiled CPU/read data in artifact; no production allocator or concurrent-client proof.


## 2026-11-03: independent R08, R04 and R02 proofs and research workflow audit

- **R08 — ACCEPT_FOR_NEXT_GATE (semantics only)**. [Native matcher oracle CI #41610517632](https://github.com/moleus/ClickHouse/actions/runs/41610517632): 42/42 exact labels/timestamps/values against independent Python oracle. Tested absent/empty, =, !=, =~, !~, anchored regex, mixed selectors and zero results, on two real physical parts before `OPTIMIZE FINAL` and one part after. Pinned O1 binary SHA `3990e373...`, fixture SHA `6ece3bbb...`. [Immutable evidence](evidence/R08-matcher-absence-regex-v1/41610517632.json). Does not cover stale NaN/counters/ACL/performance.
- **R04 — ACCEPT_FOR_NEXT_GATE (RecentSamples TTL semantics only)**. [Native hot/straddle/cold, late-insert and merge CI #41612199798](https://github.com/moleus/ClickHouse/actions/runs/41612199798): 18/18 independent numeric and physical SELECT target assertions, hot 5m/30m reads RecentSamples; other four windows read main SAMPLES. After merge, two separate sample stores occupy 2506 B recent and 2571 B main, too small to extrapolate production amplification. [Immutable evidence](evidence/R04-recent-ttl-parity-v1/41612199798.json). Physical expiration, CPU, write amplification and wall still unmeasured.
- **R02 — ACCEPT_FOR_NEXT_GATE (write correctness and compressed footprint, NOT steady-state CPU)**. [Corrected stock O1 periodic write CI #41613436491](https://github.com/moleus/ClickHouse/actions/runs/41613436491): twelve 4000-row batches/table, 24 QueryFinish inserts, 18 MergeParts events, 48000 actual sample points and 10000 logical series per table; LC and RAW native avg oracle exact. Compressed all-target bytes LC 939088 vs RAW 877576 (-6.55%). PartLog one-trial merged CPU LC119.94ms vs RAW103.43ms, but no replicated write CPU claim; INSERT QueryFinish reports only 15ms CPU against ~800ms wall and is not whole-process write CPU. [Immutable evidence](evidence/R02-small-batch-raw-lc-write-v1/41613436491.json). Previous [#41613225939](https://github.com/moleus/ClickHouse/actions/runs/41613225939) is **INFRA_FAIL**, not a slower/faster result: missing `system.query_log`, corrected in isolated workflow. [Negative evidence](evidence/R02-small-batch-raw-lc-write-v1/41613225939.json). Next R02 needs same-runner repeated writes with measured whole-process CPU, background merges and simultaneous reads.
- **Coordination**. [Status snapshot fix](https://github.com/moleus/ClickHouse/commit/f5d16ce10db335244ce4ae91cb9b59ddb340a03d) now scans active GitHub Actions pages, requested/waiting/pending queue states and legacy `lab/timeseries-*` branches; explicit partial inventory flags prevent silent omissions. [Regression tests](https://github.com/moleus/ClickHouse/commit/27deb6b22eba90d5288a02f3203a349ad8543a35) PASS in [CI #41611345003](https://github.com/moleus/ClickHouse/actions/runs/41611345003). Another agent added canonical create-only [R01/R04/R08 root locks](claims/roots) and a passing [state gate #41612252018](https://github.com/moleus/ClickHouse/actions/runs/41612252018); R02 root lock added before experiment. Current GitHub root and claim files, not mutable index, define exclusive ownership. Two overlapping R01 fixture attempts have distinct scope; do not merge their measurements without identical fixture contracts.

## R05: candidate ID set versus physical SAMPLES mark coverage (2026-11-03)

- [Canonical root](claims/roots/R05.json), [claim](claims/R05-candidate-id-mark-bound-v1.json), [source and mathematical feasibility gate](experiments/R05_MARK_COVERAGE_FEASIBILITY_20261103.md), [create-only handoff](handoffs/20261103-r05-mark-bound-04.json).
- No runtime improvement claimed, no new build. A uniform g-distinct-IDs/mark model predicts touched-mark fraction `1-(1-s)^g`; it is only a diagnostic prior. Next measure actual SAMPLES selected marks, ID clustering and native query parity with existing artifacts. R01/R02/R04/R08 owner boundaries remain unchanged.

[Native processor-level CPU attribution for R10](../ts-lab-r10/READ_CPU_ATTRIBUTION_20261103.md): representative `MergeTreeSelect(ReadPool)` work 30,475→54,593µs and read/decode CPU samples33→57 for RAW namespace1d when SAMPLES bucket step is 3600→900s; this is compatible with extra physical read/decode work, **not** a wall critical-path or lock-contestion proof. The broad-node 10800s 1d wall regression is not explained by read processor CPU alone.

## 2026-11-03: R10 short-lookback follow-up disproves small-bucket assumption

[Native 12-pair short-window A/B/B/A #41614179460](https://github.com/moleus/ClickHouse/actions/runs/41614179460),
[immutable machine-readable evidence](evidence/R10-short-lookback-41614179460.json).
Same source, pinned stock O1 binary, SHA-verified R01 400-slot 3d fixture and three RAW_UUID TimeSeries tables at 900/3600/10800s. All 12 pairs passed complete native PromQL label/time/value parity, physical point counts and control-drift gates, no OOM.

- `pod_namespace [15m]`, 3600s→900s: **p50 28.357→30.959ms (+9.2%)**, CPU38.45→44.34ms (+15.3%), baseline drift2.48%.
- `pod_namespace [1h]`, 3600s→900s: **29.708→32.115ms (+8.1%)**, CPU40.54→45.65ms (+12.6%), drift0.40%.
- `node_broad [15m]` across hour boundary, 3600s→10800s: **44.416→40.123ms (-9.7%)**, CPU78.41→62.35ms (-20.5%), read_rows56,704→38,592, drift1.07%.
- `pod_namespace [3h]`, 3600s→10800s: **36.965→33.951ms (-8.2%)**, CPU51.05→50.28ms, read_rows66,784→59,360, drift0.33%.
- Previous [R10 1d full-native factorial #41613268106](https://github.com/moleus/ClickHouse/actions/runs/41613268106) found 10800s **4.2% slower** for broad nodes compared with 3600s, so longer buckets are not universally faster. A 900s bucket is **not** intrinsically better for a 15m PromQL lookback: reader/bucket-row overhead matters, and shorter seek ranges do not guarantee fewer physically scanned rows.

No global change to `samples_bucket_step_seconds` is justified. Next useful question: native operator-level causal attribution and ingestion/merge costs for 3600 vs10800s under varied selectors and 14d+concurrent loads; reuse R02 source-pinned write-path work rather than repeating it. Sample-array count is not physical bucket-row count.

## 2026-11-03: R10 batch-write and explicit merge completed, fresh-process ABBA

[Full immutable machine-readable evidence](evidence/R10-write-bucket-paired-41616086196.json) —
[CI #41616086196](https://github.com/moleus/ClickHouse/actions/runs/41616086196) **PASS**.
Same frozen R01 400-node/3h/3d fixture, 28,896 series and 2,488,320 points,
unmodified source-pinned O1 executable SHA `f0a2fa1f814c58853f43423705cac10ab9df3bfe5887e27ebe9620ba7cb9660e`.
Four **fresh ClickHouse processes**, query/fixture/binary exact; RAW UUID
only, `samples_bucket_step_seconds=3600/10800/10800/3600`.
73 HTTP INSERT batches of up to 400 series in **each** phase, then explicit
`OPTIMIZE FINAL` of TAGS/SAMPLES/TimeRanges with native result parity,
`sum(length(samples))` exact count, 292/292 QueryFinish INSERT records
and 24 `MergeParts` events. All drift gates pass.

| Metric | 3600s baseline | 10800s candidate | Change | A/A drift, baseline |
|---|---:|---:|---:|---:|
| Server process CPU during inserts | 1.590s | 1.520s | -4.4% | 2.52% |
| Insert elapsed wall | 5.312s | 5.218s | -1.8% | 0.24% |
| Server process CPU in explicit merge | 0.825s | 0.720s | **-12.7%** | 1.21% |
| Explicit merge wall | 0.581s | 0.483s | **-16.9%** | 3.29% |
| Three-target compressed part bytes | 7,424,806 | 6,833,238 | **-8.0%** | 0% |

Independent part_log MergeParts CPU corroborates merge direction:
average ~682→587ms (-14%), six events each phase.
This is a **controlled explicit merge**, not steady-state background
merging with concurrent reads. `query_log` reports only ~70ms INSERT
CPU for the 73 requests while process CPU is ~1.5s; never treat
QueryFinish INSERT CPU as complete storage/writer CPU.
The small 4.4% total write CPU effect is close to ~2.5% control drift
and needs more runs. RSS snapshots do not establish variant advantage.
**Decision: ACCEPT_FOR_NEXT_GATE** for this synthetic workload only.
A 3h bucket helps compressed footprint/explicit merges but can be
slower for some broad native 1d reads; no universal default change.

## 2026-11-03: independent OOM-aware glibc eight-phase audit (no new CI)

[Raw artifact #41607288907](https://github.com/moleus/ClickHouse/actions/runs/41607288907)
and [independent phase-by-phase audit](../ts-lab-cbo/GLIBC_FRONTIER_EIGHT_PHASE_AUDIT_20261103.md):
expected OOMs occurred in *both* default glibc phases (phase0's 10th 7d
query; phase7's 14d query). Six arena4/arena8/trim phases each passed
12 ×7d plus 14d. Exactly **101 recorded unique query IDs**, not 104:
three default-phase queries were never attempted. Workflow SUCCESS means
diagnostics were collected, not that all 104 requests succeeded.

A reducer expecting 13 entries in every phase and 104 unique IDs **cannot
validly reduce this observed censored OOM run**; keep `default` comparison
INCONCLUSIVE rather than inventing the missing queries. Among completed
candidate pairs (same runner), arena4/trim are exploratory CPU/RSS Pareto
choices; arena8 RSS varies **43%** between repeated phases and must not be
treated as stable. glibc tuning is not a production jemalloc recommendation.

## 2026-11-03: R10 1/4/8 concurrent clients full-native matched controls PASS

[Machine-readable 9-case A/B/B/A evidence](evidence/R10-concurrent-buckets-41617111380.json),
[CI #41617111380](https://github.com/moleus/ClickHouse/actions/runs/41617111380):
same pinned stock O1/binary, same R01 SHA-verified 3d 400-slot multi-sample fixture,
3600 vs10800s RAW UUID buckets, **four new server processes per contrast**.
3 native selectors ×1/4/8 independent clients; 2 warmup+8 measured concurrent
waves/phase, exact nonempty full PromQL label/time/value equality,
identical logical ID digest/physical parts, no OOM; all A/A p50/CPU/QPS
control gates pass. `max_threads=2` per request.

At **8 clients**:
| Selector | p50 ms 3600→10800 | CPU/query ms 3600→10800 | requests/s 3600→10800 | A/A p50 drift |
|---|---|---|---|---|
| node_broad | 980→941 | 466→448 | 7.656→7.931 | 1.35% |
| pod_namespace | 254→237 | 121→113 | 28.775→30.595 | 1.95% |
| pod_broad | 921→864 | 429→406 | 8.127→8.579 | 0.78% |

Caveats: n4 node-broad p50 improvement ~13.2% vs
**21.4% baseline drift**, so cannot claim robust latency from that case;
n8 namespace **p99 regresses** 300.36→303.74ms.
R10 read marks fall 117→53 for node, 229→82 for namespace, 233→82 for
broad pods; this is *physical index work*, not proof of mutex contention.
Do not claim production RSS or p99 gain based on one runner or 4Hz RSS snapshots.
**Next gate:** check sensitivity to aligned 3h synthetic node startup and
then 14d/staggered real-shaped load with write/merge concurrency.

## 2026-11-03: Glibc diagnostic reducer repaired without fabricated queries

[Historical eight-phase #41607288907](https://github.com/moleus/ClickHouse/actions/runs/41607288907),
[OOM-aware reducer validation #41617708973](https://github.com/moleus/ClickHouse/actions/runs/41617708973) **SUCCESS**,
[immutable result](evidence/CBO-glibc-frontier-oomaware-41617708973.json).
101 verified unique query IDs rather than 104, because first default phase
aborted after the 10th query on Code241. Second default phase OOM on 14d.
Two complete phases each for arena4, arena8, trim PASS full 7d+14d;
arena8 RSS A/A drift **43%**. Original strict reducer assuming 104
complete requests was invalid for this expected diagnostic failure;
new reducer treats default as censored and does **not** produce a spurious
matched default performance baseline. These glibc observations are **not**
production-jemalloc guidance.

## 2026-11-03: R10 timestamp-alignment confounder source-proof and fixture gate

[Staggered fixture CI #41618267456](https://github.com/moleus/ClickHouse/actions/runs/41618267456)
PASS. The **original aligned** 97,177,010-byte fixture SHA stays exactly
`ea9a8d43c9e58ed916f1bf43949556d8ed18169ddd14033c293af4cd54c9881d`.
The new **staggered** source variant offsets each node slot
by `(slot*317)%10800` seconds, SHA
`ae85496efcab3417634ac812cbd50a40c4eea297a397b50792efd024838e1b9a`.
Both still contain 28,896 distinct series and 2,488,320 points,
but independently calculated SAMPLES bucket-row predictions change
900s: 346,752→368,544; 3600s: 86,688→113,760; 10800s:
28,896→57,120. This is a model/fixture validation, **not yet a ClickHouse
physical read benchmark**. First/last shifted lifetimes may extend beyond
the three-day window; not a true 400-active-node capacity simulator.
[Physical and native ABBA CI #41618941657](https://github.com/moleus/ClickHouse/actions/runs/41618941657)
tests the actual count and query cost without a new C++ build.


## 2026-11-03: R05 SAMPLES 256-row default and negative mark-range surrogate

R05 owner `chatgpt-r05-mark-bound-20261103`; [root](claims/roots/R05.json), [source and mathematical correction](experiments/R05_MARK_COVERAGE_FEASIBILITY_20261103.md), [surrogate script](experiments/r05_sparse_mark_simulation.py), [3 tests](experiments/test_r05_sparse_mark_simulation.py), [deterministic output](experiments/R05_MARK_COVERAGE_SURROGATE_20261103.json), [handoff](handoffs/20261103-r05-mark-envelope-05.json).

Source-pinned v11 SAMPLES defaults to `(id,bucket)` order, **256 rows per mark** with a 512 KiB granularity-byte limit; earlier R05 hypothetical `g=8192` was **not** an SAMPLES granularity measurement. Candidate-ID selectivity alone does not prove mark pruning: in a synthetic independent 14-day, 95k-ID/day model, 2500 candidates present only in the final part overlap **5208/5208** mark-start key-range envelopes, although only **372/5208** marks contain matching IDs. Three local tests pass. **This is a surrogate, not native ClickHouse selected marks, latency, CPU, or RSS.** No new C++ build; actual persisted part and native EXPLAIN counters remain the decisive gate. R10's independent bucket-width studies are not proof of the R05 key-range mechanism.

## 2026-11-03: R10 staggered node-start native counterexample PASS

[Full source-pinned native ABBA #41618941657](https://github.com/moleus/ClickHouse/actions/runs/41618941657)
PASS, [immutable evidence](evidence/R10-staggered-native-41618941657.json),
[source and CPU attribution](../ts-lab-r10/START_ALIGNMENT_CPU_AUDIT_20261103.md).
The independent staggered generator changes only per-slot sample timestamps;
SHA256 `ae85496efcab3417634ac812cbd50a40c4eea297a397b50792efd024838e1b9a`;
tags, original values, 28,896 series and 2,488,320 sample elements unchanged.
Actual ClickHouse after merge: 3600s **113,760** SAMPLES bucket rows,
10800s **57,120**, in full agreement with the exact per-series math.
Both RAW_UUID layouts pass full nonempty native PromQL labels, times,
values and A/B/B/A drift gates; one active part per target, no OOM.

**Within the shifted fixture**, bucket3600→10800:
- `pod_namespace`: native p50 **73.726→85.345ms (+15.8%)**,
  CPU **135.859→168.388ms (+23.9%)**, control drift **1.68%**.
- `burst15`: p50 **54.164→61.632ms (+13.8%)**,
  CPU **90.879→114.482ms (+26.0%)**, drift **5.08%**.
- `pod_broad`: p50 **209.54→220.334ms (+5.2%)**,
  CPU **413.43→439.35ms**, drift **2.50%**.
- `node_broad`: p50 **272.19→261.66ms (-3.9%)**,
  but drift **13.19%**: inconclusive gain.

**Read-path paradox:** namespace `read_rows` **119,008→83,168**
and marks **294→154**, uncompressed read bytes **22.69→21.09MB**
decrease, yet CPU rises. One representative `MergeTreeSelect`
processor trace shows summed work **89.2→122.4ms** and CPU read/decode
samples **85→120**. More rows and marks are NOT a complete read CPU
cost model; larger bucket arrays or decoding cost matter. Single-query
profile work is not wall critical-path proof.

This forbids promoting `10800s` globally. The earlier *aligned* fixture
and this shifted experiment were on **different CI runners**, so the
hypothesis that alignment itself reverses performance needs
[a same-runner 2×2 interaction test #41619669176](https://github.com/moleus/ClickHouse/actions/runs/41619669176),
not a cross-runner ratio comparison. The staggered generator is an
alignment probe, not a complete 400-active-node HPA simulator.

## 2026-11-03: R10 same-runner 2×2 node-start alignment interaction confirmed

[Immutable source-pinned nested native ABBA evidence](evidence/R10-alignment-factorial-41619669176.json),
[CI #41619669176](https://github.com/moleus/ClickHouse/actions/runs/41619669176)
**PASS**. One runner, same unmodified O1 stock executable/LLVM/source SHA,
**four** RAW_UUID TimeSeries tables from original aligned fixture
`ea9a8d43...` and deterministic staggered fixture `ae85496e...`.
Every table has 28,896 series, 2,488,320 samples and the same logical
ID digest. Actual SAMPLES bucket rows for aligned are
3600s **86,688**, 10800s **28,896**; for staggered
3600s **113,760**, 10800s **57,120**, with one part per inner layer.

Outer fresh-server A/B/B/A `aligned/stagger/stagger/aligned`, nested
four-selector bucket A/B/B/A `3600/10800/10800/3600` per panel,
two warmup+six measured queries per phase: **512 full native
PromQL requests**, complete label/timestamp/numeric equality
*within* each fixture; different shift fixture values are intentionally
NOT compared at equal timestamps. All inner controls and repeated
bucket-effect drift checks PASS; no OOM.

| Selector | Aligned 10800/3600 p50 ratio | Staggered 10800/3600 p50 ratio | Aligned→staggered interaction |
|---|---:|---:|---:|
| pod_namespace | **0.97046** (−3.0%) | **1.25384** (+25.4%) | **1.29201** |
| burst15 | 1.00882 (+0.9%) | **1.23197** (+23.2%) | 1.22120 |
| pod_broad | 0.93209 (−6.8%) | 1.06298 (+6.3%) | 1.14043 |
| node_broad | 1.03086 (+3.1%) | **0.88253** (−11.7%) | 0.85611 |

The two aligned/staggered repeat differences in the namespace candidate
ratio were **2.74% / 0.52%**; effect direction is stable. Namespace
CPU 10800/3600 ratio aligned **0.9181** (−8.2%) but staggered
**1.2980** (+29.8%). **This is a verified interaction** between
time alignment and bucket width for this synthetic dataset; it removes
cross-runner doubt from comparing prior separate R10 jobs.

**Decision: reject blanket 10800s as a replacement for 3600s.**
Aligned 3h-life series are an unrealistic data-layout benchmark if real
nodes have arbitrary start phases. Candidate p50 may improve on broad
nodes but regress on selective pods. The staggered fixture has a
3d first/last lifecycle boundary artifact, is not a true 400-active-node
HPA simulator, and neither 14d nor production jemalloc has been tested.
[New staggered 4/8-client follow-up #41620367659](https://github.com/moleus/ClickHouse/actions/runs/41620367659)
is isolated, source/binary pinned and does not cancel other agents.


## 2026-11-03 R11: native PromQL QueryResultCache stale read reproduced (expected engine contract)

[Immutable R11 evidence](evidence/R11-native-promql-query-cache-consistency-v1/41619761382.json) / [stock O1 CI #41619761382](https://github.com/moleus/ClickHouse/actions/runs/41619761382), source 52529f0c7115, executable SHA `f0a2fa1f...`, artifact ZIP SHA `33490c9bf372...`. **REJECT query-cache optimization for read-after-write metrics without an explicit staleness budget.** On one synthetic TimeSeries series, native `avg_over_time` returned 1.0 before new INSERT, fresh uncached 2.0 afterward, but with `use_query_cache=1` and TTL120s returned **stale 1.0** after INSERT. `QueryCacheHits` rose 1→2; `SYSTEM DROP QUERY CACHE` restored 2.0, and inner SAMPLES points were verified 1→2. This is **documented expected behavior**, not an undiscovered bug: pinned `QueryResultCache.h` says INSERT/DELETE do not invalidate results, `Key::operator==` compares AST hash and query scope, `IsStale` is expiry-time based. This experiment specifically opted into caching; no claim any production deployment does so. No latency/CPU throughput gain measured.

## 2026-11-03 R13: independent 200-branch regex plan hypothesis staged

[Atomic root reservation](claims/roots/R13.json) / [immutable claim](claims/R13-large-regex-alternation-in-vs-match-v1.json) / [test script](experiments/r13_native_regex_in_vs_match.py) / [workflow](../workflows/ts-lab-r13-regex-literal-compact-20261103.yml). Pinned source `StorageTimeSeriesSelector::matcherToAST` converts pure regex alternatives to SQL `IN` through `tryGetLiteralAlternatives`, but compact character-class regex goes through anchored `match`. Independent Python oracle proves that `b0000|...|b0199` and `b0[01][0-9][0-9]` select the **same 200 of 1024 distinct branches**, i.e. 6400 of 32768 series, with exact same 16-stage output. Prepared same-runner native A/B/B/A 2 warmups+4 measurements per phase, QueryFinish CPU/IO/memory/drift; **manual-only while R10/R12 live measurement lanes are occupied**. Do not report performance difference until the CI run and physical plan are verified.

## 2026-11-03: R10 staggered 4/8-client concurrent full-native test rejects 10800s

[CI #41620367659](https://github.com/moleus/ClickHouse/actions/runs/41620367659)
**PASS** with 8 matched native A/B/B/A comparisons:
4 selectors × 4/8 simultaneous independent clients, query `max_threads=2`,
four new ClickHouse processes per contrast, 2 warmup+8 measured waves
per phase. All **1920 query IDs unique**, full nonempty PromQL
label/time/value parity, complete QueryFinish logs, physical layout
frozen, no OOM. Source-pinned stock O1 binary `f0a2fa1...`,
staggered fixture SHA `ae85496e...`. [Immutable evidence](evidence/R10-staggered-concurrent-41620367659.json).

| Staggered selector, 8 clients | p50 3600→10800 ms | CPU/query 3600→10800 ms | QPS 3600→10800 | p50 control drift |
|---|---|---|---|---|
| pod_namespace | **331→393 (+18.6%)** | **156→187 (+20.2%)** | **22.40→18.65 (−16.8%)** | 2.78% |
| burst15 | **218→259 (+18.9%)** | **103→125 (+21.9%)** | **33.65→28.32 (−15.9%)** | 0.38% |
| pod_broad | 1027→1074 (+4.5%) | 478→502 (+5.1%) | 7.28→6.99 (−4.1%) | 0.07% |
| node_broad | 1365→1389 (+1.8%) | 644→653 (+1.3%) | 5.56→5.51 (−1.0%) | 0.91% |

At n4 namespace p50 **161→195ms (+21.2%)** and burst15
**106→130ms (+22.1%)**. Node-broad n4 p50 +4% is **not** robust
because A/A control drift is 13.2%. Broad-pod n8 p99 slightly improves,
so do not generalize every tail direction.

**Decision: reject global switch to 10800s** for this measured synthetic
staggered multi-cadence workload. 10800s compresses ~6.5% smaller but
degrades selective PromQL CPU/QPS under concurrency; earlier aligned
4/8-client results are NOT representative of arbitrary node start phases.
The independent [same-runner 2×2 R10 #41619669176](https://github.com/moleus/ClickHouse/actions/runs/41619669176)
established the actual alignment interaction rather than comparing
different hosts. Source evidence: [bucket CPU cost audit](../ts-lab-r10/START_ALIGNMENT_CPU_AUDIT_20261103.md);
[query-mix break-even model](../ts-lab-r10/BUCKET_CPU_BREAK_EVEN_20261103.md).
No real 14d/HPA, continuous concurrent writes, or official jemalloc test;
do not promote based on storage bytes alone.


## 2026-11-03 R13: equivalent 200-branch regex syntax — NO_EFFECT on mixed-key TAGS layout

[Immutable scientific evidence](evidence/R13-large-regex-alternation-in-vs-match-v1/41621089700.json) / [stock-O1 native ABBA #41621089700](https://github.com/moleus/ClickHouse/actions/runs/41621089700) (artifact ZIP SHA `bdfa7a6fd305...`, fixture SHA `e102f02fbffb...`): 32768 synthetic distinct series, 1024 branches, 200 exact matching values, 6400 selected series, 16 stage groups. Two PromQL regex shapes select **identical** rows: 1199-byte literal `b0000|...|b0199` uses pinned-source `tryGetLiteralAlternatives` → `IN`; 16-byte `b0[01][0-9][0-9]` uses anchored `match`. Exact labels/timestamps/numeric oracle and 24/24 trial requests pass; 4 phases, two warmups and four measured each.

- Literal-IN wall p50 mean **63.414ms**, CPU **63.645ms**; compact-match **64.263ms**, CPU **64.183ms**. Difference +1.34% wall/+0.85% CPU, below 1.76% A/A wall drift. **NO_EFFECT** rather than a speedup; the original raw artifact's `ACCEPT_FOR_NEXT_GATE` was only an experimental gate, not scientific evidence of a performance win. [Script classifier corrected](https://github.com/moleus/ClickHouse/commit/9f79496283683e96428dc3afe6f3f88e92b762e3) to separate valid methodology from scientific effects.
- Direct `EXPLAIN PLAN` of `timeSeriesSelector` shows the same **6400-element ID set**, **128/128** SAMPLES granules in both cases; overall `read_rows=98304` for both. Rewriting branch regex cannot prune mixed-ID SAMPLES granules on this fixture. Query tracker peak ~19.06MB literal vs17.98MB compact, descriptive only; one-run memory effect unconfirmed.
- Do not spend more runner minutes on this exact small fixture. Test a new indexed-label physical layout or held-out branch=All fixture only with a fresh source-level bound and a new claim. No production/Grafana rewrite recommendation from 1.3% wall difference.

## 2026-11-03: R12 matched compiled LC decoder rejected, R09 source delta audited

[R12 immutable full native ABBA evidence](evidence/R12-lc-decoder-matched-41620022189.json);
[GitHub Actions #41620022189](https://github.com/moleus/ClickHouse/actions/runs/41620022189) **SUCCESS as a valid experiment, REJECT as optimization**.
Two SHA-verified LLVM22.1.8 O1 C++ binaries share exact pinned source, fast-sum-rate and opt-in JOIN override; the only additional patch in the experimental binary is `SerializationLowCardinality.cpp` fresh dictionary adoption. Same source fixture SHA256 `8f3a36846f80d6be5a8604d142e934399f2687094766c57bf105e43977f52858`, default LC UUID v11, 1.33m series. Four fresh-server A/B/B/A workloads passed complete native PromQL label/time/value parity, physical part parity, no OOM and p50/CPU control drift below 25%.

| Selector | Normal decoder p50 | Experimental p50 | Normal CPU/query | Experimental CPU/query | p50 baseline A/A drift |
|---|---:|---:|---:|---:|---:|
| stage14d | 916.225ms | **1022.784ms (+11.6%)** | 1694.628ms | **1876.203ms (+10.7%)** | **0.109%** |
| project14d | 1795.351ms | 1774.902ms | 3080.300ms | 3054.194ms | 4.35% |
| regex14d | 1956.876ms | 1919.190ms | 3417.883ms | 3353.601ms | 2.29% |
| whole1d | 1370.533ms | 1403.063ms | 2311.228ms | 2344.173ms | 2.77% |

Stage slowdown is well outside drift. Project/regex/whole changes are small relative to drift and not robust gains.
**Do not merge or compile the unguarded LC decoder fastpath again**: it skips the stock `checkIndexesAreLimited` and assumes dictionary uniqueness. [Source safety/cost audit](../ts-lab-r12/LC_FRESH_DICTIONARY_SAFETY_AND_COST_20261103.md).
The artifact's CPU `trace_log` categories/hot leaves are **empty** despite a complete processor profile; no direct call-site CPU or mutex conclusion is valid. Stage representative summed processor work is 1664→1666ms while median query CPU changes 1695→1876ms, indicating that this profile cannot explain the regression on its own.

Independent [R09 upstream SHA-diff audit](evidence/R09-upstream-source-audit-20261103.json):
at exact master `a79b6cefb2fafc49d04f357a7edc512e25b93753` the TimeSeries selector and sink source files have identical Git blobs to pinned `52529f0c7`. Upstream changed LowCardinality bounds validation for the **global dictionary** branch and exposed a reusable static bounds checker, **not** a fix for the experimental **no-global** branch. No latest-master performance claim or C++ rebuild.

R12 is closed as **REJECT**. Preserve the separately pending jemalloc binary build [#41607779848](https://github.com/moleus/ClickHouse/actions/runs/41607779848); it is not a replacement for the unsafe LC prototype.


## 2026-11-03: R14 step-grid AVG sufficient-statistics hypothesis — mathematical/CI PASS, native unmeasured

[Canonical R14 root](claims/roots/R14.json), [claim](claims/R14-exact-step-grid-avg-rollup-v1.json), [source/algebra/cost and safety audit](experiments/R14_EXACT_STEP_GRID_ROLLUP_FEASIBILITY_20261103.md), [Python model](experiments/r14_rollup_oracle.py), [tests](experiments/test_r14_rollup_oracle.py), [handoff](handoffs/20261103-r14-rollup-math-06.json). [Nine tests in CI #41622064271](https://github.com/moleus/ClickHouse/actions/runs/41622064271) **SUCCESS**, decoded logs checked.

Hypothesis: for predeclared grid-aligned finite-float PromQL \`avg by(stage)\` of supported per-series selectors, independently compute each series scalar first and persist revisioned \`(sum,count)\` grouped by retained filter labels. Under **hypothetical** 95k/day, 38 stages, 10 jobs, branch=All and 14 days, **1.33m individual series evaluations vs at most 5320 (stage,job,time) states** for eligible repeated requests: 250x fewer logical elements, **NOT 250x wall**. \`avg_over_time\` must average each series before group, not flatten samples. Amdahl with affected fraction 90% gives optimistic ~9.65x ceiling; group NDV/repetition/freshness not measured. R11 showed generic QueryResultCache can be stale after INSERT. Source-pinned \`applyOneArgumentAggregationOperator.cpp\` **already uses \`avgForEach(values)\` and GROUP BY**: do not claim a new in-query AVG implementation. R14 only targets verified reuse of precomputed states. No native performance claim.

## 2026-11-03: R15 default monthly vs candidate UTC-day SAMPLES partitions — mathematical/CI PASS, native unmeasured

[Canonical R15 root](claims/roots/R15.json), [claim](claims/R15-samples-calendar-partition-pruning-v1.json), [source/math feasibility](experiments/R15_CALENDAR_PARTITION_FEASIBILITY_20261103.md), [Python model](experiments/r15_partition_oracle.py), [tests](experiments/test_r15_partition_oracle.py), [handoff](handoffs/20261103-r15-day-partition-07.json). [Four tests in CI #41622964101](https://github.com/moleus/ClickHouse/actions/runs/41622964101) **SUCCESS**, decoded logs and 14-day deterministic output checked.

Pinned TimeSeries v11 SAMPLES defaults to \`PARTITION BY toYYYYMM(toDateTime(bucket,'UTC')) ORDER BY(id,bucket)\`, with override for \`samples_partition_by\`. Independent uniformly hashed one-sample/ID model, 95k/day ×14 and 256 rows/mark: a 1d time predicate hits **5196/5196** monthly merged-part marks, while an ideal UTC-day-partitioned layout needs **372** positive marks (13.9677x ratio of modeled SAMPLES granules). For 14d: monthly **5196** vs daily **5208**, **no advantage**. This is not actual ClickHouse \`SelectedMarks\` or wall latency. Validate that the generated bucket predicate prunes the daily partition with \`EXPLAIN indexes=1\`, **turn off recent-samples TTL on both controls**, and measure native parity and partition insert/merge cost before a recommendation. No C++ build or scheduled workflow.

## 2026-11-03 R02 fresh-process write and merge whole CPU — valid short gate, replication pending

[Immutable 12-batch ABBA evidence](evidence/R02-small-batch-raw-lc-write-v1/41623694970.json) / [CI 41623694970](https://github.com/moleus/ClickHouse/actions/runs/41623694970): stock-O1 binary SHA `f0a2fa1...` from 41596421285, 4 fresh-process phases LC→RAW→RAW→LC, exact repeated fixture SHA `916d72ce...`, 12x4000-row small inserts per phase = 48000 physical sample points and 10000 logical series. Correct source-matched v11 schemas, native 30m avg-by(scope) numeric parity within 1e-8, all three inner tables merged to one active part. **Critical attribution repair:** `subprocess.Popen` PID is the ClickHouse watchdog; `/proc/PID/stat` of that parent returned zero under work. Valid instrumentation follows `/proc/PID/task/PID/children` and sums watchdog + worker CPU and IO, with exactly two PIDs per phase.

| Controlled 12-batch measure | LC | RAW | RAW/LC |
|---|---:|---:|---:|
| Process CPU, 12 inserts; merge stopped | 0.240s | 0.225s | 0.9375 |
| Insert wall time | 0.86943s | 0.85726s | 0.9860 |
| Process CPU, start merges + 3 explicit final OPTIMIZE | 0.110s | 0.100s | 0.9091 |
| Merge wall time | 0.07705s | 0.0710s | 0.9215 |
| `/proc` write_bytes during INSERT | 3688448 | 3559424 | 0.965 |
| Final compressed TAGS+SAMPLES+TimeRanges bytes | 939088 | 877576 | 0.9345 |

LC A/A CPU drift 0, wall drift 2.2% for inserts and 3.9% for merges. Nevertheless CPU counters have **10ms tick resolution**, while mode differences are only 10–15ms, two phases per mode, one runner. **Decision: `INCONCLUSIVE_REPLICATION_NEEDED`**, not a production speedup. 12-batch footprint (-6.55% RAW) remains verified. The earlier [CI 41623149595](https://github.com/moleus/ClickHouse/actions/runs/41623149595) never ran the test because of malformed artifact path ([failure evidence](evidence/R02-small-batch-raw-lc-write-v1/41623149595.json)); [CI 41623203578](https://github.com/moleus/ClickHouse/actions/runs/41623203578) had valid four-phase data but **INVALID CPU** because only the watchdog PID was counted and exact float equality incorrectly rejected 1e-13 variations ([negative evidence](evidence/R02-small-batch-raw-lc-write-v1/41623203578.json)). Never merge their invalid CPU numbers.

**Source-based scaling hypothesis:** LC ID index storage has approximately (16D+wN) bytes per physical part versus RAW (16N), where D is distinct UUIDs, N physical ID references and w encoded dictionary-index width. At high D/N near 1, LC may provide little benefit; this fixture has 10000 distinct IDs and only 10000 bucketed SAMPLES rows after merge. The next isolated [96-batch fresh-process ABBA 41624336193](https://github.com/moleus/ClickHouse/actions/runs/41624336193) uses same source and stock O1, 96 batches/phase = 384000 sample points, full 4-hour native oracle, process CPU/IO and lean reports-only artifact. **No result accepted until artifact correctness + drift gates pass.** Simultaneous reads/background merges and jemalloc optimized builds remain outside this controlled write-first test. No master/production/schedules touched.

## 2026-11-03 R02: replicated write/merge CPU benefit of RAW UUID across 3 independent runners

[Immutable three-runner evidence](evidence/R02-small-batch-raw-lc-write-v1/41625403146.json) / [strict reducer CI #41625403146](https://github.com/moleus/ClickHouse/actions/runs/41625403146) **SUCCESS, decision `REPLICATED_LAB_EFFECT` for this fixed stock-O1 lab fixture**, NOT an all-workload production recommendation.

Evidence chain: same source SHA `52529f0c...`, same stock-O1 binary SHA `f0a2fa1...`, same 96-batch native fixture SHA `dd2dd55ee...`, script SHA `aff76393...`, 4 fresh-server phases LC→RAW→RAW→LC per runner, 384000 physical sample points/phase, exactly 96 QueryFinish INSERT per phase, 3 explicit inner-table final merges, full 4h native PromQL independent oracle and 25%-max A/A CPU+wall drift. Runner #1 [41624336193](https://github.com/moleus/ClickHouse/actions/runs/41624336193), runners #2/3 [41624764688](https://github.com/moleus/ClickHouse/actions/runs/41624764688). Reducer source `.github/ts-lab/experiments/r02_reduce_cpu_replicates.py`; official artifact GitHub digests checked in reducer workflow; artifacts compact (no DB data directories).

| Measured ratio RAW/LC | Runner 1 | Runner 2 | Runner 3 | Within-runner ratio geomean |
|---|---:|---:|---:|---:|
| Process CPU of 96 inserts, merges stopped | 0.84269 | 0.88439 | 0.81070 | **0.84539 (-15.5%)** |
| Process CPU of START MERGES + 3 FINAL OPTIMIZE | 0.71182 | 0.77857 | 0.70526 | **0.73115 (-26.9%)** |
| Ingest wall | 0.91857 | 0.99914 | 0.89619 | 0.93694 (not consistently faster) |
| Merge wall | 0.76014 | 0.75797 | 0.73472 | 0.75085 (-24.9%) |
| Process write_bytes during ingest | 0.93888 | 0.94050 | 0.93266 | 0.93734 (-6.3%) |

Post-final-merge compressed TAGS+SAMPLES+TimeRanges: LC **6,238,086 bytes** vs RAW **6,026,894 bytes** (-3.4%); 66000 unique series, 68000 bucketed SAMPLES rows, 384000 real sample points, 96 chronological inserts (32 distinct TAGS parts before merge, 96 SAMPLES and TimeRanges parts). LC per-part dictionary overhead plausibly dominates when distinct IDs per part approach physical references. **Real write latency differs by runner; CPU reductions must not be paraphrased as a guaranteed latency reduction.** Absolute CPU seconds also differ greatly across hosts, so only within-host ratios are pooled. This is stock `-O1` glibc and controlled no-merge ingest followed by explicit merges. Next R02 gate is a separate **concurrent native reads + natural background merges** A/B/B/A; retain numeric parity and source SHA. Other agent's R03 read-only CH vs VM benchmark is not duplicated.

## 2026-11-03: R03 full native ClickHouse vs VictoriaMetrics 1/4/8 client comparison — 12/12 valid

[Immutable 12-case machine evidence](evidence/R03-ch-vm-k8s-concurrent-41624246411.json) /
[CI #41624246411](https://github.com/moleus/ClickHouse/actions/runs/41624246411) **SUCCESS**.
Cross-engine full nonempty PromQL label/timestamp/value parity on all 4 selectors (max abs value difference `2.50e-16`). Frozen R01 400-node, 3-day synthetic 15s/60s/300s source fixture SHA256 `ea9a8d43c9e58ed916f1bf43949556d8ed18169ddd14033c293af4cd54c9881d`: **28,896 series and 2,488,320 original points**. Same runner, same synthetic data, source-pinned stock ClickHouse LLVM22.1.8 **O1 + glibc** binary SHA256 `f0a2fa1f814c58853f43423705cac10ab9df3bfe5887e27ebe9620ba7cb9660e`, v11 TimeSeries with opt-in RAW_UUID, versus optimized VictoriaMetrics **v1.134.0 single-server** release. VM uses `-search.maxUniqueTimeseries=165000`, no query-response caching; VM index tag-filter cache may remain warm.

Each selector × 1/4/8 clients ran full **VM/CH/CH/VM A/B/B/A**, 2 warmup + **64 measured synchronous waves** per phase; per-request nonempty numeric parity, both-engine p50/QPS/schedstat-CPU control drift <=25%, no OOM, physical CH parts unchanged. CPU uses `/proc/PID/task/TID/schedstat` nanosecond on-CPU deltas; start/end RSS snapshots, **no high-overhead live RSS observer**. Validity covers all 12 comparisons.

| 8 concurrent clients | VM p50 | ClickHouse p50 | CH / VM | VM QPS | CH QPS | VM process CPU/query | CH process CPU/query |
|---|---:|---:|---:|---:|---:|---:|---:|
| node_broad | 115.963ms | 591.532ms | 5.10× | 61.40 | 12.46 | 62.04ms | 315.85ms |
| pod_namespace | 11.150ms | 201.954ms | 18.11× | 529.58 | 36.03 | 5.74ms | 107.87ms |
| pod_broad | 179.104ms | 603.252ms | 3.37× | 39.64 | 12.04 | 97.10ms | 328.47ms |
| burst15 | 4.051ms | 135.466ms | 33.44× | 1407.49 | 53.02 | 1.49ms | 72.58ms |

**Interpretation:** VM1.134.0 release is faster/lower process CPU on **all twelve specific matched fixture/config combinations**. It is **not valid** to call this a production-normalized architecture result: ClickHouse is stock **O1/glibc**, while VM is optimized production release; max_threads and allocator differ, memory caps are not identical strict cgroups, both engines shared a runner, VM internal index caches can be warm, aligned node starts are unrealistic and no 14d/1.33m workload was tested. See [protocol and limits](../ts-lab-r03/METHODOLOGY_AND_LIMITS_20261103.md) and [source-backed ID search/read-path contrast](../ts-lab-r03/READ_PATH_SOURCE_DIFFERENCES_20261103.md); do not attribute speedup to a specific index stage without profiles.

**Historical invalid first attempt** [#41623609320](https://github.com/moleus/ClickHouse/actions/runs/41623609320) is preserved as [censored evidence](evidence/R03-first-censored-41623609320.json): VM CPU clock granularity/control drift and high-overhead RSS observer caused failure after 10/12 pairs. Do not combine its timings with the corrected run. [Ledger M41](METHODOLOGY_AND_FAILURES.md).

**Decision:** `ACCEPT_DIAGNOSTIC`, **NO CH-over-VM production recommendation**. Next gate: identical CPU/RSS cgroup and production-optimized O2+jemalloc ClickHouse (preserve ongoing [jemalloc build #41607779848](https://github.com/moleus/ClickHouse/actions/runs/41607779848) without duplicate compilation), 14d/1.33m mixed workload, staggered node starts, VM 165k match cap and source-level TAGS/TimeRanges/SAMPLES CPU attribution. No changes to production, master or upstream.

## 2026-11-03 R15: real SAMPLES UTC-day partition pruning confirmed, but **not** a 14x native speedup

[Canonical root](claims/roots/R15.json), [full source/math/CI analysis](experiments/R15_CALENDAR_PARTITION_FEASIBILITY_20261103.md), [immutable native evidence](evidence/R15-native-calendar-41625086352.json), [raw GitHub artifact #41625086352](https://github.com/moleus/ClickHouse/actions/runs/41625086352), [handoff](handoffs/20261103-r15-day-partition-07.json). Stock O1 SHA \`f0a2fa1f814c58853f43423705cac10ab9df3bfe5887e27ebe9620ba7cb9660e\`; original source \`52529f0c7\`. Synthetic 70,000 distinct one-sample series in 14 days, identical RAW UUID and SAMPLES \`ORDER BY(id,bucket)\`, \`recent_samples_ttl_seconds=0\`, 3600s buckets; only SAMPLES monthly vs day PARTITION BY differs. Exact ID hash and native results matched.

**Decisive EXPLAIN and real QueryFinish:** direct SAMPLES 1d selects **273 monthly marks vs 20 daily**, reads **70,000 vs5,000 rows**, 10.006ms vs3.118ms CPU, and day MinMax prunes **13 of 14 partitions**. Direct 3d reads 273→60 marks; 7d 273→140; 14d **273→280** (day loses). This verifies physical pruning; prior hypothetical g=256 model was not the engine itself.

Full **144 native PromQL A/B/B/A** measured requests, 6 nonempty parity-checked selectors, all controls under 25% drift, no OOM. Broad 1d p50 **67.98→55.45ms (1.226x)**, CPU **118.02→83.44ms (1.414x)**, selected marks **291→38**; 3d p50 **87.54→74.50ms (1.175x)**; 7d **142.68→140.57ms (~neutral)**; 14d **243.62→240.86ms (~neutral)**. Exact stage1d p50 **40.08→34.25ms (1.170x)**; exact stage14d **57.49→59.42ms (3.4% slower)**. Broad1d native \`read_rows\` falls only **210,000→145,000**, consistent with unchanged TAGS+TimeRanges metadata work of 140k rows; the **next major bottleneck is metadata**, not SAMPLES physical date pruning. This is a workload-specific mechanism, **not a production/default change**.

Measured post-merge SAMPLES disk: month **2,289,798** vs day **1,422,855** bytes (−37.9%) and 1 vs14 parts; do not generalize to steady-state storage/merge or query throughput. One-pass ingest wall ~2.84s each is unpaired and does **not** prove equal writer CPU. The archive SHA256 \`58a3e3586c172c304687033e609d3a9bf1ac40b5484de3db90edd45e8534670e\` and report/BUILD_INFO were independently verified. Next gated question: determine a **time-aware tag/series metadata access path** with a read/write/space break-even, avoiding duplicate R02/R05/R07 work.

## 2026-11-03 R07 full-ID time-day covering metadata — source/math gate PASS; native speed UNMEASURED

[Canonical R07 root](claims/roots/R07.json), [claim](claims/R07-time-day-covering-metadata-v1.json), [read/write/semantic cost audit](experiments/R07_TIME_DAY_COVERED_METADATA_20261103.md), [pure Python oracle](experiments/r07_day_cover_oracle.py), [12 regression tests](experiments/test_r07_day_cover_oracle.py), [immutable source-proof](evidence/R07-source-day-cover-r01-41627103046.json), [handoff](handoffs/20261103-r07-day-cover-source-proof-09.json). [12/12 tests SUCCESS in CI #41627103046](https://github.com/moleus/ClickHouse/actions/runs/41627103046), decoded logs checked. Earlier [CI #41626607817](https://github.com/moleus/ClickHouse/actions/runs/41626607817) failed an incorrect continuous-time \`1.125\` assertion, corrected to **1.1243055556** for 60-second real sample boundaries; retain this as method-negative, not engine failure.

**Independent high-upside question:** R15 actual broad1d native reads still **140,000 metadata/other rows** after SAMPLES daily pruning. A hypothetical **extra** UTC-day-keyed covering sidecar of **original full stable ID + metric + allowed labels** could prune inactive-series metadata for short-lived high-churn metrics, but MUST keep source freshness/ACL, deduplicate ID across overlapping days before aggregation, and refuse long-lived series without bounded duplication. No C++ change or native read/writer speedup claim. On the source-pinned 400-slot/3d R01 generator, exact synthetic replay predicts **28,896** day-cover rows for aligned node starts (1.0×) and **32,424** for staggered node starts (**1.122093×**, 3,528 cross-midnight ID duplicates, day0..3 entries [9608,10808,10808,1200]). The R01 staggered last-day overflow is intentionally preserved.

At a conditional uniform 3h high-churn/14d model, reading two UTC-day covering partitions costs \`N*1.125*2/14\` metadata rows vs broad original TAGS+TimeRanges \`2N\` → **12.44× fewer metadata logical rows**, **NOT wall speedup**. The sidecar costs **~1.125N additional metadata writes** if original layers remain; 0.125N refers only to duplicate day entries beyond one per ID. A 30d stable series may need ~31 day copies: reject unless selective per-metric policy. **Next gate:** schema-only stock-O1 SQL sidecar, native PromQL independent oracle, late-write/revision correctness and write/merge/disk Pareto, then no C++ patch unless net benefit proven. R05 owns SAMPLES candidate-ID mark pruning, not this metadata sidecar.

## 2026-11-03: R17 native small-output-format CPU floor disproved JSON hypothesis

[Full 416-query stock O1 native ABBA #41628966700](https://github.com/moleus/ClickHouse/actions/runs/41628966700), [immutable result/CPU samples](evidence/R17-output-format-cpu-41628966700.json), [source-aware attribution](../ts-lab-r17/NATIVE_OUTPUT_CPU_ATTRIBUTION_20261103.md).
New canonical root R17, no duplicate R03/R05/R07. Same SHA-verified stock O1/RAW_UUID v11 ClickHouse binary `f0a2fa1f...` and exact R01 400-node 3d fixture `ea9a8d43...`. Four exact-native selectors, JSON/Null/Null/JSON, 2 warmup+24 measured requests/phase. All 416 requests, JSON nonempty full label/timestamp/numeric parity, zero-byte Null output, identical read rows/marks and before/after physical parts, A/A CPU/p50 drift below25%, no OOM.

| Selector | JSON CPU/query | Null CPU/query | JSON wall p50 | Null wall p50 | Output |
|---|---:|---:|---:|---:|---:|
| node_broad | 411.338ms | 440.113ms | 256.781ms | 277.783ms | 1530 B |
| pod_namespace | 94.337ms | 93.260ms | 67.257ms | 66.177ms | 191 B |
| pod_broad | 374.139ms | 376.256ms | 238.737ms | 241.139ms | 3057 B |
| burst15 | 67.899ms | 68.010ms | 51.443ms | 50.918ms | 767 B |

**REJECT JSON output optimization as a primary answer** to the earlier [R03 VM/CH diagnostic](evidence/R03-ch-vm-k8s-concurrent-41624246411.json). Null discards output but read rows/marks do not change; namespace saves just1.1ms CPU and burst no CPU. Null is actually slower on broad nodes, so changing FORMAT is **not a pure serialization microbenchmark** and never a production output recommendation. Representative JSON CPU traces show `read_decode` 45/90 on namespace and29/60 on burst; broad node `grid_aggregate`128/413 and `expression`121/413, broad pod `grid`131/408 and `tag_registration`107/408. These are *one-query statistical samples*, NOT median or critical-path fractions; processor work sums overlap threads. Primary next target is physical read/decode or grid/tag materialization **after source-level cost bound and a distinct root claim**, not repeat UUID or TimeRanges/mark-index work. Lab remains O1/glibc, no production equivalence.

