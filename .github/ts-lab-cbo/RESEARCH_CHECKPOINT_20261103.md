# TimeSeries CBO research checkpoint 2026-11-03

Repository moleus/ClickHouse, isolated branch lab/ts-cbo-adaptive-20261103.
Pinned source 52529f0c711566d696917678bf15e7068772fdde.
No master or production mutations. Do not cancel other builds or enable scheduled GitHub workflows.

## Runs and validity

41530696837: first 6k comparison. INVALID short time windows (Nov 2 after last sample Nov 1). Do not cite short-window speedups.
41531461979: corrected 6k comparison, four workloads PASS.
41531500328: 30k/100k, seven jobs PASS; node 100k falsely failed exact JSON floating-point hash. The Python script now uses tolerant numeric parity and exact labels/time.
41532393067: 1.33m Jenkins direct JOIN comparisons, 4 jobs PASS. Important limitation: timed query included sum(sipHash64(id)) and groupBitXor checksum; CPU trace shows hashing costs a material portion of execution. Whole 1d: merge 114.604ms, hash 89.757ms; whole 14d: merge 358.614ms, hash 472.229ms. NOT pure JOIN latency. Reject stage 3d drift 51.7% and whole 7d drift 33%.
41533187321: count-only 1.33m follow-up, checksums validated separately, warm A/B/B/A and 30% drift guard. Prefer results of this run when completed.
41531656434: pinned-source O1 C++ build for opt-in join_algorithm / optimize_read_in_order override. Default native settings preserved. No measured gain yet.
41532747372: verified 6k/30k/100k results reducer; excludes initial invalid empty-window run.

## Critical source and profile observations

- src/Storages/StorageTimeSeriesSelector.cpp forces full_sorting_merge and optimize_read_in_order for the nested metadata JOIN.
- In direct diagnostic SQL, LEFT JOIN plus WHERE matched=1 becomes INNER JOIN in EXPLAIN. This is not the same as full native PromQL with timeSeriesStoreTags side effects.
- join_algorithm=auto picked SpillingHashJoin(HashJoin) in inspected 100k/1.33m plans even when measured merge was faster. Do not assume auto uses statistics to choose cheapest algorithm.
- 1.33m default TAGS estimates about 219.45k rows for stage, project AND regex despite actual 35k, 166250 and 190008 selected IDs. LABELS stored in Map are not equivalent to indexed dedicated columns. Need validate estimator details.
- ConditionSelectivityEstimator.h defaults: equal 0.01, unknown 0.33, LIKE 0.1. ConditionSelectivityEstimator.cpp combines unknowns and stats; do not assume independent filters have accurate intersections.
- CPU flame traces: ReverseIndex::insert, HashJoinMethods::joinRightColumns, MergeJoinTransform, LowCardinality UUID decoding, sipHash64Keyed (checksum overhead).
- Read path significant even when JOIN itself does little work. Processor elapsed_us sums are not wall time.
- prior native TimeRanges no-join run 41510101527 showed selective-stage benefit but huge regression for whole metric at 1/3/7d. Do not remove TimeRanges unconditionally.

## Ready for pipeline continuation

1. Analyze completed 41533187321 and use exact reported nonempty counts / performance without checksum overhead.
2. When C++ 41531656434 succeeds, retrieve verified binary artifact and use .github/ts-lab-cbo/bench_native_join_override.py. Compare native PromQL JOIN alternatives within the SAME patched binary; no O1/O2 cross-build speedup claims. Run on 1.33m fixture plus actual exporter-style synthetic workloads. Check labels/timestamps/numeric parity.
3. Add selectivity estimator probes with TAGS dedicated stage/project columns. Materialize statistics and compare estimated vs actual counts and index selections. Measure cost of extra column storage and lookup.
4. Collect EXPLAIN, processor wait vs CPU work, trace stacks, query_log memory, predicate_statistics_log and physical parts indexes. Use strict A/B/B/A with drift rejection, bounded memory, source/binary/fixture SHA.
5. Continue independent experiments while CI runs, not just polling. Avoid duplicate expensive builds.

Scripts: .github/ts-lab-cbo/run_adaptive_probe.py, bench_1330k_planner.py, bench_native_join_override.py, apply_join_override_cpp.py, reduce_campaign.py.
Automation: hourly continuing research checks are enabled. It does not offer sub-hour continuous work.



## Second pipeline discoveries and next actions

- 41533187321: corrected COUNT-only 1.33m native inner SQL JOIN campaign, 4/4 PASS, all 16 selector/window combinations. The previously timed sipHash64 checksum was separated into independent preflight. Whole 1d hash174.207ms vs merge189.928; whole 3d hash216.124 vs merge261.691; whole 7d hash343.062 vs merge286.320; whole 14d hash453.450 vs merge355.985. This is SQL diagnostic (LEFT to INNER rewrite), NOT native PromQL.
- 41534205329: interpolated crossover at whole metric 1.33m, 3..7d; 5d hash214.165ms vs merge280.526, 6d hash303.632 vs merge265.650. Crossover near 5-6d for this fixture. No universal decision threshold implied.
- 41534052746: all 32 CPU profiles exported as folded stacks, DOT sampled call graphs, SVG top-exclusive CPU bars. Source .github/ts-lab-cbo/analyze_cpu_stacks.py. On one 14d whole profile: merge CPU577.2ms, peak query memory126.2MiB; hash CPU839.8ms, peak176.6MiB; both ~60MiB compressed reads. Per-trial profiles are NOT experiment medians.
- 41533707123: Map vs dedicated stage TAGS, 1.33m. When use_statistics=1, the SQL INNER JOIN swaps build side and latency dropped from ~320ms to ~107ms. Stage column stats reported NDV=38, but estimated stage-filter rows17,500 instead of actual35,000. Default TAGS Map estimated219,450. The initial comparison order was not paired.
- 41535099397: corrected paired A/B/B/A proof, 1.33m; Map no-stats359.5ms vs stats123.25ms (2.9168x), control drift0.83%; dedicated stage no-stats357.5ms vs stats128ms (2.793x), drift2.52%. This validates planner reordering, NOT that stage column improves native speed. Full native parity 14 points/labels and numerical max_abs_error ~1e-13, but one-off timings for new stage column were slower.
- 41534706528: pure SQL 150k unique Tuple(UInt64,UInt64) keys, uniqExact=150k, uniqCombined64=150349. ALTER ADD STATISTICS ... TYPE uniq_v2 rejected Tuple; fingerprint UInt64 supports uniq_v2 and estimated cardinality150409. Source StatisticsUniqV2.cpp validator excludes Tuple even though collector supports tuple SQL aggregator. Optional patch .github/ts-lab-cbo/apply_tuple_uniqv2_cpp.py.
- 41535495895: 150k constant LC String, uniqExact=1 and uniqCombined64=1 but stored UniqV2 estimated 2; regular String estimated1; LC Nullable(String) estimated2. Source StatisticsUniqV2.cpp collects entire dictionary including unused implicit default; this also explains 1.33m Jenkins metric_name NDV2 instead of true1. Combined with unknown TAGS stage filter factor0.33: 1.33m * 1/2 *0.33 =219450 estimated. With known stage column 38 values: 1.33m *1/2 * 1/38=17500 estimated (actual35000). With composite ID NDV lacking statistics, default cardinality factor0.1 gives estimated JOIN output 10x higher than these source-row guesses.
- 41531656434: first O1 patched C++ build with opt-in native JOIN override still compiling at last check.
- 41535183046: second O1 pinned C++ build with both native JOIN override and opt-in Tuple UniqV2 validator patch still compiling at last check; do not cancel either, preserve ccache.
- .github/ts-lab-cbo/apply_lowcard_ndv_fix_cpp.py: staged source patch correcting UniqV2 statistics by collecting distinct actually referenced dictionary positions; incurs an O(N) index pass at materialization. This patch is NOT compiled, tested, nor production-ready yet. Test LC String, LC Nullable String, sparse/empty, memory and ingest overhead before using.
- .github/workflows/ts-lab-cbo-native-join-20261103.yml: NATIVE FULL PromQL A/B/B/A 4 selectors x 4 windows prepared with workflow_dispatch ONLY; DO NOT trigger until 41531656434 compiled artifact exists. Once artifact verified, change only this workflow on to push with path self-trigger to start its four independent jobs. Checks exact values/labels/timestamps and controls query memory.
- 41535785666: TimeRanges is AggregatingMergeTree; minmax skip index supported; ADD PROJECTION rejected with deduplicate_merge_projection_mode=throw. Settings prefer_projection_optimization unknown on pinned binary (despite newer docs). Cannot assume projections are safe on aggregated TimeRanges.
- 41536147012: revised small projection smoke under deduplicate_merge_projection_mode='rebuild', materialization and optimize_use_projections. If supported and semantics correct, only then consider 1.33m physical time-index A/B; preserve aggregation behavior across updates/merges.
- Future work must continue without waiting for CI, use existing verified binary and keep independent jobs active. Check branch tip and workflow statuses. Analyze error logs/artifacts and save with SHA. Never modify master or stop near-complete builds.


## New active CI / physical-index and joint-label findings

- 41535495895 and duplicate 41536208906: UniqV2 LowCardinality(String) and LC Nullable(String) produce NDV2 for a true one-distinct-value column (150k repeated nondefault values); String produces NDV1. This is linked to the source collector building over the LC dictionary, which includes implicit default. A standalone patch .github/ts-lab-cbo/apply_lowcard_ndv_fix_cpp.py has NOT been compiled or benchmarked.
- 41536188291: independent correlated-label 152k-series campaign passed; includes real native PromQL result parity (2069 points) on a Map table and dedicated label columns, and A/B/B/A matrix of combinations including impossible predicates that should return zero. See CI logs/artifacts for exact estimates and drift. Some no-stat controls drift >30% and must be excluded.
- 41536147012: small TimeRanges physical index/projection smoke PASSED after setting AggregatingMergeTree deduplicate_merge_projection_mode='rebuild'. Minmax skip index, projection materialization, and EXPLAIN control / cost / force queries all worked. Cost mode preferred base when projection read same one mark; force mode selected ReadFromMergeTree(time_by_max). Do not use prefer_projection_optimization on this pinned binary; it is an unknown setting.
- 41536526138: running two independent canonical 1.33m TimeRanges projection jobs (direct read, JOIN), each materializes a max_time-ordered projection and compares A/B/B/A projection disabled vs forced for 1/3/7/14d with source/fixture SHA and querylog CPU/read_marks. This is a diagnostic; AggregatingMergeTree repeated-update semantic parity is NOT yet verified.
- 41535099397 paired use_statistics proof: Map A359.5ms vs B123.25ms (2.9168x), drift0.8%; stage-column A357.5ms vs B128ms (2.793x), drift2.5%.
- 41531656434 and 41535183046 are separate ongoing source builds. DO NOT cancel. Native full PromQL workflow .github/workflows/ts-lab-cbo-native-join-20261103.yml is intentionally workflow_dispatch only until first artifact exists. Activate via editing on push trigger once compiled and run four pinned synthetic selector A/B/B/A jobs. Never compare O1 patched binary directly to earlier O2 as a one-variable optimization.
