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

