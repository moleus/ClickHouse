# TimeSeries performance lab — index

## R44 / E05 — inline singleton TimeSeries aggregate state (2026-11-04; REJECT REGRESSIONS)

- [R44 full native 1.33m/14d sparse and 8192×256 dense results](evidence/R44-native-1330k-dense-local-20261104.json), [source proof, regression and limits](../ts-lab-r44/README.md), [pinned C++ state helper](../ts-lab-r44/apply_inline_single.py), [O2 build 41805775727](https://github.com/moleus/ClickHouse/actions/runs/41805775727), [160 native output/errors/plans PASS 41807197978](https://github.com/moleus/ClickHouse/actions/runs/41807197978). R44 adds a singleton sample alongside the R43 2-cell map and spills on second input. On matched R43/R44/R44/R43 1.33m/14d: whole tracked memory 839.531→838.654MB (~0.1%), but project tracked memory **189.404→206.181MB (+8.86%)**. Sparse wall differences smaller than control drift or high drift (stage baseline drift 51%). Dense native rate over 8192×256: **CPU 85.55→113.70ms (+32.91%)**, reference CPU drift13.41%, full 532-point analytic oracle PASS. **REJECT unconditional R44**, no production. R45 compact state must be validated separately.

## R43 / E05 — cumulative R40 plus R34 sparse bucket grower (2026-11-04; MEMORY PASS, STAGE REGRESSION)

- [Immutable matched 4-query native A/B/B/A](evidence/R43-native-1330k-local-20261104.json), [source/measurement report](../ts-lab-r43/README.md), [160 native semantic/plan PASS 41805684591](https://github.com/moleus/ClickHouse/actions/runs/41805684591), [O2/jemalloc build 41804461799](https://github.com/moleus/ClickHouse/actions/runs/41804461799). R43 retains R35/R37/R38 cumulative compiler and adds only R34 two-cell TimeSeriesBucketsHashTableGrower. Same frozen 1.33m/14d RAW_UUID parts and full independent oracle: wide `by` and `without` query memory 870.31→839.53MB (**−3.54%**), stage-only 72.30→53.51MB (**−26.0%**), project-only 282.66→189.40MB (**−33.0%**). Whole wall observed −7.14% but control drift 15.49%: inconclusive. **Stage wall worsened 6.97% with only 0.51% reference drift**; project wall also inconclusive due 15% reference drift. **SPLIT DECISION: memory benefit verified; unconditional performance promotion REJECTED**. Do not multiply with standalone R34 or claim production capacity.


## R42 / E05 — direct C++ root tag serialization (2026-11-04; NO_PROVEN_GAIN)

- [Full 1.33m/14d native R41/R42/R42/R41 evidence](evidence/R42-native-root-35k-local-20261104.json), [source proof and reproducibility](../ts-lab-r42/README.md), [source-pinned C++ serializer](../ts-lab-r42/apply_direct_root_tags.py), [O2/jemalloc build 41801868345](https://github.com/moleus/ClickHouse/actions/runs/41801868345), [160 native semantic/plan PASS 41803649288](https://github.com/moleus/ClickHouse/actions/runs/41803649288). R42 replaces R41's `arrayFilter(timeSeriesGroupToTags(group))` with a direct C++ tag output function. Same 35k full independent-oracle points, 3,989,152 read rows/337,293,196 bytes, physical parts and 4CPU/4GiB/2.85GB envelope. R41 wall638→R42 642.5ms (**+0.7%**, R41 drift1.25%); CPU568.15→575.68ms (**+1.33%**, drift2.70%); query memory 72.239→72.220MB negligible; observed RSS431→411MB (−4.7%) within 7.17% RSS drift. **NO_PROVEN_GAIN / DO_NOT_PROMOTE.** No master/upstream/production change.

## R41 / E05 — root exact-metric late projection (2026-11-04; NATIVE PILOT PASS, RSS REGRESSION)

- [R41 measured full native 35k-point ABBA evidence](evidence/R41-native-root-35k-local-20261104.json), [source proof and reproduction](../ts-lab-r41/README.md), [optimized pinned LLVM22.1.8 O2/jemalloc build 41799488038](https://github.com/moleus/ClickHouse/actions/runs/41799488038), [160 native output/plan guards PASS 41800693590](https://github.com/moleus/ClickHouse/actions/runs/41800693590). On frozen 1.33m/14d RAW_UUID source and root `avg_over_time(ci_duration{stage="stage-1"}[1d])` returning 35,000 independent-oracle verified output points, four fresh-server R40/R41/R41/R40 phases show wall795.25→644ms (**observed −19.0%**), query CPU712.28→577.93ms (**−18.9%**), tracked memory75.62→72.24MB (**−4.47%**); R40 control wall drift11.76%. Sampled request peak RSS **regressed** 406→440MB (+8.3%); do not promote as production or general gain until repeated with low drift and RSS issue resolved. Exact physical parts, 3,989,152 read rows, 337,293,196 read bytes, every output equal. All R36/R39 exclusions preserved.

## R40 / E05 — cumulative native compiler R35 + R37 + R38 (2026-11-04; NATIVE SEMANTIC PASS)

- [R40 mechanism and limits](../ts-lab-r40/README.md), [source-pinned integration helper](../ts-lab-r40/apply_cumulative.py), [R40 root](claims/roots/R40.json), [optimized LLVM22.1.8 O2/jemalloc build 41797918745](https://github.com/moleus/ClickHouse/actions/runs/41797918745), SHA256 `522bbe374eb73727142bcd6239a7beb62a983c2f5a29a7f8b3456446c5536653`. Changes only `applyFunctionOverRange.cpp`: R35 by-parent deferral, R38 without-parent deferral, and R37 fallback injective projection for root/other parents. [155-case native differential output and physical-plan PASS 41798916172](https://github.com/moleus/ClickHouse/actions/runs/41798916172) against R35, R37 and R38 on the same fixture. Local 1.33m/14d whole `by(stage)` and `without(...)` returned all 532 independent-oracle points exactly. The initial CI harness failed because the plan list lacked `wide_without`; corrected, no C++ changes. **Integration verified; no new cross-shape cumulative speedup claimed**. Excludes R36 (narrow regression), R39 (inconclusive), and R34 (unpaired). Do not infer p95/p99, VM parity or production readiness.



## R39 / E05: bounded without-tag interning (2026-11-04; INCONCLUSIVE)

- [Measured native 1.33m/14d A/B/B/A evidence](evidence/R39-native-1330k-local-20261104.json), [source proof and next diagnostic](../ts-lab-r39/README.md), [source-pinned C++ patch](../ts-lab-r39/apply_without_intern.py), [O2/jemalloc build 41792846773](https://github.com/moleus/ClickHouse/actions/runs/41792846773), [157-case native oracle PASS 41793653918](https://github.com/moleus/ClickHouse/actions/runs/41793653918). R39 hashes retained sorted tag pairs before allocating projected objects for N>=16384, collision checks exact pairs, capped D<=min(4096,N/8) and pre-registration fallback. Native R38/R39/R39/R38 on frozen 1.33m/14d: wall6966.25→6750.75ms (**observed −3.09%**) with R38 wall drift2.69%; CPU6871.77→6653.95ms (**observed −3.17%**) with control drift2.65%; tracked query memory ~870.315MB unchanged. All 532 points and read rows/bytes match. **INCONCLUSIVE, DO NOT PROMOTE**. Diagnose actual removeTags block sizes and branch activation with low observer overhead before another expensive compilation. No R36/R37 integration.

## R38 / E05: native fixed-metric name deferral through without (2026-11-04; VALIDATED RESEARCH CANDIDATE)

- [Two-client native memory-capacity evidence](evidence/R38-two-client-capacity-local-20261104.json): fixed native 1.33m/14d whole `without` query, 4CPU/4GiB cgroup/2.85GB ClickHouse server cap, 2 client requests at a time, four fresh R35/R38/R38/R35 server phases with 4 waves per phase. R35 **6/16 successful, 10/16 ClickHouse error241** (all total-memory-limit OvercommitTracker, not kernel OOM), R38 **16/16 successful, 0/16 errors**. Excluding warmups: R35 2/8 versus R38 8/8. Every success complete 532-point oracle; fixed physical data. This is finite capacity evidence, not universal concurrency or mixed read/write readiness.

- [Immutable 1.33m native A/B/B/A](evidence/R38-native-1330k-local-20261104.json), [mechanism and reproduction](../ts-lab-r38/README.md), [one-guard C++ patch](../ts-lab-r38/apply_without_deferral.py), [O2/jemalloc build 41791235811](https://github.com/moleus/ClickHouse/actions/runs/41791235811), [155 native semantic/plan PASS 41791869622](https://github.com/moleus/ClickHouse/actions/runs/41791869622). R38 allows R35's exact-metric injectivity proof to defer name removal through immediate `without(...)`; the parent removes `__name__` itself, avoiding any intermediate projected groups or duplicate-check aggregate. In native `avg without(build,project,branch,job)(avg_over_time(ci_duration[1d]))` on 1.33m/14d RAW_UUID: R35 wall11615ms→R38 7056.5ms (**−39.25%**), CPU11498.10→6952.35ms (**−39.53%**), query memory1.416GB→0.870GB (**−38.55%**), sampled request peak RSS2.049GB→1.432GB (−30.14%, not a guaranteed max). R35/R38/R38/R35 fresh-server ABBA: two warmups+four measured per phase, R35 wall control drift1.91%, all 532 points independent-oracle equal; same3.99m read rows/336024252 bytes, unchanged physical parts,4CPU/4GiB cgroup,2.85GB server cap. **ACCEPT_FOR_NEXT_GATE only; no production or VM parity claim**, no R36 combination. [Separate direct R37/R38/R38/R37 native ABBA](evidence/R38-vs-R37-direct-native-1330k-local-20261104.json) confirms R38 reduces wall 8609.5→7221.75ms (**−16.12%**), CPU8495.41→7118.38ms (**−16.21%**), tracked memory1.3668GB→0.8703GB (**−36.32%**); control wall drift4.55%, all 532 oracle points and physical reads exact.

## R37 / E05: native injective metric-name projection (2026-11-04; VALIDATED RESEARCH CANDIDATE)

- [Immutable 1.33m/14d native A/B/B/A result](evidence/R37-native-1330k-local-20261104.json), [source proof and reproduction](../ts-lab-r37/README.md), [source-pinned C++ compiler patch](../ts-lab-r37/apply_injective_projection.py), [O2/jemalloc build 41789987661](https://github.com/moleus/ClickHouse/actions/runs/41789987661), [155 native compatibility and plan PASS 41790880980](https://github.com/moleus/ClickHouse/actions/runs/41790880980). R37 avoids duplicate-check GROUP BY on single exact metric-name range functions when R35 cannot defer metric drop. In the exact native `avg without(build,project,branch,job)(avg_over_time(ci_duration[1d]))` 1.33m/14d query: R35 wall 12088ms vs R37 8123.75ms (**−32.8%**), CPU 11976.64 vs 8026.72ms (**−33.0%**), tracked query memory 1.4164 vs 1.3668GB (**−3.5%**). Four fresh-process R35/R37/R37/R35 phases (2 warmups+4 measured per phase), 4CPU/4GiB quota, 2.85GB server cap, 532 independent-oracle output points and identical 3.99m inner read rows/336024252 bytes. R35 wall control drift 6.78%; candidate scope **research only**, not production, VM parity or p95/p99. No R36 integration.

## R36 / E05: native one-label projection (2026-11-04; SPLIT DECISION)

- [Measured 1.33m/14d native evidence](evidence/R36-native-1330k-local-20261104.json), [source and reproduction](../ts-lab-r36/README.md), [source-pinned patch](../ts-lab-r36/apply_single_label_intern.py), [O2/jemalloc build 41787693315](https://github.com/moleus/ClickHouse/actions/runs/41787693315). Same exact physical parts and full independent oracle; local 4CPU/4GiB fresh-process R35/R36/R36/R35, 2 warmups+4 measured per phase. Whole-metric wall 6743→6141ms (−8.9%), CPU 6650→6049ms (−9.0%), memory ~870.31MB unchanged; 3.53% wall control drift. **ACCEPT_FOR_NEXT_GATE only for the broad selector**.
- Critical negative control: `stage=stage-1` wall 541→592ms (**+9.4% regression**, baseline drift1.48%), CPU +9.4%. Project observed −7.9% wall but stock drift10.7%: INCONCLUSIVE. The [133k CI pilot 41789371186](https://github.com/moleus/ClickHouse/actions/runs/41789371186) passed 155-case native compatibility and outputs but failed timing drift gate. **REJECT unconditional R36 promotion**; investigate bounded adaptive dispatch, never multiply R35/R36 or R34 gains without a separate matched build.


## R35 / E05: completed native redundant-aggregation elimination (2026-11-04)

- [Result and reproduction](../ts-lab-r35/README.md), [source proof](../ts-lab-r35/SOURCE_PROOF.md), [isolated C++ patch](../ts-lab-r35/apply_metric_drop.py), [optimized candidate build41732238195](https://github.com/moleus/ClickHouse/actions/runs/41732238195). Same pinned source/LLVM22.1.8/O2/jemalloc; no R32/R34/JOIN patch. Native AST proof defers metric-name removal to an immediate safe aggregation for a single exact metric, eliminating one full-series duplicate-check aggregation and copied labels. Positive plan3->2 Aggregating nodes; negative guards unchanged;147 native cases match stock, including duplicate-series error preservation.
- [Full native1.33m/14d local A/B/B/A evidence](evidence/R35-native-1330k-local-20261104.json): whole-metric wall11974->7777ms (-35.0%), CPU11862->7644ms (-35.6%), query memory1.416->0.870GB (-38.6%). Same3.99m inner-path read rows and336024252 read bytes;532 exact output points. Whole control drifts0.63% stock/1.13% candidate. Dense native rate control179.75->148.5ms with analytic oracle. Selective latency effects require replication because of drift. **ACCEPT_FOR_NEXT_GATE, not production/VM superiority or tail-latency proof.**
- [Two-client native memory-cap test](evidence/R35-two-client-capacity-local-20261104.json): same1.33m data,4-CPU/4GiB container,2.85GB server cap. Stock completes6/12 requests with6 total-memory-limit errors; candidate12/12, all successful outputs exact. Excluding warmups: stock4/8 vs candidate8/8. Both stock controls reproduce one failure per wave. This is a bounded capacity result, not a production failure rate.
- [Separate R34 sparse-map result](evidence/R34-sparse-map-local-20261104.json): initial bucket-map16->2 cells cuts aggregate-only sparse memory165->53MB; full-native133k whole-query memory only292.53->281.34MB. Dense memory unchanged. Do not conflate kernel and full-query savings or multiply R34/R35 gains.
- [R32 finalizer-only negative result](evidence/R32-local-native-133k-20261104.json): no reliable native speedup; **NO_EFFECT / DO_NOT_PROMOTE**. The R32 binary passed compilation but that alone was not a performance result.
- Next gates: sustained open-loop mixed reads/writes, retention, ACL/tenant and distributed paths. Master/upstream/production unchanged. Original failed/censored local timing attempts are retained separately; final R35 runs completed all phases.


## R29 / E01: clean stock optimized O2 + jemalloc build (2026-11-03)

- [Canonical R29 root](claims/roots/R29.json), [claim](claims/R29-stock-o2-jemalloc-source-parity-v1.json), [E01 owner](epics/20261103-release-parity/E01-R29-stock-o2-jemalloc-owner-20261103.json). R29 owns only a **source-unchanged release-optimized baseline artifact**, not E02 benchmark results or upstream/master changes.
- [Pinned build workflow](../workflows/ts-lab-r29-stock-o2-jemalloc-20261103.yml), [CI #41685467438](https://github.com/moleus/ClickHouse/actions/runs/41685467438) **IN PROGRESS, NO COMPILED BINARY OR SPEED CLAIM YET**. Reuses passing stock O1 build recipe, exact upstream source 52529f0c711566d696917678bf15e7068772fdde and LLVM22.1.8 with changed Release -O2 and jemalloc ON; removes all historical C++ lab fastpath patches. An earlier [#41685290429](https://github.com/moleus/ClickHouse/actions/runs/41685290429) had **invalid generated YAML, no job/compile** due unsafe JavaScript literal replacement of shell regex $' in workflow text. Corrected using literal replacements and preflight before upload. Do not restart/cancel or duplicate this long build.
- E02/production-parity requires SHA-verified unmodified O2+jemalloc executable, compiler/cmake provenance, runtime jemalloc proof, full native oracle, matching CPU/RSS cgroups and steady read/write; a build alone cannot establish ClickHouse faster than VictoriaMetrics.

## R28: native workload query admission and 8-client memory safety (2026-11-03)

- [Native eight-client capacity experiment](../ts-lab-r28/bench_native_admission_capacity.py), [offline protocol tests](../ts-lab-r28/test_native_admission_capacity.py), [source/math model](../ts-lab-r28/ADMISSION_CAPACITY_MODEL_20261103.md), [self-triggered workflow](../workflows/ts-lab-r28-native-capacity-20261103.yml), [CI #41683302083](https://github.com/moleus/ClickHouse/actions/runs/41683302083) **PASS / ACCEPT_FOR_NEXT_GATE**, [immutable evidence](evidence/R28-native-query-slot-admission-v1/41683302083.json). Pinned stock O1 executable SHA256 f0a2fa1..., full 1.33m/14d RAW UUID Jenkins project selector, 266-point complete native oracle, 8 clients/max_threads=2, six fresh server processes with admission caps **8/2/4/4/2/8**. **k=8 fails OOM in both fresh phases** at peak process RSS ~4.68/4.72 GB; **k=2 and k=4 both complete all 2 warmup+8 measured waves, 128/128 measured exact results per cap**. Repeated cap2 mean p50 **2475ms**, p95 **4018ms**, completed QPS **2.0047**, CPU **1836ms/request**, max observed RSS **1.82GB**; cap4 p50 **2946ms**, p95 **4090ms**, QPS **1.9566**, CPU **2024ms/request**, max RSS **2.51GB**. Both control p50 drifts <=3.6%; RSS observer overhead ~0.1%; complete QueryFinish rows for successful full phases. **Safety/capacity benefit proven only for this O1/glibc synthetic workload, not VM superiority or production SLA.** Next gates: optimized+jemalloc+cgroup parity, open-loop arrivals/queue rejection, longer retention/ingest and mixed selectors.

- [Canonical R28 root](claims/roots/R28.json) and [claim](claims/R28-native-query-slot-admission-v1.json) reserved before CI. Existing stock-O1 eight-client Jenkins project14d runs failed the 4.47GiB process memory ceiling despite reducing per-query threads; R28 tests a **different intervention**, limiting simultaneously admitted native queries with existing `CREATE RESOURCE ... (QUERY)` and workload slots. This is independent of R03's CH-vs-VM 400-node baseline and R06/R07 planner/index ownership.
- [Isolated functional smoke source](../ts-lab-r28/admission_smoke.py), [workflow](../workflows/ts-lab-r28-admission-smoke-20261103.yml), [CI #41676439539](https://github.com/moleus/ClickHouse/actions/runs/41676439539) **PASS**, [immutable measured evidence](evidence/R28-native-query-slot-admission-v1/41676439539.json). Uses pinned unmodified LLVM22 O1 stock executable SHA256 `f0a2fa1f814c58853f43423705cac10ab9df3bfe5887e27ebe9620ba7cb9660e`. Eight simultaneous 200ms sleep queries: slots=8 wall 210.19/209.00ms; slots=2 wall 812.30/810.76ms. All 32 results exactly TSV `0`. A blocked request with admission timeout 100ms returned `QUERY_SLOT_ACQUISITION_TIMEOUT` in 103.87ms while holder was executing. **Functional admission proved, not native TimeSeries speed/memory improvement.** Reduced slots deliberately increase waiting time for these no-CPU queries.
- [Handoff](handoffs/20261103-r28-admission-01.json): after smoke passes, a separate 1.33m/14d RAW UUID **native full PromQL** 8-client fresh-process A/B/B/A must compare unlimited/2/4 slots, preserve exact output, record arrival-to-response p95/p99 (including queue), completed QPS, server peak RSS, CPU, errors and A/A drift. Reject if OOM persists or queueing harms the throughput/tail budget. Query slots do not themselves reserve a safe amount of memory.

**Single write branch:** `lab/ts-cbo-adaptive-20261103`. All agents should commit their research here. Keep one append-only history; do not reset, amend, rebase, or force-push.

## E03 / R31: TimeRanges IN-set semi-filter on native-generated SQL (2026-11-04)

- [R31 1.33m/14d large-set counterexample](../ts-lab-r31/LARGE_CARDINALITY_CROSSOVER_20261104.md), [immutable evidence](evidence/R31-1330k-in-memory-41721273167.json), [CI #41721273167](https://github.com/moleus/ClickHouse/actions/runs/41721273167) **PASS diagnostic SQL, REJECT global IN**. One frozen part each on 1.33m TimeSeries rows; project14d ALL/ANY/IN wall 794.75/745.25/**1376**ms, stage14d 366.25/347.5/**998**ms, project1d 329.5/**306.25**/319.5ms. IN is 2.87x slower vs ANY on stage14d and requires ~158MB vs 113.6MB query memory. For project1d IN uses 29.84MB vs ANY 110.98MB but is ~4.3% slower. Exact NDV + two independent ID fingerprints and first64 full SAMPLES equal; not full native patched C++. **IN set build has a distinct memory/cost crossover, not a universal win.** [O2 stock SQL rerun](../workflows/ts-lab-r31-o2-jemalloc-20261104.yml) and [R30 matched O2 ANY one-site build](../workflows/ts-lab-r30-any-o2-jemalloc-build-20261104.yml) are separate isolated jobs, no master/production changes.

- [Canonical R31 root](claims/roots/R31.json), [claim](claims/R31-timeranges-membership-in-semijoin-v1.json), [source proof and test](../ts-lab-r31/IN_MEMBERSHIP_SOURCE_PROOF_20261104.md), [immutable artifact evidence](evidence/R31-inset-three-way-41719952453.json) / [CI #41719952453](https://github.com/moleus/ClickHouse/actions/runs/41719952453) **PASS**. On pinned O1 source and 2048 stable IDs with 1/16 unmerged TimeRanges rows each, 6/6 exact ID-set and SAMPLES SQL hashes equal across original ALL / R30 ANY / R31 WHERE id IN (same TimeRanges time+metric filters with original legacy-ID OR). Project k16 A/B/C/C/B/A: ALL 44ms wall/54.891ms CPU/13.35MB, ANY 30.75/40.947/11.74MB, **IN 20/29.538/8.61MB** (−54.5% wall, −46.2% CPU from ALL). At k1 wall 14/14/13ms. IN EXPLAIN shows **CreatingSets + TAGS PREWHERE id IN** rather than FullSortingMergeJoin. This is **SQL-stage only, not native patched C++, release O2 or 1.33m/14d**; IN has an unbounded prior set build memory risk until measured. R29 optimized stock build unchanged.

## E03 / R30 — TimeRanges membership JOIN multiplicity (2026-11-03)

- [R30 verified FINAL negative control](evidence/R30-any-sql-postmerge-41715251318.json), [CI #41715251318](https://github.com/moleus/ClickHouse/actions/runs/41715251318) PASS: only TimeRanges merged; 66560 physical rows/18 parts become 5120/1; 5120 unique IDs unchanged. Stage/project/whole ALL/ANY output multiplication **16x→1x** with exact ID-set and SAMPLES parity. Direct project SQL premerge wall84.25→55.5ms (-34.1%), CPU104.18→75.59ms (-27.4%); postmerge 23→20.5ms, effect small and not proven stable, tracked memory ANY slightly higher. Strong causal evidence for **duplicate-right-row reduction**, not a native C++ production speedup.
- [R30 five-level duplicate-density curve](../ts-lab-r30/bench_any_duplication_curve.py), [CI #41716983848](https://github.com/moleus/ClickHouse/actions/runs/41716983848) **PASS 5/5**, [immutable evidence](evidence/R30-any-duplication-curve-41716983848.json): 2048 stable IDs with exact TimeRanges multiplicity 1/2/4/8/16 per independent table; strict output, ID-set and SAMPLES parity, drift <25% across all A/B/B/A. ALL→ANY CPU ms: 6.361→6.163 / 7.728→7.193 / 9.884→8.236 / 14.564→10.841 / 23.637→16.855. **ANY query memory regresses slightly at k1/2/4/8**, drops about14.7% at k16. Within-level effects valid; absolute cross-level timings not paired. [First curve](evidence/R30-duplication-curve-invalid-41716512958.json) INVALID_METHOD_PARTIAL due unscoped STOP not freezing newly-created TimeRanges; corrected per-table STOP applied. No compiled native ANY binary or production claim; R29 O2 build not touched.

- [R30 verified premerge SQL gain](evidence/R30-any-sql-premerge-41714502297.json), [CI 41714502297](https://github.com/moleus/ClickHouse/actions/runs/41714502297): 4096 IDs x16 unmerged inserts, TimeRanges 66,560 physical rows /5120 IDs, exact ID-set and full SAMPLES array equality for stage/project/whole; `LEFT ALL` vs `LEFT ANY` yields 16× fewer JOIN rows. On same frozen source/binary, direct project ID SQL A/B/B/A: wall73.75→50.50ms (-31.5%), CPU94.259→70.044ms (-25.7%), query peak26.72→14.27MB (-46.6%), read rows/bytes unchanged, drift0.68%/1.98%. **SQL-stage proof only; NO C++ native candidate speedup or production parity.** [First alias error](evidence/R30-any-sql-invalid-method-41713474075.json) recorded as INVALID_METHOD.
- [R30 postmerge negative control](https://github.com/moleus/ClickHouse/actions/runs/41715251318) **PASS** for TimeRanges-only FINAL and exact 16x→1x output multiplicity collapse, matching SAMPLES/ID sets. E01 [R29 optimized stock O2 jemalloc](https://github.com/moleus/ClickHouse/actions/runs/41685467438) remains in progress; no R30 C++ build launched.

- [Root R30](claims/roots/R30.json), [pre-registered claim](claims/R30-time-ranges-any-semi-join-v1.json), [source proof and gates](../ts-lab-r30/ALL_VS_ANY_TIME_RANGES_PROOF_20261103.md). Source-pinned TimeRanges `AggregatingMergeTree` writes one range per INSERT and merges by ID in background, but native selector uses `LEFT ALL JOIN` then `id IN` membership. Hypothesis: on unmerged duplicate ranges, `ANY LEFT JOIN` avoids multiplicative repeated ID outputs without changing the *set* of matching IDs. This is **not yet full native parity or performance proof**.
- [Read-only SQL A/B/B/A probe](../ts-lab-r30/bench_timeranges_any_sql.py) with initial [INVALID_METHOD CI #41713474075](https://github.com/moleus/ClickHouse/actions/runs/41713474075) and verified [PASS CI #41714502297](https://github.com/moleus/ClickHouse/actions/runs/41714502297), same pinned unchanged O1 binary and 4096 IDs × 16 writes; verify physical repeats, both distinct ID sets and SAMPLES arrays, real JOIN algorithm, paired CPU/wall/RSS. [One-site opt-in C++ patch helper](../ts-lab-r30/apply_any_join_cpp.py) is **dry-run only**; no new compiled binary while [E01 R29 O2 jemalloc](https://github.com/moleus/ClickHouse/actions/runs/41685467438) is in progress. R05/R07/R28 owners unaffected.

## E03 / R06 native order-aware planner source gate (2026-11-03)

- [Artifact-based factorial reduction](../ts-lab-r06/HISTORICAL_FACTORIAL_REDUCTION_20261103.md): restored BOTH original 41589798782 ZIPs, verified fixture/run, re-computed ~16.6–16.8% fewer rows yet +32.7–36.2% merge wall, factorial interaction +142/+357ms, and identified that four top-level EXPLAINs are identical. [Strict reducer and tests](../ts-lab-r06/reduce_native_factorial.py); no performance gain inferred.
- [Source-pinned diagnostic nested plan probe](../workflows/ts-lab-r06-nested-plan-20261103.yml): [first INVALID_METHOD 41670929674](evidence/R06-nested-plan-invalid-method-41670929674.json) recorded, [fixed 41671546950](https://github.com/moleus/ClickHouse/actions/runs/41671546950) PASS for eight generated SQL/PLAN/PIPELINE captures and native result parity on a separate 8192-ID fixture. Inner `id IN (SELECT TAGS LEFT JOIN TimeRanges)` still hidden in outer EXPLAIN; new [ID-subquery extractor](../ts-lab-r06/probe_nested_selector_plan.py) has separate parser tests. Third isolated plan-only run [41672454144](https://github.com/moleus/ClickHouse/actions/runs/41672454144) is a diagnostic test, not a release benchmark.

- [Source-matched nested ID JOIN capture](../ts-lab-r06/NATIVE_ID_PLAN_CAPTURE_20261103.md): [CI 41673047398](https://github.com/moleus/ClickHouse/actions/runs/41673047398) PASS, `FullSortingMergeJoin` vs `SpillingHashJoin(HashJoin)` physically verified on standalone generated SQL with native-effective settings, 8/8 exact native outputs. Small `time_by_max` projection was considered but not selected (1 mark). Prior [41672454144](https://github.com/moleus/ClickHouse/actions/runs/41672454144) omitted forced merge in standalone EXPLAIN and **must not** be used for join algorithm attribution.
- [Native CBO cardinality estimation failure](../ts-lab-r06/CARDINALITY_ESTIMATE_AUDIT_20261103.md), [immutable evidence](evidence/R06-native-join-cardinality-41673788133.json): [CI 41673788133](https://github.com/moleus/ClickHouse/actions/runs/41673788133) PASS, same 8192-ID synthetic fixture. JOIN estimates left `1.35k` vs true `216` stage/`410` project; right `892` vs true `8192` TimeRanges IDs. A factor-9.18 underestimation of right build rows makes estimate-only hash memory admission unsafe on this fixture; not a calibrated global ratio or CPU speedup.

- [R06 canonical claim](claims/roots/R06.json), [specific claim](claims/R06-native-order-aware-plan-cost-v1.json), and [source-level decision audit](epics/20261103-release-parity/E03-R06-order-aware-native-planner-source-audit-20261103.md). Source `52529f0c711566d696917678bf15e7068772fdde`: selector forces ordered merge and in-order read; physical projection can break join order, and `timeSeriesStoreTags` has per-query side effects. Historical native factorial 41589798782 shows fewer rows but more sorting and worse p50 in stage/project. R06 has **new small-fixture diagnostic CI, no new optimized binary and no gain claim**; next gates are 1.33m/14d and held-out cardinality validation, upper-bounded memory admission and an E06 oracle before any opt-in C++ change. R05/R07 ownership is unchanged.

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


## 2026-11-03 R07 matched native/SQL factor tests: **1.55x no-index 14d**, **1.48–1.72x sidecar-only on narrow/3d**, global rewrites rejected

[All three paired ABBA source and decision note](experiments/R07_NATIVE_VS_SQL_FACTORIAL_20261103.md), source-pinned O1 \`52529f0c7115\`, binary SHA \`f0a2fa1f814c58853f43423705cac10ab9df3bfe5887e27ebe9620ba7cb9660e\`, same 70k-series 14d one-sample-per-ID synthetic fixture. Each of 3 CI runs used one runner, **four cases x A/B/B/A x 2 warmup+4 measured**, full nonempty native PromQL label/time/Float64 parity, <25% control drift, no C++ build. Runs are on **different hosted runners; do not pool absolute timings or multiply ratios between runs**.

1. **Native vs SQL with sidecar**: [#41628618648](https://github.com/moleus/ClickHouse/actions/runs/41628618648) PASS, [immutable evidence](evidence/R07-sql-sidecar-abba-41628618648.json): SQL p50 vs native 1d broad **neutral**, 1d stage **1.73x faster**, 3d broad **2.34x faster**, 14d broad **2.97x faster**. This changes both SQL plan and metadata source and **does not isolate index gain**. First ABBA [#41628463197](https://github.com/moleus/ClickHouse/actions/runs/41628463197) failed only a test-script missing \`import os\`; fixed and negative retained.
2. **Identical SQL, original TAGS vs extra day-cover metadata**: [#41629085028](https://github.com/moleus/ClickHouse/actions/runs/41629085028) PASS, [immutable evidence](evidence/R07-tags-vs-cover-sql-41629085028.json): sidecar stage1d **1.48x faster**, broad3d **1.72x faster**, broad1d **19% slower** even with read_rows **150k→30k**, broad14d **~4% slower / inconclusive** with 18.7% A/A drift. Sidecar adds **70k rows, 14 day parts, 1.55MB compressed** to existing TAGS/TimeRanges/SAMPLES and introduces correctness/freshness/write challenges. **Do not recommend globally**.
3. **Native vs SQL using ONLY original TAGS/SAMPLES (no sidecar at all)**: [#41629492828](https://github.com/moleus/ClickHouse/actions/runs/41629492828) PASS, [immutable evidence](evidence/R07-native-vs-tags-no-cover-41629492828.json): broad14d p50 **395.89→254.87ms = 1.55x faster**, CPU **786.50→405.29ms (−48.5%)**, query peak tracker **101.27→38.38MB (−62.1%)**, read_bytes **19.6→8.33MB**, **same 210k read rows and 298 marks**. Broad1d original-TAGS SQL is **2.53x slower**, broad3d **1.81x slower**; stage1d SQL only **1.09x faster**. **REJECT global rewrite**, retain promising conditional broad14d gauge path without index write amplification.

**Key insight:** whole-query plan, label registration, projected columns and per-ID/grid aggregation can matter more than physical marks; ClickHouse already implements native \`avgForEach\`. SQL proves only this restricted finite-float one-sample/ID expression subset, not full PromQL staleness/NaN, multi-point, repeated same-timestamp samples, native histograms, ACL or concurrent-client behavior. **Next gate:** source-pinned R01 3d mixed 15/60/300s **multi-sample**, independent native parity/CPU operator attribution and range-dependent plan admission before any C++ planner prototype. Keep other owners' R02/R03/R05/R10/R12/R16/R17 lanes intact and don't restart long jemalloc build.

## 2026-11-03: R18 value subcolumn codec beats ALP on space but crosses over on CPU

[R18 actual full native A/B/B/A #41630585794](https://github.com/moleus/ClickHouse/actions/runs/41630585794),
[immutable 320-query CPU/physical/write evidence](evidence/R18-samples-value-codec-41630585794.json),
[source/cost audit](../ts-lab-r18/VALUE_CODEC_NATIVE_READ_WRITE_20261103.md).
Same stock O1/LLVM22 executable, same 400-slot 3d R01 source SHA, RAW_UUID in all
three inner targets, 3600s SAMPLES buckets, no recent samples. Change only
`samples.value` codec among `ALP,ZSTD(3)` (stock), `ZSTD(1)`, `LZ4`.
Actual `SHOW CREATE` codec, physical part identity, exact **28,896
series/2,488,320 points** and nonempty full native PromQL values verified.
All 8 independent codec ABBA controls valid, no OOM.

| On synthetic repetitive gauges | ALP+ZSTD3 | ZSTD1 | LZ4 |
|---|---:|---:|---:|
| Compressed SAMPLES bytes | 5,303,391 | **3,414,486 (−35.6%)** | 3,773,263 (−28.9%) |
| Total all three inner bytes | 7,424,806 | **5,535,901 (−25.4%)** | 5,894,678 (−20.6%) |
| pod_namespace native CPU/query | 36.886ms | **40.558ms (+10.0%)** | 38.910ms (independent baseline39.000ms) |
| node_broad native CPU/query | 115.136ms | 108.109ms (−6.1%) | 107.138ms (independent baseline108.827ms) |

The namespace ZSTD1 CPU regression is well outside A/A CPU drift **0.80%**
despite much fewer compressed bytes. Broad-node gain is modest relative to
CPU drift3.85%, pod-broad CPU difference within8.42% drift; LZ4 gains
not globally robust. A single unpaired ingest/merge shows slightly smaller
CPU and wall for ZSTD1/LZ4, **not** a valid steady-state writer A/B/B/A.
R17 uses a different AMD EPYC runner (9V74 vs R18 9V45); never compare
absolute R17/R18 latency/CPU across runners. **ACCEPT_NEXT_GATE_ONLY**:
repeat with a held-out realistic Float64 value distribution (high-entropy,
monotonic, counters/NaNs as relevant), natural write/merge and concurrent
readers before recommending any default codec change.

First [R18 #41630357537](https://github.com/moleus/ClickHouse/actions/runs/41630357537)
was an invalid pre-ingest benchmark gate (backtick/Delta(8) DDL formatting);
correction confirmed in successful rerun, retained as [M42](METHODOLOGY_AND_FAILURES.md).
One representative CPU trace per variant is diagnostic, not causal proof.

## 2026-11-03: R18 value-distribution holdout reverses the value-codec storage winner

[Two independent full native ABBA holdout CI jobs #41631747983](https://github.com/moleus/ClickHouse/actions/runs/41631747983),
[SHA-verified immutable result](evidence/R18-value-pattern-holdout-41631747983.json),
[heldout data and caveats](../ts-lab-r18/HELDOUT_VALUE_DISTRIBUTIONS_20261103.md).
R18 primary root remains owned by the existing agent; no duplicate root.
Same O1 ClickHouse binary and **same 400-node 3d metric names, tags,
timestamps, 28,896 series, 2,488,320 points, 86,688 SAMPLES rows and
340 marks**; only finite Float64 value distribution changes. Actual
`samples.value` ALP+ZSTD3 versus ZSTD1 verified, exact complete native
PromQL labels/times/float values, four selectors and A/B/B/A per
fixture, all eight comparisons PASS.

| Value distribution | ALP+ZSTD3 compressed SAMPLES | ZSTD1 compressed SAMPLES | ZSTD1 change |
|---|---:|---:|---:|
| Original repetitive [#41630585794](https://github.com/moleus/ClickHouse/actions/runs/41630585794) | 5,303,391 B | 3,414,486 B | **−35.6%** |
| Smooth deterministic | 4,718,192 B | 3,937,064 B | −16.6% |
| High-entropy Float64 | **19,686,139 B** | 20,216,284 B | **+2.7%** |

**Codecs have different storage winners for different value entropy.**
In high-entropy namespace query, ZSTD1 CPU **107.805→99.317ms (−7.9%)**
even as it occupies more bytes, CPU baseline drift1.60%; the original
repetitive namespace showed the **opposite sign**, CPU+10.0% on ZSTD1
with drift0.80%. Both jobs have exact same-native output parity.
Different heldout jobs are isolated runners (AMD EPYC 7763), and the
original fixture was run on EPYC 9V45: **never compare absolute CPU
across runs**. Within-run A/B/B/A only.

Scientific decision remains **ACCEPT_NEXT_GATE_ONLY, NO GLOBAL CODEC
PROMOTION**. First [R18 #41630357537](https://github.com/moleus/ClickHouse/actions/runs/41630357537)
was invalid DDL formatting guard, corrected and recorded as M42;
no C++ build, master, old-branch or production changes.


## 2026-11-03 R02: fixed open-loop native read/write + natural merges — replicated CPU and p95 benefit, stock-O1 only

[Immutable scientific reduction](evidence/R02-small-batch-raw-lc-write-v1/41634795775.json) / [SHA-checked reducer #41634795775](https://github.com/moleus/ClickHouse/actions/runs/41634795775) **PASS, REPLICATED_LAB_EFFECT**, with [two independent hosts #41634084470](https://github.com/moleus/ClickHouse/actions/runs/41634084470). Same pinned stock O1 + fixture SHA `dd2dd55ee8fc...`; 4 fresh processes per host (LC/RAW/RAW/LC), exactly **84 time-fixed INSERTs** every 200ms and **40 complete native PromQL requests** every 400ms per phase (offset100ms). Physical sample points=384000; all native labels/timestamps/float values checked; natural background merges ON. No request-count/QPS bias or schedule lateness >75ms; no optimizer or binary change.

| Within-host RAW/LC | Runner one | Runner two | Geometric mean |
|---|---:|---:|---:|
| Native p95 | 0.65677 | 0.68626 | **0.67135 (−32.9%)** |
| Whole-server process CPU during fixed offered load | 0.77509 | 0.80339 | **0.78911 (−21.1%)** |
| Total fixed schedule wall | 0.99948 | 0.99957 | 0.99952 (by design) |

The initial [fixed-arrival CI #41633619697](https://github.com/moleus/ClickHouse/actions/runs/41633619697) passed individual semantic/scheduling checks but failed **26.30% LC p95 A/A drift >25%**. It remains explicitly **INVALID_METHOD** [negative evidence](evidence/R02-small-batch-raw-lc-write-v1/41633619697.json), excluded from all aggregation. Valid independent runner LC p95 A/A drift **11.30%,5.14%**, CPU drift **6.08%,2.51%**; every p95 and CPU improvement exceeds the local controls. Distinguish this **fixed-rate 2.5 queries/s** experiment from earlier closed-loop variable-QPS [#41627431408](https://github.com/moleus/ClickHouse/actions/runs/41627431408), which gave a different p95 effect because each candidate executed a different number of queries. **Not a production SLA or claim about optimized jemalloc / 14d 1.33m series**. Next gate is optimized allocator+binary, heldout long-window/client concurrency and resource caps; do not launch competing build.

## 2026-11-03 R07: **two independent 2.49M multi-sample stock-O1 native vs original-TAGS SQL ABBA runs**, plus native stale-NaN correctness counterexample

[Complete study, source/math/profiles/negative gates](experiments/R07_R01_MULTISAMPLE_NO_SIDECAR_20261103.md); [full R01 staggered machine evidence](evidence/R07-r01-multisample-native-tags-41633587870.json), [held-out aligned evidence](evidence/R07-r01-aligned-heldout-native-tags-41634426255.json), [stale/duplicate semantic counterexample](evidence/R07-stale-duplicate-native-counterexample-41635230738.json). No extra index, no C++ build, no source/production/scheduled changes.

- **R01 staggered [41633587870](https://github.com/moleus/ClickHouse/actions/runs/41633587870) SUCCESS:** stock unmodified O1 \`52529f0c7...\`, 400-slot/3d, **28,896** source series, **2,488,320** samples at **15s/60s/300s**, source-pinned R01 SHA \`ae85496efcab3417634ac812cbd50a40c4eea297a397b50792efd024838e1b9a\`, 113,760 post-merge sample bucket rows. Native vs original-TAGS-per-ID-avg-then-group-avg SQL: six native nonempty selectors ×3 distinct evaluation timestamps = **18/18 exact output parity checks**; six cases ×4-phase A/B/B/A ×(2 warmups+4 measured)=**96 measured requests**, all same-host A/A drift <4.6%. Broad **pod1d native 75.596→SQL37.952ms (1.992x)** and CPU123.21→47.58ms; broad **pod3d 110.460→57.188ms (1.932x)** and CPU213.10→76.26ms; **selected SAMPLES marks unchanged 302** in both broad cases. Broad node1d ~1.019x (near neutral), node3d 1.474x. Query tracker peak falls, but **whole process RSS does NOT show a matching fall** due allocator retention; do not present query memory as actual RSS savings. Processor \`elapsed_us\` profiles contain native ExpressionTransform/AdaptiveBucketMergingSource/ReadFromMergeTree costs but are **not exclusive CPU**.
- **Held-out aligned [41634426255](https://github.com/moleus/ClickHouse/actions/runs/41634426255) SUCCESS:** exact independent R01 aligned generator SHA \`ea9a8d43c9e58ed916f1bf43949556d8ed18169ddd14033c293af4cd54c9881d\`. Same series, values, total samples, original TAGS and SQL formulas; only start phase changes, resulting in **86,688** physical SAMPLES bucket rows. Again **18/18 independent output comparisons, 6 ABBA cases, 96 measured requests and <2.33% A/A control drift**. Broad **pod1d 70.200→35.201ms (1.994x)**, pod3d 90.582→43.398ms (**2.087x**) with **233→233 selected marks**. Node1d remains near neutral1.034x, node3d1.185x. These are independent hosted runners; **only within-host ratios are comparable**, not absolute p50 across runs. This validates a robust conditional ~2x pod query-path optimization *on the two closely related synthetic R01 fixtures*; no general production extrapolation.
- **Actual stale/duplicate test [41635230738](https://github.com/moleus/ClickHouse/actions/runs/41635230738) SUCCESS as a correctness counterexample:** native range selector drops ONLY Float64 stale NaN payload \`0x7ff0000000000002\` (see pinned \`src/Storages/TimeSeries/PrometheusQueryToSQL/fromSelector.cpp\`), retains ordinary NaN. A stored series with values **5 and stale NaN**: native avg is **5**, raw R07 SQL \`avg\` becomes **NaN (wrong)**, fixed SQL \`avgIf\` filtering exact stale bits returns **5**. A duplicate timestamp with values1,7 and another later value3 is stored as2 points (max7 at first timestamp) and native/SQL avg both5; see pinned \`TimeSeriesSink.cpp\` and \`timeseriesMaxValueForDuplicateTimestamp.h\`. **Unmodified R07 SQL is NOT an arbitrary native PromQL replacement**; the 2x R01 win is valid only for the finite-gauge no-stale subset tested. Correct stale filtering (not all-isNaN) and duplicate/offset/multigrid/ACL/NaN semantics must pass before any C++ route.

**Decision:** preserve as a high-upside **conditional source/query-path research result**; do **not** add a global rewrite or deploy a day-cover sidecar. The read-mark count was identical in headline cases, so this is not a physical part/pruning gain. Next R07 gate: exact stale-marker fix and benchmark its overhead; negative/corner multi-grid and repeated update probes, then CPU/heap/1/4/8 concurrency on independent workload. Long jemalloc build \`41607779848\` remains owned and untouched. [Handoff](handoffs/20261103-r07-r01-two-fixtures-stale-11.json).

## 2026-11-03 R19: expired SAMPLES leave TAGS/TimeRanges; **replicated native query CPU penalty**

[Canonical R19 root](claims/roots/R19.json), [pinned source and cost proof](experiments/R19_RETAINED_METADATA_COST_PROOF_20261103.md), [immutable three-runner evidence](evidence/R19-expired-samples-retained-tags-timeranges-v1/41635997049.json) / [SHA-verified reducer CI #41635997049](https://github.com/moleus/ClickHouse/actions/runs/41635997049). **Decision: REPLICATED_LAB_EFFECT.** The pinned `src/Storages/StorageTimeSeries.cpp ## Retention` explicitly states that the default TAGS target has **no TTL** and retains an ID after all its SAMPLES are removed. `tags_deduplication_cache_expiration_seconds=3600` controls an in-memory cache, **not** persistent TAGS retention.

Exact same stock O1/LLVM22 binary and source, v11 RAW_UUID schema, identical 2000 active series/timestamps/values. Retained variant first loaded 48000 retired IDs and dropped only its old `SAMPLES` partition `202608`. After this **deterministic physical deletion** (not a TTL scheduler test), both tables had exactly **2000 live SAMPLES rows, identical 42310 B live SAMPLES part**, and identical nonempty full native PromQL results. But original TAGS+TimeRanges retained **50000+50000** rows vs clean **2000+2000**. QueryFinish broad physical read **6000→102000 rows (17x)**, **454106→7894106 B (17.38x)**, selected marks **10→22**.

[Initial valid CI #41635340660](https://github.com/moleus/ClickHouse/actions/runs/41635340660) plus [two independent runners #41635691926](https://github.com/moleus/ClickHouse/actions/runs/41635691926), source/fixture/artifact exact SHA gates and native oracle, total **312 native requests**, each selector A/B/B/A 3 warmup+10 measured, all baseline CPU/p50 drift ≤25%.

| Retained/clean native ratio, geomean of 3 within-runner A/B/B/A | Broad 1d | Exact project=p0 |
|---|---:|---:|
| Read rows | **17.0x** | **17.0x** |
| Read bytes | **17.38x** | **17.42x** |
| CPU p50 | **1.462x (+46.2%)** | **1.325x (+32.5%)** |
| Wall p50 | **1.428x (+42.8%)** | **1.283x (+28.3%)** |

All four latency/CPU effects exceed local A/A drifts on every runner. Source evidence + physical writes confirm persistence; the demonstrated query penalty is real for this synthetic fixture. Earlier [#41635086542](https://github.com/moleus/ClickHouse/actions/runs/41635086542) is **INFRA_FAIL**, connection refused before SQL because server was not started; [negative evidence](evidence/R19-expired-samples-retained-tags-timeranges-v1/41635086542.json). No failure numbers pooled.

**Production-style bound (not measured):** 400 nodes with 3h lifetime imply 3200 new IDs/day/metric; 15d main SAMPLES retention≈48000 live IDs, but 365d TAGS may accumulate≈1.168m identities (24.3x live) if no maintenance. Do **not** extrapolate wall latency linearly. Distinct next gate: actual SAMPLES TTL expiry plus late/resumed series semantic safety, then selective metadata pruning/retirement without invalidating old IDs. R07 already owns time-day covering sidecar; do not create a duplicate R07 index. No production changes or default DDL promotion.

## 2026-11-03: R18 start alignment × value codec 4992 native concurrent requests

[Successful 1/4/8-client nested native ABBA #41633815009](https://github.com/moleus/ClickHouse/actions/runs/41633815009),
[immutable 36-panel/9-interaction codec evidence](evidence/R18-codec-start-stagger-concurrency-41633815009.json),
[exact same-codec timestamp-cost reduction](evidence/R18-timestamp-alignment-cost-41633815009.json),
[full source and limitations](../ts-lab-r18/START_STAGGER_CODEC_CONCURRENCY_20261103.md).
Stock SHA-verified O1 LLVM22/glibc, RAW UUID TimeSeries v11, 3600s bucket,
28,896 IDs,2,488,320 original gauge points; only node-start alignment
`(slot*317)%10800` and SAMPLES.value `ALP+ZSTD(3)` vs `ZSTD(1)`
varied. Same-runner outer aligned/stagger/stagger/aligned with
inner ALP/ZSTD1/ZSTD1/ALP for three native full PromQL selectors
× clients1/4/8. **4992 requests** (3744 measured), 144 phases,
36 valid ABBA panels and nine repeated interactions; complete
native within-alignment label/timestamp/value parity, physical
parts/hash identical except expected staggered bucket count, no OOM.

| Physical SAMPLES | Aligned | Staggered |
|---|---:|---:|
| Rows / marks | 86,688 / 340 | 113,760 / 446 |
| ALP+ZSTD3 compressed bytes | 5,303,391 | 5,952,971 |
| ZSTD1 compressed bytes | 3,414,486 (−35.62%) | 4,126,572 (−30.68%) |

On **unchanged ALP+ZSTD3** with eight clients, staggering node starts
raises broad-node native p50 **492.074→781.375ms (+58.8%)**,
CPU/request **235.002→381.293ms (+62.25%)**; read_rows
57,728→67,136 (+16.3%), compressed bytes4.149→4.526MB (+9.08%).
The two outer-panel baseline p50 repeat drifts were 1.51%/1.37%.
Namespace and burst also regress. This is a **whole-workload
timestamp-offset effect**, not proof of any single operator/codec
causing the extra CPU. Under eight clients the codec CPU ratios
ZSTD1/ALP remain ~0.982–1.003 depending selector/alignment;
no global CPU upgrade despite >30% compressed storage saving.
The synthetic shifted source does **not** model constant 400
active nodes at lifecycle boundaries, and no natural merging
or true prod O2/O3+jemalloc equivalence was tested.

## 2026-11-03: R18 orthogonal ALP transform × ZSTD level 2×2 factorial

[Full native 2-job SUCCESS #41635457586](https://github.com/moleus/ClickHouse/actions/runs/41635457586),
[immutable 24 matched ABBA factor results](evidence/R18-alp-zstd-level-factorial-41635457586.json),
[full source/CPU stack audit](../ts-lab-r18/ALP_ZSTD_2X2_CPU_AND_STORAGE_20261103.md),
[predeclared confound correction](../ts-lab-r18/VALUE_CODEC_2X2_FACTORIAL_PLAN_20261103.md).
Prior R18 comparison **changed two variables simultaneously**:
ALP transform and ZSTD level3→1. Now four explicit identical v11
SAMPLES schemas vary only `samples.value`: ALP+ZSTD3,
ALP+ZSTD1, ZSTD3, ZSTD1. Two SHA-pinned original patterned
and high-entropy Float64 fixture jobs independently verify
effective codecs, 2,488,320 points, identical native labels,
timestamps and values, immutable parts, 24/24 ABBA,
1728 HTTP queries, CPU and p50/p95 with valid baseline drift.

| SAMPLES value codec | Patterned compressed bytes | High-entropy bytes |
|---|---:|---:|
| ALP+ZSTD3 | 5,303,391 | **19,686,139** |
| ALP+ZSTD1 | 5,550,516 | **19,686,139** |
| ZSTD3 | **3,355,724** | 20,216,284 |
| ZSTD1 | 3,414,486 | 20,216,284 |

**Isolated ALP removal at fixed ZSTD3** saves36.72% SAMPLES
bytes on patterned values but **increases bytes2.69%** on
high-entropy values. ZSTD3→1 at fixed ALP adds4.66% patterned
bytes (without ALP +1.75%), no entropy byte difference.
On high entropy, removing ALP at fixed ZSTD3 reduces
`pod_namespace` CPU **111.914→102.548ms (−8.37%, A/A CPU
drift0.74%)** and `burst15` CPU **74.499→66.506ms (−10.73%,
drift4.90%)** with exact full-native parity. Patterned namespace
CPU54.633→54.681ms (near neutral, CPU drift7.08%).
Thus the earlier one-run +10% namespace CPU penalty for
two-variable ZSTD1 must **not** be attributed to ALP alone or
promoted as a reproducible global regression. One-query sampled
ALP CPU stacks support decompression overhead, but not precise
causal function percentages.

Both invalid pilot runs [#41634612567](https://github.com/moleus/ClickHouse/actions/runs/41634612567)
(backtick DDL guard) and [#41634883798](https://github.com/moleus/ClickHouse/actions/runs/41634883798)
(selector dictionary shadowing after valid physical storage, before
queries) are preserved as [M43](METHODOLOGY_AND_FAILURES.md).
Now five pure preflight unit tests run **before binary/fixture download**.
**DECISION:** no global codec switch. Need realistic value entropy and
3h node starts, natural repeated writer/merge, multiple clients
with an optimized+jemalloc binary. The independent C++ build
[#41607779848](https://github.com/moleus/ClickHouse/actions/runs/41607779848)
was still compiling in the last check; do not cancel or replace.


## 2026-11-03 R19: actual 30d SAMPLES TTL, late insertion and resumed-ID semantics PASS

[Immutable successful TTL proof](evidence/R19-expired-samples-retained-tags-timeranges-v1/41637046443.json) / [stock-O1 CI #41637046443](https://github.com/moleus/ClickHouse/actions/runs/41637046443), same pinned source/binary. **A real inner SAMPLES AggregatingMergeTree** with `PARTITION BY toDate(bucket,'UTC')`, `TTL toDateTime(bucket,'UTC') + INTERVAL 30 DAY`, `ttl_only_drop_parts=1`, and `recent_samples_ttl_seconds=0` was verified with `SHOW CREATE`. 128 older Aug10 IDs and 16 live Oct20 IDs ingested, native `avg by(scope)(avg_over_time(metric[1d]))` oracle confirmed.

- After `MATERIALIZE TTL SETTINGS mutations_sync=2`, SAMPLES had **16 live rows**, historical Aug10 **0 rows**, and historical TAGS+TimeRanges still **144 IDs**. Native live average exactly **100.75**.
- A late expired sample in the Aug10 bucket disappeared after another actual `MATERIALIZE TTL`, leaving **16** live points and unchanged native result.
- Reusing one original expired ID with a fresh Oct20 sample produced **17** SAMPLES rows and native groups `live=100.75` and `old=7.0`, while TAGS and TimeRanges stayed **144 stable IDs after FINAL**. This verifies this specific resurrection/late-write scenario, not NaN, ACL or HA.
- **Important part lifecycle finding**: old `20260810` partition can remain in `system.parts` with **0 rows** and **1244 B** even after TTL; after one late old insert it had two empty parts totalling **2488 B**. **Zero-row parts are not retained expired samples**. First [CI #41636600724](https://github.com/moleus/ClickHouse/actions/runs/41636600724) incorrectly required complete partition disappearance, failed after `post_ingest`, and is preserved as [INVALID_METHOD evidence](evidence/R19-expired-samples-retained-tags-timeranges-v1/41636600724.json). Fixed no-expired-rows oracle in commit `dc1d7869912b8d3a6b9ee2e48c0944308ceb1086`; source and binary unchanged.
- Next question **within existing R19 ownership**: do repeated late expired inserts accumulate zero-row parts, and does natural merge/explicit `OPTIMIZE FINAL` remove them? Do not claim a permanent part leak before a part-lifecycle test. No automatic TAGS deletion recommended.

## 2026-11-03: R18 patched jemalloc binary SHA and operational gate

Previously running [jemalloc build #41607779848](https://github.com/moleus/ClickHouse/actions/runs/41607779848)
**completed successfully** without cancellation/restart, source
`52529f0c711566d696917678bf15e7068772fdde`,
LLVM22 O1, ZIP SHA256
`9e716f524c0802a29d42cc542accc3b388b2766920ae04936687ad101e6e2295`,
verified executable SHA256
`26b1577cea637b98a1d0b3c9efc996cd266234bf6ef89bd1912c75c56098eed3`.
Downloaded ZIP and extracted zstd TAR with both SHA256 manifests
passing. Runtime `clickhouse local` reports v26.11.1.1,
`system.jemalloc_bins` **232 rows**, **66039 active bin
registrations** and `system.jemalloc_stats` reports jemalloc5.3-RC,
nonzero allocated/resident, profiling and background threads.
[Immutable binary/provenance/source-diff record](evidence/R18-jemalloc-patched-cxx-artifact-readiness-41607779848.json),
[preregistered R18 capacity gate](../ts-lab-r18/JEMALLOC_PATCHED_NATIVE_CAPACITY_GATE_20261103.md).

**IMPORTANT ALLOCATOR CAUSALITY LIMIT:** unlike unmodified stock O1,
this binary also has two non-allocator source patches:
`StorageTimeSeriesSelector.cpp` defaults-aware JOIN override,
`Converter.cpp` narrow `sum(rate(exact_metric))` fastpath.
R18 `avg by(...)(avg_over_time(...[1d]))` should not take
the fastpath, and does not explicitly override JOIN, but binaries
are **not patch-identical**. Any source-glibc versus
patched-jemalloc speed/RSS difference is a **diagnostic whole-binary
comparison**, never a pure allocator effect or production-normalized
benchmark. A separate identical-patch glibc build would be
required for genuine allocator A/B.

[Self-path, no-compile patched jemalloc operational gate #41637869309](https://github.com/moleus/ClickHouse/actions/runs/41637869309)
launched to repeat the full R18 same-fixture 1/4/8-client
nested native PromQL parity, read CPU and capacity. Its
result must be checked independently before claiming PASS.
It verifies downloaded executable SHA, active running-server
jemalloc bins and stats, original/staggered source fixture SHA,
complete native results, memory and immutable physical layout.
Original R18 source/claims untouched; no master/old branches
or production changes.


## 2026-11-03 R19: real TTL empty SAMPLES parts converge, not unbounded in 16 repeats

[Immutable 17-observation run](evidence/R19-expired-samples-retained-tags-timeranges-v1/41637697605.json) / [CI #41637697605](https://github.com/moleus/ClickHouse/actions/runs/41637697605) **SUCCESS, decision NO_MONOTONIC_EMPTY_PART_GROWTH within this run**. An actual inner SAMPLES 30d TTL table, 16 valid live ID samples and one late expired ID. Following 16 sequential old-point inserts with synchronous TTL materialization after each, **all expired SAMPLES rows were 0** and full native 1d gauge group remained exact. The old partition's active zero-row parts underwent **1→2→3→4→1 repeatedly**, one zero-row part uses **1244 bytes**, maximum **4 parts/4976 bytes** before automatic consolidation; final/3s natural idle and explicit `OPTIMIZE FINAL` remain at 1 part/1244 bytes. Physical snapshots, not a trace of exact merge operator. Final SAMPLES=16, TAGS=17, TimeRanges=17, original tags/metrics intact. Thus the earlier single-point 2-part observation is not evidence of permanent or unbounded zero-row part leakage. Not a stress/long-run/HA proof.

## 2026-11-03 R19: direct TAGS cleanup creates invisible fresh samples while dedup cache stays warm

[Source + immutable safety evidence](evidence/R19-expired-samples-retained-tags-timeranges-v1/41642999590.json) / [CI #41642999590](https://github.com/moleus/ClickHouse/actions/runs/41642999590) **SUCCESS, SAFETY_HAZARD_REPRODUCED**. After direct `ALTER DELETE` of just one inner TAGS row on an isolated synthetic TimeSeries table (no prod!), **cache enabled** still remembered the ID, so one successful new INSERT produced **2 physical sample points, 0 TAGS IDs, native PromQL returned no series**. In the **cache disabled** control, the same INSERT produced **2 sample points, 1 TAGS ID, native 2-point average=13.5**. Table-scoped `SYSTEM DROP TIME SERIES CACHES <table>` followed by one more INSERT restored 1 logical TAGS ID and native 3-point visibility. Physical TAGS rows may be 2 for one logical ID until ReplacingMergeTree merge; always check `uniqExact(id)`. This behavior is source-documented as expected when altering targets directly: `TimeSeriesSink.cpp` and `TimeSeriesDeduplicationCache.cpp` cache prior writes; `ParserSystemQuery.cpp` states caches do **not** notice direct inner-target changes and requires a **table argument** to drop caches. This is not a bug in normal TimeSeries inserts or standard sample TTL. It invalidates naïve direct TAGS GC while writes continue, particularly with multiple writer replicas. [Invalid syntax pilot #41642319169](evidence/R19-expired-samples-retained-tags-timeranges-v1/41642319169.json) and [invalid physical duplicate count pilot #41642667408](evidence/R19-expired-samples-retained-tags-timeranges-v1/41642667408.json) retained separately. Next R19 gate tests *TimeRanges-only* stale-row retirement, leaving TAGS untouched, with exact late/resumed-ID native oracle and then real read/CPU Pareto; no production cleanup is approved.

## 2026-11-03 R19: TimeRanges-only stale-retirement native semantics (sample TTL + resumed ID) PASS

[Actual TTL semantic CI #41643612916](https://github.com/moleus/ClickHouse/actions/runs/41643612916), [immutable proof](evidence/R19-expired-samples-retained-tags-timeranges-v1/41643612916.json). In unmodified stock-O1 v11 TimeSeries with true 30d SAMPLES TTL, original 128 retired +16 active samples had 144 TAGS and144 TimeRanges. After confirmed SAMPLES TTL expiration **16 live sample points remained**; deleted only old `TimeRanges.max_time < 2026-09-01`, preserving **144 TAGS IDs, 16 TimeRanges IDs and exact native current result**. Fresh INSERT for an original expired ID regenerated TimeRanges and visible sample (`144 TAGS /17 TimeRanges /17 live points`), including after a late expired backfill and TTL. No TAGS cache invalidation needed because TAGS was never deleted and TimeRanges is written on every insert (`TimeSeriesSink.cpp`). **SEMANTIC_LAB_PROOF_ONLY**: direct TimeRanges mutation while there are concurrent inserts, readers, replicas or unexpected backfills was not tested or approved.

## 2026-11-03 R19: TimeRanges-only cleanup cuts metadata rows nearly half but fails CPU/latency benefit

[Stock-O1 104-native-query A/B/B/A #41644188939](https://github.com/moleus/ClickHouse/actions/runs/41644188939), [raw immutable negative evidence](evidence/R19-expired-samples-retained-tags-timeranges-v1/41644188939.json). **NO_ROBUST_BROAD_IMPROVEMENT**. Two independent identical v11 RAW UUID tables ingest same SHA-pinned 48k retired+2k live IDs, DROP only old August SAMPLES partition and retain identical 50k TAGS and active 2k SAMPLES compressed bytes; only candidate prunes 48k obsolete TimeRanges (50000→2000 rows), original keeps 50000. Exact independent full nonempty native PromQL oracle for broad1d and `project=p0`, 2× A/B/B/A 3+10 runs/phase, no result cache; source/bin/fixture SHA identical; all baseline drift below25%.

| Same-runner pruned/full native | Broad1d | Exact project=p0 |
|---|---:|---:|
| Read rows | 102000→54000 (−47.1%) | 102000→54000 |
| Read bytes | 7.894→5.974 MB (−24.3%) | 7.944→6.024 MB |
| Selected marks | 22→16 | 22→16 |
| p50 wall | 28.84→28.26ms (−2.0%) | 20.89→21.63ms (**+3.5%**) |
| p50 CPU | 29.25→27.84ms (−4.8%, below5% practical minimum) | 20.86→21.15ms (+1.4%) |

Both tables have exactly the same 50k original TAGS physical rows. Even after candidate mutation, reinsert of original old ID regenerated 2001st TimeRanges and SAMPLES, native `active=1.47445`, `retired=999`. Theoretical scan bound 102k→54k logical rows is observed, but **rows/IO saved are not whole-query speedups**; TAGS reads, label registration, grid and other work remain. Direct GC has additional unmeasured mutation CPU/write/storage/replica/race costs. **Do not deploy TimeRanges-only cleanup as a performance optimization.** R07 owns active-ID access path/sidecar; do not duplicate their current tests. Avoid global `TAGS DELETE`: it causes hidden new samples while cache warm, proven separately in R19 [#41642999590](https://github.com/moleus/ClickHouse/actions/runs/41642999590).

## R19 handoff and ownership boundary

[Consolidated R19 handoff](handoffs/20261103-r19-retention-21.json) links all verified retained-metadata, TTL/resurrection, empty-part, unsafe TAGS-GC, TimeRanges-only semantic and native negative-performance evidence. **No production promotion.** [R19 root](claims/roots/R19.json) stays owned. Other agents should not rerun these exact workloads or introduce duplicate R07 time-aware sidecars; R07 already owns active-ID query path research. New R19 cleanup proposals first need a source-backed atomicity proof across writers/replicas and an explicit CPU/read/write cost bound.

## 2026-11-03 R21: TAGS cache capacity knee creates native query read amplification (stock O1)

[Canonical owner root](claims/roots/R21.json), [preregistered claim](claims/R21-tags-dedup-cache-working-set-knee-v1.json), [immutable two-host evidence](evidence/R21-tags-dedup-cache-working-set-knee-v1/41652867042.json), [two-job PASS #41652867042](https://github.com/moleus/ClickHouse/actions/runs/41652867042). Same **unpatched** stock O1 ClickHouse binary SHA `f0a2fa1...`, canonical 4096 long-lived full IDs ×12 inserts=49152 sample points, v11 RAW UUID TimeSeries. Four fresh server processes `1MiB/32KiB/32KiB/1MiB` per independent runner, same cache expiry 3600s, one static or rotating row order (each runner has only one order). STOP MERGES to retain raw TAGS duplicates and isolate write volume; all full native PromQL labels/timestamps/values exact.

**Pinned source math:** `TimeSeriesDeduplicationCache.h APPROXIMATE_ENTRY_SIZE` on 64-bit=(16+6×8)+(16+2×8)+(24+3×8)=**144B**; large cache nominal 7281 entries covers all 4096 IDs; tiny 32KiB nominal 227. Global `system.events` worker-inclusive hits/misses large **45056/4096**, tiny **2497/46655** in every phase and on both independent runners. Raw TAGS rows large **4096**, tiny **46655 (11.3904x)**; TAGS compressed bytes **137512→~1571k (11.42x)**. Native QueryFinish physical read_rows **102400→144959**, and the extra **42559 rows equal exactly the extra TAGS rows 46655−4096**; read_bytes **5972069→10824906** (1.81x).

| Within-runner tiny/large, each two-phase mean | Fixed order | Rotated order |
|---|---:|---:|
| Whole process INSERT CPU | 0.755/0.555=**1.360x** | 0.745/0.580=**1.284x** |
| Whole process write_bytes | **1.660x** | **1.642x** |
| Full native PromQL p50 wall | **2.315x** | **3.141x** |
| Full native PromQL QueryFinish CPU p50 | **3.19x** | **4.26x** |
| A/A process CPU drift | **1.8%** | **6.9%** |
| A/A native wall drift | **8.4%** | **5.3%** |

**Critical limits:** These jobs are **two different runners**, so a larger small-cache *relative* read penalty on rotated order is **not** proof of input-order causality. Both order jobs had **identical TAGS miss/hit totals and native read_rows**. Actual single-host order-only [CI #41653813776](https://github.com/moleus/ClickHouse/actions/runs/41653813776) confirmed identical TAGS rows/cache counters across all four phases, but read timing is **INVALID_METHOD** because fixed-order A/A wall drift **38.85%** and CPU drift **34.61%** (25% gate); [immutable negative evidence](evidence/R21-tags-dedup-cache-working-set-knee-v1/41653813776.json). Do not claim order-driven latency difference. Also [rejected instrumentation pilots #41651757821/#41652494693](evidence/R21-tags-dedup-cache-working-set-knee-v1/NEGATIVE_PILOTS_20261103.md): query-level ProfileEvents omitted cache counters, and fresh server global events omitted zero-valued entries; these are not zero cache hits. `system.events` post-write includes actual worker hits/misses. `QueryFinish` INSERT CPU is NOT equivalent to measured whole process CPU (the two diverged), so do not describe it as total cost.

**No production cache-size recommendation**: this deliberately scales working set beyond 32KiB, but default is100MiB and production working-set cardinality and SLRU dynamics remain unknown. Merges were stopped for raw write causality, so long-run ReplacingMergeTree merge and retention costs remain unmeasured. [R22 root](claims/roots/R22.json) isolates expiration **at fixed full-capacity 1MiB**; no duplication of R07 active-ID planner or R20 native sum/count implementation.

## 2026-11-03 R23: reordering labels inside JSON tags Map **does not** reduce native TimeSeries CPU

[Canonical R23 root](claims/roots/R23.json), [source/mathematics/falsifiers](experiments/R23_TAG_MAP_NORMALIZATION_MATH_20261103.md), [immutable evidence](evidence/R23-tag-map-key-order-normalization-cost-v1/41662837084.json) / [stock-O1 native A/B/B/A CI #41662837084](https://github.com/moleus/ClickHouse/actions/runs/41662837084). **SUCCESS, scientific decision `NO_ROBUST_CPU_ORDER_EFFECT`**, not a client pre-sort optimization. Validated by state contract [#41663027781](https://github.com/moleus/ClickHouse/actions/runs/41663027781).

Same 4096 active stable IDs ×24 inserts=98304 samples per phase, 19 input labels + injected `__name__`, stable outer row order, source and 1MiB/3600s cache, RAW_UUID inner schema. Intervention is solely **order of JSON Map keys within each row** (sorted A0/A3 vs deterministic per-series/batch shuffle B1/B2). Same canonical fixture SHA `826f8d12...`; 39980232 exact same wire bytes per mode, but wire hashes differ as preregistered. All **four full ID+normalized Map fingerprints** match exactly (`a3844034...`); same global cache hits **94208** and misses **4096** per phase, same raw TAGS **4096 rows/472749 compressed bytes**, same TimeRanges bytes **1619136**, same native QueryFinish read rows **200704** and bytes **12349770**, exact full avg by(project) labels/timestamp/numeric oracle.

| R23 four fresh-server phases, shuffled/sorted means | Ratio | Within-sorted A/A drift |
|---|---:|---:|
| Whole-process ingest CPU: 0.970/0.975s | **0.99487 (-0.5%)** | **5.13%** |
| Process ingest wall: 2.20470/2.22912s | 0.98904 (-1.1%) | 1.41% |
| Native full query p50: 271.80/277.33ms | 0.98007 (-2.0%) | 3.18% |
| Native query CPU p50 | 0.97894 | 3.24% |

None exceeds the preregistered **max(5%, control drift)** threshold. Beware QueryFinish **INSERT** CPU is only ~24-28ms and does **not** represent whole process CPU (~0.97s) in this async pipeline; a larger QueryFinish-only ratio is not a source CPU gain. **Source proof:** `TimeSeriesSink::fillTagsColumns` creates and sorts all labels, injects `__name__`, hashes the full ID, then consults TAGS dedup cache; client pre-sorting does not remove these operations. Do not re-run this same fixture or optimize client JSON label order on this evidence. Future distinct R24 candidate may isolate **number of labels per series at equal raw wire bytes**, a different variable. R21 owns cache working set/row ordering; R22 owns cache expiry.

## 2026-11-03: E04 source audit — VM date-scoped postings, filter-cost feedback and index-cache lifecycle

[E04 source audit](epics/20261103-release-parity/E04-source-audit-20261103.md) and [epic](epics/20261103-release-parity/E04-victoriametrics-index-layout-parity.md) compare pinned VM v1.134.0 and ClickHouse TimeSeries v11 source. **SOURCE_AUDIT_ACCEPT, NO NEW RUNTIME GAIN**: both have inverted label access; VM registers per-day ordinary/composite tag→MetricID keys, reorders tag filters using stored per-date `loopsCount`, can switch expensive remaining postings to checking candidate metric names, and retrieves TSID/time-bounded data via block headers. CH v11 uses global TAGS `text(keyValuePairs)`, optional TimeRanges merge JOIN and SAMPLES sparse `(id,bucket)` marks. VM's matcher+date result cache is reset on index flush (coalesced worker callback) and deletion, not by disabling HTTP response cache. Source-derived VM per-day insertion lower bound is `3+2*label_count` logical entries per newly registered (series,date), **not measured bytes or CPU**. Evidence tied to successful R03/R15/R19/R07 runs; performance attribution and same-optimized-build CH/VM parity remain open. No new build, CI workflow, root claim, scheduler or production change. R05/R07 canonical ownership preserved.

## 2026-11-03: E04 R26 VM per-day vs global index — successful source-pinned A/B/B/A

[Human report](../ts-lab-e04/R26_VM_PERDAY_GLOBAL_RESULTS_20261103.md), [immutable evidence](evidence/R26-vm-perday-vs-global-query-index-path-v1/41670634959.json), [full artifact + CI #41670634959](https://github.com/moleus/ClickHouse/actions/runs/41670634959) **SUCCESS, ACCEPT_DIAGNOSTIC_PATH_ABBA**. VM v1.134.0 exact same optimized release; 2,488,320-point/28,896-series R01 3d fixture, SHA-pinned, one imported data snapshot cloned to fresh GLOBAL/DAY/DAY/GLOBAL processes. **Only `-disablePerDayIndex` changes**, matcher result cache fixed to 1 byte and response cache disabled. Four primary broad cases pass exact PromQL oracle, index query trace and A/A drift <25%. For one evaluation with [1d] lookbehind: **node 11.70→9.00ms p50 (1.30×), CPU 28.54→24.38ms**; **Pod 21.27→15.62ms (1.36×), CPU 38.96→33.44ms** (GLOBAL→DAY). Three daily evaluations yield near-neutral/small changes (node 1.012×, Pod 1.056× latency). One secondary narrow namespace one-tick case is **INVALID** due 47.98% global baseline wall drift; excluded from gain claims. This measures the **VM index read-path mechanism**, not CH/VM production parity or a ClickHouse optimization. Source `Storage.adjustTimeRange` selects global for full partition and >40-day windows; E03 owns any ClickHouse planner adaptation.

[R25 VM cache capacity 1 byte vs 64MiB](../ts-lab-e04/R25_CACHE_CAPACITY_DIAGNOSTIC_20261103.md) **INVALID_METHOD** (two raw artifact attempts [#41668987185](https://github.com/moleus/ClickHouse/actions/runs/41668987185), [#41669600149](https://github.com/moleus/ClickHouse/actions/runs/41669600149)). No speedup promoted; cache traces and counter inconsistency preserved. R25/R26 roots belong to E04, no duplicate R05/R07/R06 CBO experiments, no source/production/master changes.



## 2026-11-03 E04 additional source finding — CH native TAGS already has postings and cardinality heuristics

[Source-pinned CH/VM parity note](../ts-lab-e04/E04_CLICKHOUSE_TEXT_INDEX_SOURCE_PARITY_20261103.md): pinned ClickHouse v11 default `TAGS text(keyValuePairs)` contains **per-part row-ID postings**, exact `equals(arrayElement(tags,key),nonempty)` lookup, cardinality-based token ordering, bitmap intersection/union, and optional shared/query-local text-index caches. These are **not** the same as VM's **per-day stable MetricID postings** or cached full matcher+date ID sets. [Native selector source](https://github.com/moleus/ClickHouse/blob/52529f0c711566d696917678bf15e7068772fdde/src/Storages/StorageTimeSeriesSelector.cpp#L355-L407) and [text-index recognition](https://github.com/moleus/ClickHouse/blob/52529f0c711566d696917678bf15e7068772fdde/src/Storages/MergeTree/MergeTreeIndexConditionText.cpp#L2089-L2184) confirm an existing exact Map-pair path for eligible predicates. **SOURCE_PARITY_AUDIT_ACCEPT, not a new measured performance gain**. The E03 CBO owner should map how text-index cardinality / date scope enters native plan selection before proposing another index. R05 and R07 keep mark and time-cover ownership. E04 independent [R27 root](claims/roots/R27.json) tests index path on frozen 1.33m/14d Jenkins; no new ClickHouse build.


## 2026-11-03 E04 VM TSID locality source proof

[VM TSID versus CH SAMPLES layout](../ts-lab-e04/E04_VM_TSID_PHYSICAL_LOCALITY_20261103.md): VM v1.134.0 [`generateTSID`](https://github.com/VictoriaMetrics/VictoriaMetrics/blob/v1.134.0/lib/storage/index_db.go#L421-L435) builds a physically sortable `(MetricGroupID,JobID,InstanceID,MetricID)`; [`sortTags` has explicit label-key priority](https://github.com/VictoriaMetrics/VictoriaMetrics/blob/v1.134.0/lib/storage/metric_name.go#L569-L723) for namespace/service/job followed by instance/pod/node. CH v11 SAMPLES defaults to `ORDER BY(id,bucket)` with ID `(metric_hash64,UUID hash128(tags))`, with no explicit namespace/pod ordering. **SOURCE_LOCALITY_PROOF_ACCEPT, NOT A MEASURED CH/VM CAUSAL SPEEDUP.** Actual mark and storage-block locality must be measured by R05/E03 owners before considering any change of CH physical key; E04 R27 high-churn VM-only index path CI remains independent.

