# ClickHouse TimeSeries research checkpoint — 2026-11-02

This is the persistent handoff file for subsequent user turns. Read it **before** modifying the lab branch, together with `ENGINE_GOAL_AND_ROADMAP_20261102.md`, `EXPERIMENTS_20261102_ID_RANGE_AND_RATE.md`, and `TIMESERIES_V11_STORAGE_INDEX_ALIGNMENT_20261102.md`.

## User's requirements

- Improve **ClickHouse TimeSeries**, not replace it with a separate native events table.
- Support both very high-cardinality short/single-sample series (Jenkins) and normal long-running regularly sampled series.
- Compare performance and correctness with unchanged ClickHouse and VictoriaMetrics.
- Investigate source code, implement isolated C++ optimizations, run A/B/A benchmarks and adversarial correctness tests.
- Preserve the original labels, samples, PromQL semantics, ingest/storage behavior and controlled memory.
- Show diagrams of TAGS text postings, series IDs, SAMPLES physical buckets and PromQL evaluation windows.
- Keep a durable Markdown checkpoint, reread it when the user continues or conversation history is lost.
- Work only in fork `moleus/ClickHouse` on `lab/timeseries-fastpath-ci`; no upstream writes, no cancellation of ongoing builds.

## Source baseline and limitations

- Research source pinned to upstream ClickHouse `52529f0c711566d696917678bf15e7068772fdde`; lab patches are separate.
- VictoriaMetrics v1.134.0. The VM MetricsQL `rate` result is not necessarily numerically identical to ClickHouse's PromQL `rate`.
- CI ClickHouse binaries use `-O1` for fast build, not production `-O2/-O3` throughput evidence.
- User supplied `ClickHouse-master.zip` and mentioned a newer `ClickHouse-master(1).zip`. The mounted container exposed neither ZIP in the latest inspection; file materialization of the first archive returned 403. Use the pinned upstream GitHub source via the connected GitHub tool until the bytes become available. Do **not** claim that the archive was inspected.
- Last observed branch head BEFORE this checkpoint: `2367f9a0b464cbef93362dcd8afe8911411c0f4c` (2026-11-02 ~08:37 UTC).

## Verified strongest optimizations / negative findings

1. Broad Jenkins grouped average, late TAGS join and residual ID range:
   - `41412500210`: ~1540ms baseline vs 673ms candidate, query memory ~510MB -> 199MB on 282,036 target series mixed with 846,108 unrelated samples. Requires strict access/ID type/ownership controls. Narrow selectors regress; keep guarded.
   - `41413945372`: type-aware small-table-guarded candidate 1340ms baseline recheck vs 690ms candidate and 506.6MB -> 201.3MB memory (with extra ID metadata read). Dense rate paths not changed by guard, so any dense timing variation is noise. All eight output checks pass.
2. Tags index `tags_idx` already exists for `tags Map` and is used for equality. Posting lists point to ROW positions in each part; TAGS rows contain the series IDs. Never claim that no inverted index exists.
3. `samples_bucket_step_seconds=14400` improved dense sum(rate) ~175ms ->143ms but did not improve short Jenkins series. Do not modify global default.
4. Smaller hash table, one-bucket fast path, a second tag predicate rewrite, 8-level sample-granularity, global read threshold changes, `optimize_read_in_order`, and C++ array-batch runs did not yield a general performance gain. Reject them as defaults.
5. Full dense `timeSeriesSelector` rate already parallelizes read across four streams. A direct inner `GROUP BY id` query can be serial unless read/concurrent settings are changed.

## Newly verified run `41414437463` (successful; 2026-11-02 ~08:39 UTC)

- Goal: compare original `prometheusQueryRange` vs direct `timeSeriesRateToGrid(...)(samples)` grouped by ID with `sumForEach`, same TimeSeries internal SAMPLES table; 20,000 series x 120 samples, 60 output steps.
- A/B/A warm p50 in ms: full native 381.557, direct serial 1027.843, direct forced parallel 4=326.117, direct forced parallel 8=321.209, full native repeat=382.589; VM MetricsQL=67.186.
- All ClickHouse 60 values passed the exact numerical-equivalence tolerance check (`rel_tol=1e-10, abs_tol=1e-10`); VM not checked for rate equality.
- Full native read_rows=80,000; direct=60,000. Peak tracked query memory p95 native recheck ~82MB, direct parallel-4 ~84MB, direct parallel-8 ~84MB, direct serial ~111MB.
- EXPLAIN: full native already has ReadPool and AggregatingTransform x4; direct serial ReadPoolInOrder x1; forced direct ReadPool x4. This explains why merely increasing `max_threads` on direct SQL does not work.
- No C++ optimization resulted from that SQL test yet. Do not claim a decisive new engine speedup.
- Links: https://github.com/moleus/ClickHouse/actions/runs/41414437463 and original source script `.github/ts-lab-bench/bench_direct_id_rate_aba.py`.

## Further work

- First inspect `AggregateFunctionTimeseriesBase.h`, `AggregateFunctionTimeseriesExtrapolatedValue.h` and underlying `timeSeriesSelector` read source. Compare pinned source against latest upstream separately.
- Profile per-sample bucket classification and rate state finalization. Form isolated hypotheses; avoid new global settings without a measured A/B/A improvement.
- For a new code candidate, use an isolated GitHub Actions workflow with `cancel-in-progress: false`, exact source pin, verified baseline binary, independent compile+13 PromQL smoke tests and 10-case A/B/A Jenkins+dense correctness.
- Add edge correctness: 15s intervals, duplicate timestamps, NaNs/stale markers, missing labels, overlapping windows, offsets, shifted/noncanonical series IDs, mixed metrics, 14–90d late data and authorization over inner targets.
- Update this checkpoint with exact patch name/commit/run ID and measurements after each significant result. Do not promote a candidate until a confirmed win.

## Known risks

The direct-inner grouped-avg prototype may bypass the outer table's access checks when reading owned TAGS/SAMPLES directly. It is LAB ONLY until security/inheritance are validated. The metric prefix is a hash of metric name only in canonical `Tuple(UInt64, LowCardinality(UUID))` v11 IDs; custom/shifted values must use residual-ID fallback. Small-table guard 16,384 is an experiment, not a production cost model.

## 2026-11-02 continuation: inline rate window and profile (latest user turn)

The user again requested sustained source-level experiments and said a newer upstream `ClickHouse-master(1).zip` was attached. The conversation Files list still showed only `ClickHouse-master.zip`, and neither ZIP was mounted in `/mnt/data`. No claim is made that the new ZIP was read. Exact upstream `ClickHouse/ClickHouse:master` and pinned commit `52529f0c...` were compared through the GitHub connector: `AggregateFunctionTimeseriesBase.h`, `AggregateFunctionTimeseriesExtrapolatedValue.h`, `StorageTimeSeriesSelector.cpp`, `fromSelector.cpp`, `TimeSeriesIDGenerator.cpp` and `TimeSeriesIDRange.cpp` had identical blob SHAs (the inspected paths, not the whole repository).

Source insight:
- `AggregateFunctionTimeseriesExtrapolatedValueTraits::Aggregator` uses `AggregateFunctionTimeseriesSlidingSum<Summary>`.
- The invertible strategy in `AggregateFunctionTimeseriesSlidingSum.h` uses a `DequeWithMemoryTracking<std::pair<DateTime64, Summary>>`, which is a tracked `std::deque` alias. This may allocate for every per-series evaluator.
- New lab script `.github/ts-lab-pending/apply_rate_inline_window_cpp.py`, commit `37111c6a`, replaces only this local queue for rate/increase/delta with an inline 8-summary chronological ring, falling back to an allocated deque when >8 active summaries. It leaves samples' persistent hash/buckets, format version, duplicate handling, Summary merge/unmerge, extrapolation and output format unchanged.
- C++ build workflow commit `dc29c942`, run **41416547876 SUCCESS**, 13 PromQL regression tests passed. The binary is a research candidate, not a production patch.
- Randomized stand-alone deque/ring comparison of 1000 sequences x 200 steps matched exactly, but this is not an actual ClickHouse correctness check. Illustrative O2 benchmark on the standalone simulator (5-bucket window, 20K series x 60 steps, six passes): inline ~66ms vs deque ~106ms; under O1 the same comparison was ~119ms vs ~117ms. These numbers must **not** be presented as ClickHouse performance.
- Initial A/B/A **41416918067 FAILED due test harness**: copied `bench_array_batch_aba.py` only accepted the `array_batch` phase; original baseline part completed, candidate did not run. No C++ speed/correctness conclusion from that run.
- New dedicated `.github/ts-lab-bench/bench_rate_inline_window_aba.py` and workflow fix commit `d62f98eb`; corrected run **41417443992** started with exact original → candidate → original, 10 Jenkins/dense scenarios, plus 8 rate/increase/delta corner cases that cover 5m/30m/1h windows and 15s/60s/300s evaluation. Status must be checked on every continuation.
- New independent CPU profiler `.github/ts-lab-bench/profile_timeseries_rate_cpu.py` and workflow `.github/workflows/ts-lab-rate-cpu-profile-20261102.yml` commit `f0a72709`; run **41417490871** started to sample `system.trace_log` CPU stacks for baseline native `sum(rate)`, grouped rate and exact host. It records an explicit error when profiler samples/symbols are unavailable. Status must be checked.
- An existing native selector path in `StorageTimeSeriesSelector.cpp` already implements `tryMakeWholeMetricIDRangeConditions` with an exact ID-set check and a counterexample probe, **but only for non-bucketed samples**. The source explicitly avoids the probe for bucketed samples because the set index is expected to be cheaper. Do not treat full-metric range pruning as a novel design without evaluating this built-in path.
- Important next step: inspect completed benchmark/profile raw evidence. Reject the ring if it regresses normal rate or grouped queries; do not hide A/B/A drift. Then select a source-level fix based on observed CPU symbols.

Links: https://github.com/moleus/ClickHouse/actions/runs/41416547876 ; https://github.com/moleus/ClickHouse/actions/runs/41417443992 ; https://github.com/moleus/ClickHouse/actions/runs/41417490871 .

## Completed findings and new adaptive experiment (2026-11-02 ~09:05 UTC)

1. Inline rate queue C++ build **41416547876 SUCCESS**, 5 original + 8 extended PromQL smoke tests PASS.
2. Corrected A/B/A **41417443992 SUCCESS** (script bug in first failed run 41416918067 fixed):
   - All 10 Jenkins/dense query outputs equal original ClickHouse; all eight 5m/30m/1h, 15s/60s/300s and increase/delta variants passed value checks.
   - Jenkins all: baseline repeat 791.637ms versus inline 931.188ms; tracked memory p95 ~544MB versus 550MB. The query does not use rate; this regression suggests code layout/runner noise and invalidates global improvement claims.
   - Dense sum(rate) baseline recheck 100.645ms versus inline 105.709ms; by team 127.982 versus 135.376ms; by region 128.98 versus 136.848ms. **REJECT inline queue as default**. Keep lab script for evidence.
3. Original ClickHouse CPU sampling run **41417490871 SUCCESS** (24 repeats) on 20,000 dense series:
   - rate_sum p50 111.508ms, team grouped 148.008ms, exact host rate 14.078ms; do not compare across different runner conditions.
   - system.trace_log: rate sum 8,564 stack samples; 5,556 include `AdaptiveBucketMergingSource::generate` and 5,549 include `Aggregator::mergeAndConvertAdaptiveBucket`. Team 11,460 total; 7,117 and 7,105 include these frames. **These are inclusive stack frames, not exclusive CPU attribution**. The adaptive merge looks important; inspect its leaf frames and test alternatives.
   - Source `src/Core/Settings.cpp`: `enable_adaptive_aggregator` true by default; default freeze at 16,384 keys or 4MiB. `src/Interpreters/AdaptiveAggregation/AdaptiveAggregation.h` documents staged partitioned merge.
4. A/B/C/D/A SQL benchmark script `.github/ts-lab-bench/bench_adaptive_aggregation_sweep.py` commit `466dabcfcf6a88520144680a240fe14494910583`, workflow `.github/workflows/ts-lab-adaptive-aggregation-sweep-20261102.yml` commit `e70601f7f2f28ea3e63c00842a3a58861850063b`. Run **41418262715** started. Five variants: default-before, disabled, early-freeze (512/1MiB), late-freeze (65536/0B), default-after. Six workloads: broad & selective Jenkins, dense rate sum/team/region and exact host. Exact output checks + p50/p95 + memory/read_rows/read_bytes + AdaptiveAggregationDrainedRecords from query log. Check completed result before interpreting. Never blindly change `enable_adaptive_aggregator` globally.

## Completed adaptive GROUP BY sweep (run 41418262715, SUCCESS)

Script: `.github/ts-lab-bench/bench_adaptive_aggregation_sweep.py`; same pinned baseline binary, identical datasets, same-run default-before/disabled/early-freeze/late-freeze/default-after; six workloads all produced exact same original ClickHouse labels/timestamps/values (5 repeats after warmup).

p50 milliseconds (default recheck -> best candidate):
- Jenkins 282,036 short series broad avg: 1778.997 -> 1668.994 with early freeze (512 keys, 1MiB), ~6.2% faster. Disabled 2106.327 (~18% slower).
- Dense sum(rate): 526.195 -> 510.754 with adaptive disabled, ~2.9% faster.
- Dense sum by(team)(rate): 587.746 -> 559.819 disabled, ~4.8% faster.
- Dense sum by(region)(rate): 590.817 -> 554.6 disabled, ~6.1% faster.
- Exact stage and host were insensitive to these changes.
- Original-before/after p50 close in each case. Thus adaptive merge has an inclusive CPU cost but globally disabling it regresses broad highcard. **Do not change global settings**. Selectivity/cost and exact semantic guard would be required before a query-specific plan.

Source: `src/Interpreters/AdaptiveAggregation/Merge.cpp` function `Aggregator::mergeAndConvertAdaptiveBucketImpl` partitions staged records and merges source cells; `src/Processors/QueryPlan/AggregatingStep.cpp` requires multistream, grouping keys and no ordered-rows property. It is not solely extra overhead: it performs actual aggregation work, explaining why inclusive CPU stack sampling overstated its optimization opportunity.

Link: https://github.com/moleus/ClickHouse/actions/runs/41418262715 .

## Source patch: verified whole-metric ID range for large bucketed `TimeSeries` (new)

- Pinned source `src/Storages/StorageTimeSeriesSelector.cpp` already has `tryMakeWholeMetricIDRangeConditions`: the range is a primary-key `indexHint`, while the exact `id IN (SELECT ... FROM tags)` remains the row-level filter. It probes all time-eligible matching tags for any ID outside the canonical metric hash prefix and for non-matching labels; supports exact/regex name selection, custom shifted IDs fallback, and physical ID type validation. Currently the source only invokes it for **non-bucketed** samples.
- New candidate script `.github/ts-lab-pending/apply_bucketed_whole_metric_range_cpp.py` commit `63cdbc2e92533f78d43b4c3bb6db8854a3d804f6` enables this existing, guarded source path for bucketed SAMPLES targets with >=65536 rows, using the original exact filter and safety probe. It does not change storage format, roles, query executor or inserts.
- Build workflow `.github/workflows/ts-lab-bucketed-metric-range-cxx-20261102.yml` commit `9eb4add402f8992bd246d6b8e7811ffbd9500d3e`, run **41418972822** started.
- A/B/A original/candidate/original benchmark workflow `.github/workflows/ts-lab-bucketed-metric-range-aba-20261102.yml` commit `c8662741878a31078b2ab502dbbb8d81c400849c`, run **41419049171** started, using `.github/ts-lab-bench/bench_bucketed_metric_range_aba.py` and the original Jenkins/dense inputs. Includes full metric, narrowed label selectors, exact VM Jenkins oracle, rate correctness and memory/read rows.
- **Do not claim a speedup until the C++ binary compiles and the A/B/A completes.** The 65,536-row threshold is only a controlled hypothesis; running the counterexample probe might cost more than it saves. Compare narrow/old-shifted IDs and increase in query memory. Keep fork lab-only.

Links: https://github.com/moleus/ClickHouse/actions/runs/41418972822 and https://github.com/moleus/ClickHouse/actions/runs/41419049171 .

## Bucketed whole-metric ID range: REJECTED, A/B/A finished (run 41419049171)

- C++ build **41418972822 SUCCESS**; default 13 PromQL regression tests passed.
- A/B/A run **41419049171** completed *all* 3 phases (original, candidate, original), all 10 Jenkins/dense query outputs matched exactly. Workflow marked FAILURE only because the *final inlined Python summary script* contained an unterminated quoted string. Raw `METRIC_RANGE_QUERY` for every phase and artifacts were uploaded. This is not a correctness mismatch and no reason to repeat the expensive build.
- p50 candidate vs original repeat: broad Jenkins **966.296 vs969.285 ms** (not meaningful); filtered branch **485.387 vs318.217 ms**, project **257.346 vs139.052 ms**, exact stage **216.028 vs102.784 ms**, dense sum(rate) **177.005 vs174.573 ms**, dense group team **222.737 vs214.82 ms**, group region **222.044 vs216.948 ms**. Big regressions on narrow selectors.
- Causal source: `tryMakeWholeMetricIDRangeConditions` executes an extra tags/time-range counterexample probe even when label filters guarantee the whole-metric fast path fails, so the candidate pays probe cost before original fallback. Broad matching does not gain enough to justify enabling the path for bucketed data.
- **REJECT the C++ candidate**; keep `apply_bucketed_whole_metric_range_cpp.py` as experiment only; do not enable in production or change the existing non-bucketed path.

## Permission-aware grouped AVG SQL prototype launched

- Source evidence: `timeSeriesSelector` already resolves tags/IDs using `timeSeriesStoreTags` to populate the query's collector; `timeSeriesIdToGroup` maps IDs to groups; `timeSeriesRemoveAllTagsExcept` keeps the required grouping label. It may be possible to compute per-ID scalar avg and only then map to group through the collector, avoiding direct reads of internal SAMPLES/TAGS and making regular label filtering available.
- Created experimental `.github/ts-lab-bench/bench_selector_scalar_grouped_avg.py` commit `2fd86cebc580dbf767886d5785cdb0801b22c6c5` with five selectors and native-original/native-recheck correctness, per-query memory/read_rows. **This is SQL only**, not a new C++ engine patch.
- Workflow `.github/workflows/ts-lab-selector-scalar-grouped-avg-20261102.yml` commit `f46c8ef9dbc84ae0ca2b74f41fcf726ae9e6787b`; run **41419685211** started. If SQL fails, inspect the first error and fix it; otherwise record exact A/B/A p50/p95, memory and read rows and only then decide if C++ integration is justified.
- The query passes `timeSeriesSelector` instead of direct inner targets; this is *structurally better for authorization*, but still requires explicit access-policy tests before production claims.

## Secure selector-based scalar grouped AVG SQL: SUCCESS (run 41419685211)

- The SQL proof `.github/ts-lab-bench/bench_selector_scalar_grouped_avg.py` reads via `timeSeriesSelector` and resolves stage grouping using `timeSeriesIdToGroup` and `timeSeriesRemoveAllTagsExcept`, not via direct internal TAGS/SAMPLES access. Its normal table-function query retains the existing ID selection and tag collector. Stored source data is unchanged.
- **41419685211 SUCCESS**. Five selector cases, native before / scalar selector / native after, all labels/timestamps/values exactly equal original ClickHouse in the script's tolerance.
- p50 milliseconds candidate vs native after: Jenkins broad **633.703 vs777.992** (~18.5% faster), branch_main **257.021 vs303.124** (~15.2% faster), project00 **130.037 vs124.296** (~4.6% slower), exact stage **85.972 vs88.314** (~2.7% faster; likely noise), regex stage10 **120.324 vs106.128** (~13.4% slower).
- Therefore use a **narrow query-shape guard** if integrating this into the C++ PromQL converter: exact single metric selector, `avg by(stage)(avg_over_time(metric[1d]))`, disjoint evaluation windows, version 11, DateTime64(3) Float64 schema, no offset/subqueries/binary, no extra filters (for the first safe experiment). Others must use unchanged original path. Do not make a hard-coded optimization for branch naming.
- Sample values were checked against original CH; additional adversarial NaN / staleness / shifted times, 14–90d backdelta and access-policy checks remain required. This is not production-ready.
- Link: https://github.com/moleus/ClickHouse/actions/runs/41419685211 .

## Guarded C++ lowering through `timeSeriesSelector` (work in progress)

- Script `.github/ts-lab-pending/apply_selector_scalar_grouped_avg_cpp.py` commit `e4b2905aeb2592f4d6ec0de30f35030643415a22`. It changes *only in the pinned source checkout during CI*:
  - `PrometheusQueryEvaluationSettings.h`: experimental eligibility flag, default false;
  - `StoragePrometheusQuery.cpp`: verifies v11 Float64 value type via actual TimeSeries schema, sets flag;
  - `PrometheusQueryToSQL/Converter.cpp`: guarded AST recognition for `avg by(stage)(avg_over_time(exact_metric[1d]))`, daily disjoint steps, UTC/millisecond scale, numeric-safe metric/table/database names, no other matchers, no nested subqueries, `@/offset`, or expression operators. Compiles the verified selector scalar SQL; every unsupported shape falls back to original converter.
- The candidate only reads using `timeSeriesSelector` and the query-scoped tags collector; no raw inner target-table references. It retains the engine's actual label lookup/ID safety model, but production security policy still requires review.
- Build workflow `.github/workflows/ts-lab-selector-scalar-cpp-cxx-20261102.yml` commit `66b59e250f2096dd4abddc9893f80642024bcb32`, run **41420384277** started.
- A/B/A workflow `.github/workflows/ts-lab-selector-scalar-cpp-aba-20261102.yml` commit `e4fed87e4eab3f97dd88efca01a6ba01b21e3b76`, run **41420507321** started, with full Jenkins 6 + dense 4 cases against original and original recheck, labels/times/values and read_rows/memory.
- The guard intentionally does not optimize project/regex/branch filters, because the SQL prototype regressed on project/regex and hardcoding branch is unacceptable. C++ correctness/adversarial/ABI tests and performance are **pending**; do not claim improvement until both complete. Candidate contains no production default change outside the standalone CI binary.

### Additional C++ adversarial guard test

- Added workflow `.github/workflows/ts-lab-selector-scalar-cpp-corner-20261102.yml`, commit `887f577245f0ebabf403c3702c450a9aeeb5206b`, run **41420683218**, reuses `.github/ts-lab-bench/bench_fused_corner_cases.py` for original/candidate/original on one on-disk 210-series synthetic dataset with duplicate timestamps, multiple samples, missing/empty stage, regex, offset, absent metric, out-of-order.
- It downloads verified binary artifact from build **41420384277** and compares value equality for all cases. Do not confuse an unused fastpath fallback correctness test with actual speed: current main C++ guard optimizes the exact-metric broad `avg by(stage)(avg_over_time(...[1d]))` only; all other filters must stay native.

## Guarded selector scalar C++ source correctness results (2026-11-02, latest)

- C++ build **41420384277 SUCCESS**: generated binary based on `52529f0c...` with `apply_selector_scalar_grouped_avg_cpp.py`, pinned LLVM22 and ccache; PromQL smoke passed.
- Synthetic adversarial original/C++/original run **41420683218 SUCCESS**: ten cases passed exact values/labels/timestamps (duplicate timestamps, multiple samples per series per day, missing labels, regex, day offset, overlapping windows, 6h/36h windows, absent metric). Only the exact root one-day/stage case activates the new planner; others test fallback. Small 210-series `by_stage_1d` candidate p50 18.192ms vs original recheck 16.026ms, so a cost-based minimum table-size guard may be needed.
- Main same-data 282036-series Jenkins and 20K dense A/B/A **41420507321** still active at this checkpoint. **Do not promote without finished large result**. Directly optimized small queries may regress due extra SQL stages, and even exact small queries need fallback.
- Detailed storage/index and plan diagrams at `.github/ts-lab-bench/SELECTOR_SCALAR_GROUPED_AVG_EXECUTION_20261102.md`, commit `6fe3275e9b7405dfb9b21c95b1ef0a0204f3a569`.
