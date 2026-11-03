# R07: independent 2.49M multi-sample R01 native vs ORIGINAL TAGS SQL proof

Question ownership: [R07 canonical root](../claims/roots/R07.json), [previous 70k one-sample factorials](R07_NATIVE_VS_SQL_FACTORIAL_20261103.md). This is a **no-extra-index, no-C++-patch** candidate for a *restricted* native query-path specialization, not a generic PromQL replacement.

## Exact stock source / dataset and repeatability

- [GitHub Actions 41633587870](https://github.com/moleus/ClickHouse/actions/runs/41633587870) **SUCCESS**. [Permanent compact evidence](../evidence/R07-r01-multisample-native-tags-41633587870.json). Original 31,514-byte GitHub artifact digest \`sha256:4caaa5e36f25f3ddb6169a324d3e3a5be5019b53758e20441cebb017092d116a\`, independently downloaded and checked. Full \`r07-r01/report.json\` records 144 attempted queries, QueryFinish CPU/marks/bytes/memory, six operator profiles, exact SQL, all outputs and physical audit.
- ClickHouse pinned master source \`52529f0c711566d696917678bf15e7068772fdde\`; **unmodified stock O1** binary \`f0a2fa1f814c58853f43423705cac10ab9df3bfe5887e27ebe9620ba7cb9660e\`, no recompile, same source/binary/runner within each matched test. Query memory limit 3.5GB, \`max_threads=4\`.
- Reused exact [R01 staggered generator](../../ts-lab-r01/generate_k8s_churn_fixture.py): 400 node slots, 3-hour lifetimes, 24 rotations in three days, 60s node/300s pod/15s burst gauges; 28,896 unique full-hash UUID series and 2,488,320 real synthetic points. Frozen JSONL SHA256 \`ae85496efcab3417634ac812cbd50a40c4eea297a397b50792efd024838e1b9a\`. Stagger is \`(slot*317)%10800\` seconds; samples from the last lifecycle may extend past the nominal three-day end. **Not** a real active-node/HPA simulation.
- Exact v11 TimeSeries RAW \`Tuple(UInt64,UUID)\`, original TAGS \`Map(LowCardinality(String),String)\`, TimeRanges unchanged, SAMPLES \`ORDER BY(id,bucket)\`, UTC monthly partition, 3600s buckets and RecentSamples off; one final merge then \`SYSTEM STOP MERGES\`. Physical post-merge: 28,896 TAGS, 28,896 TimeRanges, **113,760 bucket rows holding 2,488,320 points**, one part per inner table. Original TAGS/SAMPLES used by both plans; **no sidecar** and no schema change between native and SQL variants.

## Semantic proof before timing

Both plans compute \`avg by(group)(avg_over_time(metric{filters}[Xd]))\` with X=1 or3. SQL restricts SAMPLES buckets, uses \`arrayJoin(samples)\`, applies actual point time bounds **\`(eval-Xd,eval]\`**, computes \`avg(value)\` **per original full ID**, then \`avg\` per label group from original TAGS. For finite gauges with no stale markers or duplicate same-timestamp samples, this is the correct per-series weighting.

**Critical math:** if series \`i\` has \`n_i\` samples, true group mean is \`(1/N) Σ_i [(1/n_i) Σ_j x_ij]\`, **not** \`Σ_i Σ_j x_ij / Σ_i n_i\`. Different 15/60/300s sampling and phase/time boundaries make a naive global sample average wrong even if it looks faster. Both plans independently evaluated by [same stock binary](../../ts-lab-r01/bench_k8s_mixed_native.py).

**18 independent grid parity checks**: six selectors (node broad, pod namespace, pod broad, burst15, node3d, pod3d) × three separate native evaluation timestamps (Aug 5,6,7 2026 UTC), all **nonempty exact label/timestamp sets** and Float64 values matched existing \`assert_same\` numeric tolerance (relative1e-10, absolute1e-8). No empty-result pass-through. These are separate one-tick queries, not proof of equivalence for an arbitrarily multi-grid native PromQL call.

After parity, **6 paired A/B/B/A** comparisons: native/SQL/SQL/native, each phase 2 warmups +4 measured queries = 144 total (96 measured), full output equality on every request. All six A/A control drifts **<4.6%**, within 25% gate, and all QueryFinish records complete. No server restarts between phases; same live instance and frozen parts. RSS observer overhead under 2%.

## Paired native/SQL result (only within this runner)

| Selector & range | Native p50 | No-index SQL p50 | Native/SQL | Native CPU → SQL CPU | Native selected marks → SQL |
|---|---:|---:|---:|---:|---:|
| node broad 1d | 71.661ms | 70.330ms | 1.019x | 145.76→90.62ms | 154→154 |
| pod namespace 1d | 36.429ms | 33.657ms | 1.082x | 62.42→34.29ms | 234→294 |
| pod broad 1d | 75.596ms | 37.952ms | **1.992x** | 123.21→47.58ms | **302→302** |
| burst 15s 1d | 28.937ms | 23.295ms | **1.242x** | 39.59→23.63ms | 40→88 |
| node broad 3d | 155.932ms | 105.787ms | **1.474x** | 329.99→145.38ms | **154→154** |
| pod broad 3d | 110.460ms | 57.188ms | **1.932x** | 213.10→76.26ms | **302→302** |

The headline 1.99x/1.93x full **native-to-SQL** speedup is achieved without any supplemental storage. The mark counts of both broad pod cases are identical. Thus *neither fewer selected marks nor the R07 day-cover index* explains these specific gains. On node1d wall time is virtually neutral, demonstrating workload dependence. This supports a **conditional query-path research candidate**, not blanket rewrite.

The \`system.query_log\` query-level memory tracker for pod3d fell **59.0MB→17.19MB** and for node3d **80.92→22.29MB**. This is **NOT** a process RSS reduction: the sampled *whole process* RSS maxima were roughly pod3d 728→723MiB and node3d 707→704MiB, strongly influenced by allocator retention and phase order; don't claim production memory savings without a proper fresh-process matched allocation test.

## Representative processor work (not exclusive CPU)

Each number below is from one measured query per variant, stored as \`elapsed_us\` from \`system.processors_profile_log\`; input/output waits are separately recorded. Processor work can overlap in parallel, and sums need not equal QueryFinish CPU or critical path. **Never label summed work_us as exclusive CPU.**

**pod broad 3d** native representative: ExpressionTransform work63.83ms; MergeTreeSelect 52.50ms; AdaptiveBucketMergingSource 44.07ms; AggregatingTransform 20.07ms. SQL: MergeTreeSelect27.22ms; AggregatingTransform26.47ms; ArrayJoinTransform4.21ms. **node broad 3d** native: MergeTreeSelect169.11ms, AdaptiveBucketMergingSource51.51ms, AggregatingTransform33.06ms, ExpressionTransform29.4ms; SQL: AggregatingTransform54.14ms, MergeTreeSelect46.79ms, ArrayJoinTransform8.25ms.

These observations make **native bucket-to-grid processing, expression transforms, and decode** the next source-level investigation. The source already implements native \`avgForEach\`; do not claim lack of vectorization, nor assume the full 44ms \`AdaptiveBucketMergingSource\` can be eliminated safely. It may enforce duplicate/time-grid/staleness semantics that SQL shortcuts omit. Use CPU stack profiling in a matched phase to distinguish true exclusive CPU from processor elapsed time.

## Current decision

- **ACCEPT** this exact R01 synthetic **finite gauge + single evaluation \`avg_over_time\` + group average** SQL equivalence on original TAGS/SAMPLES and the paired improvements reported above.
- **NOT ACCEPTED**: arbitrary PromQL equivalence, stale markers, NaNs, duplicate timestamp arbitration, counter rate/reset, native histogram, regex, query ACL, multiple evaluation-step grids, write/merge workloads or production jemalloc O2.
- **Do not promote general SQL rewrite**: on the previous 70k one-sample/14d fixture, native broad1d/3d beat original TAGS SQL, with valid ABBA evidence [#41629492828](https://github.com/moleus/ClickHouse/actions/runs/41629492828). The query policy has to be workload-dependent. Different hosted runners cannot be pooled.
- Next gate: an **independent held-out R01 aligned fixture** (same 28,896 series and 2.49M samples but different sample-time phase). Reuse one stock-O1 binary and same queries with a source-only holdout, not a C++ build. If the effect changes sign or parity fails, record the negative and hold any planner patch. Then test NaN/stale/duplicate samples before any guarded native fastpath.

## 2026-11-03 held-out aligned R01: independent runner verifies effect

[Held-out CI 41634426255](https://github.com/moleus/ClickHouse/actions/runs/41634426255) **SUCCESS**, [permanent compact evidence](../evidence/R07-r01-aligned-heldout-native-tags-41634426255.json), independently downloaded ZIP SHA256 \`f4133a369b4144b823faa79dc3520d6e7479a9215c08a77a72e2e63053de5514\`. Identical full stable ID, tag distribution, Float64 values, cadences, fixture generator, exact native-vs-SQL formulas, test cardinalities and stock binary as staggered R01. **Only node-start time alignment changed**. Source-pinned independent aligned fixture SHA256 \`ea9a8d43c9e58ed916f1bf43949556d8ed18169ddd14033c293af4cd54c9881d\`. This changes SAMPLES physical bucket rows to **86,688** (vs113,760 staggered), total sample points still **2,488,320**, TAGS and TimeRanges both **28,896**.

Held-out again has **18/18 multi-tick separate native/SQL parity checks**, six 4-phase A/B/B/A comparisons, 96 measured requests, complete QueryFinish/processor records, A/A drift max 2.33%, zero failures/OOM. **No new index or C++ patch**.

| Case | Staggered run native/SQL p50 ratio | Held-out aligned native/SQL p50 ratio | Held-out native → SQL p50 |
|---|---:|---:|---:|
| node1d broad | 1.019x | **1.034x** | 66.626→64.428ms |
| pod namespace1d | 1.082x | **1.095x** | 34.964→31.923ms |
| pod1d broad | **1.992x** | **1.994x** | 70.200→35.201ms |
| pod 15s1d | 1.242x | **1.257x** | 26.166→20.821ms |
| node3d broad | 1.474x | **1.185x** | 91.917→77.596ms |
| pod3d broad | **1.932x** | **2.087x** | 90.582→43.398ms |

The **pod1d broad ~2x improvement survives two distinct physical bucket layouts**. For pod1d held-out, native/SQL QueryFinish CPU **114.96→41.30ms** with **identical 233 selected marks**; pod3d **174.01→58.08ms**, also **233→233 marks**. Node1d remains almost neutral; node3d benefit varies by node-start phase (1.185–1.474x). This is not evidence of universal gain and not a true external workload. **Both hosted runners are distinct**: compare paired ratios only, not cross-runner absolute p50.

## Source-based semantic blocker before native planner integration

Pinned [fromSelector.cpp](https://github.com/moleus/ClickHouse/blob/52529f0c711566d696917678bf15e7068772fdde/src/Storages/TimeSeries/PrometheusQueryToSQL/fromSelector.cpp) lines19–84 checks exact stale-NaN bit payload \`0x7ff0000000000002\` with \`reinterpretAsUInt64(value)\` and removes only this marker from **range selectors**, not ordinary NaNs. The current R07 SQL benchmark computes \`avg(tupleElement(point,2))\` without this check: **it is a deliberately restricted finite-sample oracle, NOT a generally correct PromQL rewrite**. Removing all \`isNaN()\` values would also be wrong. The SQL candidate must first implement the exact bit-level stale exclusion, then prove parity including stale-only/ordinary NaN cases.

Pinned [TimeSeriesSink.cpp](https://github.com/moleus/ClickHouse/blob/52529f0c711566d696917678bf15e7068772fdde/src/Storages/TimeSeries/TimeSeriesSink.cpp) lines324–395 sorts input samples and removes same-timestamp duplicates, choosing the **maximum finite value** (NaN loses to any finite). The shared [timeSeries duplicate merge rule](https://github.com/moleus/ClickHouse/blob/52529f0c711566d696917678bf15e7068772fdde/src/AggregateFunctions/TimeSeries/timeseriesMaxValueForDuplicateTimestamp.h) is associative and commutative. Duplicate semantics depend on the real stored bucket aggregate state, not on the JSONL input point count. An appropriate source-pinned small native semantic falsifier is [r07_stale_duplicate_semantic_oracle.py](r07_stale_duplicate_semantic_oracle.py) and its path-scoped [CI](../../workflows/ts-lab-r07-stale-duplicate-semantics-20261103.yml).

**Next decision gate:** do not port the raw SQL route to C++ until it filters stale markers, preserves correct per-series/per-timestamp arbitration, and passes multiple evaluation-step grid, NaN and concurrency tests. Even after semantic fixes, benchmark the extra per-point bit check and possible TimeRanges skipping against native under fixed O1 and subsequently production-like O2/jemalloc, not just this favorable high-churn fixture. R02/R18 and the long jemalloc build retain their owners.
