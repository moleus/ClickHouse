# TimeSeries v11 engineering measurements, 2026-11-01

Scope: fork `moleus/ClickHouse`, branch `lab/timeseries-fastpath-ci`. Data in CI artifacts; **never extrapolate single-node numbers to prod without controlled tests**.

## Objective

Match or outperform VictoriaMetrics 1.134.0 at: 13.4M–13.6M live series per hot region, 650K–800K logical samples/s, ~539 new series/s churn, high cardinality, short selective queries, 14–90-day sparse history, HA scrape dedup. Evaluate latency p50/p95/p99 and OOM, CPU, RSS, storage/merges, ingest, and numerical correctness.

Source data: user-supplied `REPORT.md`, `MASTER_V11_REPORT.md`; local synthetic workloads (not production ingress). ClickHouse pinned source commit `52529f0c711566d696917678bf15e7068772fdde`, LLVM 22.1.8. VictoriaMetrics at `v1.134.0`.

## Verified completed CI experiments

- `41289735046` C++ build succeeded at `8db1f4bc82e426ddedbd209a952ee94b1cdb5192`, artifact `13080944100`, outer SHA256 `02be86e9c92d3984a3574949a07ad2f7c9ab50a98c9fd5e732abaf1025ee7b0c`; smoke failed because ANTL4 disabled. Compile cache saved with key ending `-41289735046`. **C++ binary usable for raw SQL but not PromQL parser/selector**.
- `41301314345` new ANTL-enabled build at `f0e5d949afb947e5dcf7a6090430979d7bc03ae9` was still compiling at 14:49 UTC; do not cancel.
- `41303993610`, sparse-grid C++ four-object compile succeeded; cached object compile step 28.26s, not full build/link. Proposed sparse patch `.github/ts-lab-pending/apply_sparse_grid_patch.py` **still calls `writer.addRow()` and `writer.reserve(batch_count * grid_size)`**, so it does NOT remove per-series dense-grid allocation. It skips calculating empty prefix/suffix; CPU optimization only until proven otherwise.
- `41301999012` CMake object/cache experiments succeeded; cache-reader 16/16 100% direct ccache hits in separate GitHub Actions runner. Do not confuse single-TU compile with full linked executable.
- `41304150262`, 12k series, 144k samples across 12 batches: tag cache 64KiB with recent=OFF 2.393s / 12.834MB data / 23545 tags rows. Cache 16MiB recent=OFF 1.982s / 7.767MB / 12000 tags rows. With recent=ON 2.494s / 11.964MB; these timings are 12 short CI loopback batches, not steady-state ingest capacity.
- `41317830016`, commit `041a024514d665387b0c94dafbb277d4128063a6`: same-run interleaved direct-samples SQL vs VM benchmark on GitHub-hosted runner, 12 measurements after 4 warmups; unpatched ANTL4 parser unavailable so SQL targets inner `.inner_id.samples.*` directly; data identical.
  - 5k series, 600k samples: VM 13.240 ms p50, 15.768 ms p95; ClickHouse `sumForEach(timeSeriesRateToGrid(...)) GROUP BY id`, one thread 230.228 ms p50, 247.121 ms p95 (**17.39x VM**); four threads 230.880 ms (**17.44x VM**). CH grouping by tuple components 231.135 ms.
  - 50k series, 6M samples: VM 100.785 ms p50, 123.454 ms p95; CH `GROUP BY id`, one thread 566.370 ms p50, 574.045 ms p95 (**5.62x VM**); four threads 567.599 ms (**5.63x VM**). By tuple components 560.685 ms p50, **not robust evidence of improvement**.
  - Numeric maximum relative differences: 5k 3.00012e-5; 50k 3.00001e-6; VM MetricsQL `rate` and CH PromQL-like `rate` have distinct semantics, so not a strict equivalence test. All four CH group variants passed consistency checks.
  - Artifacts: 5k `13088291499` SHA256 `bf49896c3c021b91a5b73dd1b9c5393bb24bc6f743f67402b5e189648ef09ae6`; 50k `13087883347` SHA256 `4f988071fb1c0b212270588f782ad35c6911312d81754cd9ec7857606e61df54`.
  - Neither `GROUP BY` representation nor `max_threads=4` improves this query materially. The bottleneck exists even without label-selector and PromQL parser.

## Active experiments

- Main ANTLR-enabled C++ build `41301314345`; sparse-grid full C++ build `41304416884` waits for main; long14 native benchmark `41304682666` waits for main; direct SQL benchmark `41303584630` waits for main.
- New direct-SQL decomposition workflow `.github/workflows/ts-lab-sql-ready.yml`, triggered on changes to `.github/ts-lab-bench/bench_inner_rate.py`, uses ready old binary and VM, isolated matrix for 5k/50k. New script measures `scan_rows`, `decode_samples`, `hash_ids`, `group_ids` before `rate` so we can locate the cost. **Check latest run ID and logs for final results, not assumed results**.
- All workflows isolated concurrency and `cancel-in-progress:false`; never add triggers to main during long compile.

## Source facts and hypotheses

1. ClickHouse `src/AggregateFunctions/TimeSeries/AggregateFunctionTimeSeriesResultWriter.h` `reserve(rows_count)` reserves `rows_count * grid_size` numeric and null-map cells, and `addRow()` resizes dense grid. `AggregateFunctionTimeseriesBase.h` `insertResultIntoBatch` reserves full output; `doInsertResultInto` enumerates all grid indices (unless sparse patch skips calculation, but **not allocation**). For 44.8k history x 20,145 steps: 7.56 GiB logical Float64+null cells if all emitted. OOM on 14-day 1m/5m steps at 1.2GB CH query limit, while VM succeeds.
2. ClickHouse PromQL converter `applyFunctionOverRange.cpp` maps `rate` to `timeSeriesRateToGrid`; `applyOneArgumentAggregationOperator.cpp` maps `sum` to `sumForEach`; the existing converter-only patch removes a redundant intermediate aggregation for exact single-metric ungrouped `sum(rate())`, but doesn't eliminate dense rate arrays.
3. VM 1.134.0 `app/vmselect/promql/rollup.go`: `rate` maps to `rollupDerivFast`, using previous sample when present and first/last slope; different from PromQL extrapolation. `lib/storage/index_db.go`: tag-to-MetricID posting lists and per-day (date, tag) to MetricIDs indexes, useful for high cardinality and churn. ClickHouse TimeSeries `StorageTimeSeriesSelector.cpp` resolves tag IDs via internal tags tables, includes a whole-metric primary key fast range for some selectors; indexes and row-granule effects must be measured on actual matching sets.
4. HA duplicates shifted 2.2s retained by CH vs VM with 15-second dedup. Correctness blocker for four vmagent replicas.
5. Need fused streaming grouped rate evaluator avoiding O(series * all_steps) output, or bounded chunking with correct stitching at rate windows and no double counting. Measure a range of sparse series lifetimes, step=60/300/900s, and verify exact time/value semantics across chunk edges.
6. Target warm C++ edit/test <=2–3 min; restore source submodule metadata, ccache/objects and LLVM; time full checkout/cmake/object/link separately. 28s C++ object compile verified only; full iterative link not verified.

## Safety

Only write `lab/timeseries-fastpath-ci`. Do not push master or upstream, create PR, cancel active runs, or change central build triggers while compiling. Never claim that queued/in-progress jobs passed. For failure read complete finished job log; preserve exact run ID and SHA256 of artifacts. Hourly autonomous task ID `6ae6f0c0fe9081919f499187f801dd8c` is enabled and requires launching >=2 independent useful experiments per iteration when feasible, not status-only summaries.


## Continuation: 15:05 UTC 2026-11-01 — verified results and active hypotheses

### Successful warm full binary builds

- `41318355575`, commit `805dd851846d36c35d52711e1703d832c1347528`, SUCCESS. Reused pinned submodule metadata and `ccache`, **7,103/7,104 new compiler requests hit (99.99%)**, complete linked ClickHouse artifact `13088561213`, 13/13 deterministic PromQL regressions PASS. GitHub prerelease is unavailable (workflow treats 403 as nonblocking). The previous ANTL-enabled full compile `41301314345` required **201 minutes**, only 2,850/14,066 compiler cache hits, and passed the first 5 regressions.
- `41304416884`, sparse-grid C++ patch binary commit `dd152ce17dbe9738a14f029078c2c5afa74c8ae2`: **132 seconds** for `ninja clickhouse` under warm cache (after setup), 7,089/7,104 new compiler cache hits, 13/13 regressions PASS; artifact `13088397010` SHA256 ZIP `8b51ca06082a0ef915af6254d8ca865ac9442456927dd8c13ccac573e5e98f17`; workflow overall FAILED only release 403. This patch skips empty grid calculations but still allocates full dense arrays; **not memory-complexity fix**. The 132s compile time is not the entire workflow's checkout/LLVM/CMake time.

### Sparse-grid and 14d benchmark

- Baseline `41304682666`, commit `2c42cec28a5ef5559e3276d4b80d8b955020a5d0`: 44,800 historical series / **32,256,000** samples; `sum(rate(lab_node_network_receive_bytes_total[5m]))`; VM p50 15m 336.283ms / 5m 783.643ms / 1m 3508.972ms; CH (converter patch, no sparse optimization) p50 **760.005 / 2723.134 / 10091.662ms**. All 200 at 1.2GB per-query limit; peak CH query_log tracked memory **267,225,516 bytes**; max read rows 268800, no query errors. 1m output 20155 points.
- Sparse-grid version from `41318649849`, commit `48923617b6f755c8f8fc68a2664f4f0e1f4abaf7`: same deterministic 44,800 series/32.256M samples on DIFFERENT runner. VM p50 15m 460.149 /5m 1097.731 /1m 5001.163ms; CH sparse p50 15m 840.345 /5m 2718.707 /1m **5359.916ms**. Unpaired CH 1m 10091.662->5359.916 = 1.883x faster, but VM in same run also changed 3508.972->5001.163ms (1.425x slower). **NOT a reliable isolated sparse speedup** until same-run A/B. Sparse code still allocates dense arrays per output series.
- Matched A/B/A workflow `.github/workflows/ts-lab-long14-paired.yml` run `41319232637` compares baseline -> sparse -> baseline recheck on same runner and exact SAME already ingested ClickHouse TimeSeries data, with 13+ points of numeric equivalence and 14d 1m/5m/15m speedup. Inspect finished logs; fix any startup/shutdown or consistency failures.
- Experimental chunked query `.github/ts-lab-bench/bench_long14_chunked.py`, independent workflow `.github/workflows/ts-lab-long14-chunked.yml`, compares one raw-inner-table `sum(rate())` across 1h/3h/12h bounded grids to native full 14d PromQL on **every evaluation timestamp and numeric result**; note this is NOT a general production-safe parser. Check its logs and fix test failures; if effective, implement a correct native ClickHouse bounded-grid operator.

### Numerical semantics is NOT evidence of a C++ correctness regression

- `41318689745` artifact reviewer (job `124884104810`): on baseline long14 1m, exactly **444/20,155 points (2.2029%)** disagree; at each every 3-hour node-lifetime switch, four 1-minute points differ by exactly 50%: CH sum ~400, VM MetricsQL sum ~800.
- `41319131971`, commit `7301ac685f253eaba706a4079bdc43dd19795a2e`, **targeted 2 slots x2 generations** proved why: VM `rate()` maintains slope 1.0 on both old and new series for first 4 minutes so sum is 4.0 rather than steady 2.0; CH PromQL `rate()` computes extrapolated falling/rising per-series rates (e.g. 0.775 old + 0.225 new) so sum remains 2.0; VM `rate_prometheus()` returns different boundary values (around sum 1.8). This is a MetricsQL vs PromQL semantics difference, **not proved C++ bug**. Other 19,711 points matched baseline within ~1e-16 relative tolerance.
- Source: VM `app/vmselect/promql/rollup.go` uses `rollupDerivFast`; CH `timeSeriesRateToGrid` implements PromQL extrapolation. All speed acceptance tests must say which semantics they benchmark.

### Direct sample-stage bottleneck and part compaction negative result

- `41318047417`, commit `0ce9500997e67ee0dac2053beea1665674f1af15`, direct v11 TimeSeries samples aggregation without PromQL parser on 50k series /6M samples: raw count 7.383ms; sum(length(samples)) 9.663ms; `uniqExact(id)` 82.638ms; `GROUP BY id` 89.915ms; `timeSeriesRateToGrid` + sumForEach ~2,293.123ms (one worker), while VM p50 124.052ms. On 5k series CH rate ~56.915ms, VM ~9.840ms. Earlier run `41317830016` saw CH rate 566ms vs VM100.8ms for 50k; different runner/runtime = ~4x variance, do not attribute cause without within-run AB.
- `41318822378`, paired `OPTIMIZE FINAL` test on 50k series: samples table had exactly **ONE ACTIVE PART** *both before and after*, 150000 stored rows, 17.364MB active. `GROUP BY id` raw-grid rate: 2,396.916ms before and 2,305.015ms after (3.8% faster); 5k baseline 67.287 vs 66.322ms (1.4%). **This experiment cannot establish that part count caused prior variation**, as it had 1 part in both phases. DO NOT recommend `OPTIMIZE FINAL` in prod.
- Query-script process RSS and CPU previously pointed to `ClickHouseWatch` guardian PID, not the ClickHouse server worker (CPU 0.01 etc). **RSS/CPU for ClickHouse from those runs are invalid** until benchmark script follows child PID or uses server telemetry. `system.query_log.memory_usage` is a tracked per-query metric, not full process RSS.

### Current action list for autonomous worker

1. Follow up `41319232637` (paired A/B/A) and latest bounded-grid workflow. Read complete failed/successful job logs, quantify paired speedups, memory and correctness. Repair failures by pushing only independent workflow/script changes.
2. If bounded-grid SQL passes and beats native, write a safe C++ converter/executor prototype for `sum(rate())` with bounded grids, preserving out-of-order/reset/gap/offset and group semantics. Test 5k, 50k, long14, highcard, 1m/5m/15m steps. No production claims before compatible MetricsQL/PromQL behavior and 15s HA dedup tests.
3. Test a staged compiler/link cache with actual elapsed checkout/submodule/LLVM/CMake/object/link times, not just Ninja duration; warm compile target <=2-3min, baseline vs candidate pinned and matched compiler flags.
4. Do not stop at CI status. Hourly autonomous task `6ae6f0c0fe9081919f499187f801dd8c` must launch >=2 meaningful independent experiments/hypotheses per run unless truly blocked. No master/upstream/PR, no cancellation of long runs, no false claims about prerelease.

## Continuation: 2026-11-01 autonomous iteration — bounded widths and adaptive A/B/A

Two independent new CI workflows committed only on this lab branch, each `push` path-scoped to its own file and `cancel-in-progress:false`, with different per-run concurrency groups:

1. Adaptive C++ hypothesis: threshold=2048 preserves sparse long14 1m acceleration and eliminates 15m sparse regression while preserving all 20155 output values. Matched baseline/adaptive/baseline A/B/A on the same persistent dataset, VM baseline, grid steps 60/300/900, exact 1m array comparison. Workflow `.github/workflows/ts-lab-adaptive-paired-20261101.yml`; first run `41332753647` / commit `69c64e39b89a1546a63c23fc69f4db827d2705e2`. Adaptive full C++ compile `41319688851` / source fork commit `6648c1f0f1a6fbf97c7941c015953efbb2083a16` succeeded, 13/13 regressions PASS, artifact `13088444544` ZIP sha256 `4f6e4f5fa324b2a0e7f9e2eafe6f6a9d785a7198016b9470923b29b9319dc6d6`; ccache and pinned submodule metadata cache hits. Need finish same-run A/B/A and highcard before accepting.
2. Bounded memory/CPU hypothesis: grid width 720-2880 points avoids full long14 dense allocation and amortizes repeated queries better than <=180 widths. Workflow `.github/workflows/ts-lab-long14-width-sweep-20261101.yml`; first run `41332762159` / commit `560751823e4d668b5437e2f9dc1265e3fd281feb`; candidate widths 360/720/1440/2880, native full-range control, warmup=1, repeats=3, all 20155 values required equivalent.

Completed source experiment `41319478369` / fork commit `ddfcd42ffb2fef3a44f28a008de00441488e95e7`, full job log read: long14 44800 historical series / 32256000 samples, exact 20155 point output equivalence at all bounded widths (mismatch_count=0, max_relative=3.6948222259525156e-15). Native p50/p95/p99=15940.980/15984.011/15987.836ms. Width 60: 38888.845/39392.409/39437.171ms (336 HTTP requests). Width 180: 16558.799/16694.907/16707.005ms (112 requests). Width 720: 6676.908/6679.534/6679.768ms (28 requests), 2.387x p50 speedup over native. This SQL prototype is NOT general PromQL and measured timings are from its own runner, not matched to sparse binary A/B/A. Artifact `13088926571`, GitHub ZIP sha256 `08818129ce9b7a7b1841a229ccd764e7792b821c1fb36a39b842435d19635257`.

The pinned main build `41301314345` reached compiled binary and artifact `13088430930` ZIP sha256 `84066302b43744c9f7db0d29b79ac24bbf1023f4100037ba0292d149a6195320`; overall workflow failed at optional prerelease HTTP 403, not compiler. Full log of the adaptive build was inspected; this does not imply access to in-progress job live logs. Need read fresh completed job logs for two new experiments, annotate p50/p95/p99, memory, read_rows/bytes and correct C++ worker PID, then iterate.

Root `REPORT.md` and `MASTER_V11_REPORT.md` are absent (GitHub contents 404 on this branch); earlier original reports were user supplied, referenced in these notes. Do not claim their full files were read in this run.


## 17:50 UTC 2026-11-01: completed follow-up runs and source-level findings

### Completed A/B/A adaptive C++ benchmark

- `41332753647`, commit `69c64e39b89a1546a63c23fc69f4db827d2705e2`, SUCCESS. Same runner, exact same 44,800 historical series/32,256,000 samples, 14d, baseline->adaptive->baseline. CH p50 (baseline/adaptive):
  - 15m step (1,344 grid points): **784.914 / 901.016 ms** (adaptive slower; threshold 2048 should take normal path, so test variation suspected).
  - 5m step (4,031 points): **3024.670 / 1675.055 ms** (1.806x).
  - 1m step (20,155 points): **10665.631 / 2701.738 ms** (3.948x); baseline recheck **6647.754 ms** (2.461x vs recheck). VM in adaptive phase **3871.084 ms**, candidate 1.43x faster on this single sparse long14 workload.
  - Baseline/adaptive/recheck CH 1m values numerically identical. VM MetricsQL `rate()` differs by 50% at 444 boundary points (known semantic difference).
- `41332905200`, commit `55c537be993fac1ecd3b0edc9ec43133d9d7d561`, SUCCESS. Dense 60-step `sum(rate(bench_metric[5m]))` native CH, same data same runner, 5k: baseline 54.700ms -> adaptive 54.617ms -> baseline recheck 58.014ms; 50k: baseline 901.824ms -> adaptive 910.325ms -> baseline recheck 2452.382ms. All CH results equal; adaptive keeps normal algorithm for <2048 points. **Huge 2.7x drift on 50k baseline recheck** means CH background state/query cache/merge/CPU needs controlled profiling. VM stayed ~133ms on same run. Do not infer regressions below >10% without stable A/B/A.
- `41332818743` initially FAILED at unrelated `du -sb` during sample ingest because ClickHouse removed a `tmp_merge_*` directory during traversal; source data fully inserted. Fixed only dedicated lab workflow `ts-lab-long14-adaptive-paired.yml` commit `56cc61051a5d955f8a5b106d049f6c44497175a6`. Repeat `41333087397` completed SUCCESS; inspect metrics if needed.

### Bounded-grid width response curve (pointwise exact)

- `41332762159`, commit `560751823e4d668b5437e2f9dc1265e3fd281feb`, SUCCESS, 14d 44,800 series, 20,155 result points, 3 timed trials, same CH binary/runner; **ALL bounded variants have 0 mismatched values** vs native with max relative error <3.7e-15.
  - Native single-grid query p50 **10,823.875ms**.
  - Width 360 (6h), 56 HTTP subqueries: **7,964.306ms** (1.359x faster).
  - Width 720 (12h), 28 HTTP subqueries: **5,185.108ms** (2.087x).
  - Width 1440 (24h), 14 HTTP subqueries: **3,880.205ms** (2.790x).
  - Width 2880 (48h), 7 HTTP subqueries: **3,443.640ms** (3.143x).
  - Earlier `41319478369` width 720 on another runner 6,676.908ms vs native 15,940.98ms (2.39x); 60/180 steps were slower. Need next sweep 3d/4d/7d windows, plus per-query max memory, CPU, read_rows to optimize total runtime without OOM.
  - This is a SQL client batching prototype; it does not eliminate native per-series dense `timeSeriesRateToGrid` arrays and is not production integration.

### Label selector bottleneck and physical index evidence

- `41333026964`, commit `b7f61fa95ec93c13394b83861c89de55cc02f66c`, SUCCESS, full ANTL PromQL on CH vs VM MetricsQL `rate` and `rate_prometheus`, 6 timed repetitions after 2 warmups:
  - 50k series /6M samples /60 output points, p50 CH vs VM `rate_prometheus()`:
    - all: 243.795ms vs 87.083ms (2.80x)
    - metric regex: 308.735ms vs 92.318ms (3.34x), matches exact CH result.
    - region=east: 172.973ms vs 44.515ms (3.89x)
    - team=t00: 81.928ms vs 3.275ms (25.02x)
    - host=h0001 (exact ONE series): **16.557ms vs 0.341ms (48.55x)**.
    - group by region: 304.357ms vs 86.881ms; group by team 398.638ms vs 110.688ms.
  - 5k series 600k samples: host CH 19.447ms vs VM-promql 0.450ms; team 31.899 vs 0.923ms; all 71.951 vs 11.915ms.
- `41333445354`, index-query-log artifact audit from same runs (SUCCESS). On 50k series, CH native host query `system.query_log` reports median **58,448 read_rows**, **3,030,849 read_bytes**, p50 server query duration 16ms and max query memory ~6.35MB, despite selecting ONE series. All-series native sum reads 500,000 rows /120,129,976 bytes with 245ms server duration; team=t00 reads 363,040 rows/82,016,616 bytes. On 5k, host reads **10,256 rows** /802,314 bytes. Strong hypothesis: 50k host = 50,000 time-ranges rows + 8,192 tags granule + 256 samples granule; 5k host = 5,000 time ranges + 5,000 tags + 256 samples. These arithmetic decompositions are HYPOTHESES, not independently instrumented source-table reads.
- **Pinned ClickHouse source `src/Storages/StorageTimeSeriesSelector.cpp`**:
  - `matcherToAST` around lines 354–408: exact tag matcher becomes equality on `tags['tag']` or dedicated tag column; literal regex alternatives may become IN, else regex match.
  - `addTimeRangesTableJoin` around lines 486–555: selector tags query LEFT JOINs time_ranges table to select IDs, with `full_sorting_merge` `join_algorithm` pinned in `applySettingsForSelectIDsQuery` around line 1180, `optimize_read_in_order=true`.
  - `makeWhereFilterForSamplesTable` lines ~722–780: after ID set selection, filters samples by time range and `id IN (select from tags)`; `makeSelectQuery` around lines 900–915 has `WHERE notEmpty(samples)` for bucketed samples.
  - `readImpl` around lines 1230–1365: chooses recent or full samples, resolves IDs via tags/time-ranges join, then probes sample table. Primary-key hint fast path is only for old (one sample per row) tables, **not** version 11 bucketed samples.
  - `StorageTimeSeries.cpp` embedded pinned schema around lines 1195–1270: TimeSeries v11 default TAGS ReplacingMergeTree `PRIMARY KEY metric_name ORDER BY (metric_name,id) SETTINGS index_granularity=8192` with `INDEX tags_idx tags TYPE text(tokenizer='keyValuePairs')`, full-part inverted posting lists; TIME_RANGES AggregatingMergeTree ORDER BY id; SAMPLES AggregatingMergeTree ORDER BY (id,bucket) index_granularity=256; recent samples duplication on by default.
  - VM source `lib/storage/index_db.go` holds tag-value to MetricID posting lists and date/tag to MetricID indexes; 0.34ms CH/VM comparison shows a practical gap for high-selectivity labels, but not yet a proven exact cause.
- New isolated A/B/A workflow `ts-lab-timeranges-ab.yml`, commit `5594259b1324c1959c4f73fcf20135f6c6e6f89d`, run `41333353318`. Creates two TimeSeries tables on one runner with identical synthetic samples: `store_time_ranges=true` vs `store_time_ranges=false`, measures 7 selectors baseline/no-ranges/baseline, compares actual timestamp/value output for all/specific labels and group-by-team. This controls one specific proposed source of read-amplification. **Check 50k job completion and read full logs before claiming a speedup.** Note disabling time range target may worsen inactive-series pruning in long history; any optimization must balance that case.
- `ts-lab-selector-log-audit.yml` read-only workflow, commit `5deedc59b4b056c60182fd3ccf1e05bc0977cffa`, run `41333445354`, SUCCESS, collects labels vs read_rows/memory metrics. Important: earlier process RSS sampling targeted `ClickHouseWatch`, not real server child, so prefer query_log and child PID.

### Explicit next actions

1. Get complete `41333353318` 50k A/B/A logs; confirm exact outputs and separate cause of tag lookup costs. If no-timerange makes host fast, test opt-in C++ selector optimization which can skip time-range join for an EQ tag matcher *only if correctness is proved for stale, missing, late, inactive, empty series*. Benchmark on long historical churn before considering default change.
2. Measure precise source scan via `EXPLAIN indexes=1`, per-table query log if available, and query plan for `timeSeriesSelector`; evaluate `tags_to_columns` dedicated label columns/index effect.
3. Bound grid evaluation windows to 48h–7d with p50/p95 and memory; learn optimal adaptation from workload density. Source patch still calls `writer.addRow()` and fills per-step nullable grids, so memory complexity remains.
4. Need production-like highcard 113,284 series /27.32M samples and 13.4M+ live series, 650K–800K samples/s ingest, 539 new series/s, HA dedup 15s; strict numerical semantics comparison vs VM MetricsQL and VM `rate_prometheus`.
5. The hourly Automation ID `6ae6f0c0fe9081919f499187f801dd8c` **was observed DISABLED at 17:40 UTC**, last run 15:34–15:37 UTC `requires_user_action=true`, no additional runs after 15:15 until manual work resumed. Do NOT claim uninterrupted autonomous operation; reconnect/approval may be necessary. Explicitly re-enable only with accurate limitations. It must try working per run, not status-only reporting.


## 17:54+ UTC 2026-11-01 — Time-ranges A/B fully verified; new C++ selective fastpath

### Stable targeted performance finding

- `41333353318` commit `5594259b1324c1959c4f73fcf20135f6c6e6f89d`, **SUCCESS both 5k/50k matrix**, 6 trials per phase. Same runner / same synthetic samples per two TimeSeries tables, time_ranges target enabled (baseline) vs `store_time_ranges=false` (candidate), A/B/A timings and exact CH outputs.
  - 50k historical/live series, `sum(rate(bench_metric{host="h0001"}[5m]))`: baseline p50 **31.934ms**, no time-ranges **17.244ms**, baseline recheck **31.938ms**, **46.0% faster** candidate. Checked exact labels/values/timestamps, 60 points. Complete time-ranges removal is semantically safe for 5 tested queries **on this particular synthetic dataset only**.
  - 5k host: 17.190ms -> 13.130ms ->17.379ms (~24% better).
  - 50k all: 861.024 ->884.548 ->853.402ms (candidate slightly worse); team=t00 145.424->141.278->147.678ms; by team 953.589->954.634->947.511ms. 5k all:54.206->46.459->64.033ms, drift present. This proves no time-ranges JOIN advantage for selective query but broad query benefit is not established.
  - Avoid disabling `store_time_ranges` globally in production: long histories with many inactive series require time pruning. The performance difference could be in merge join/ID resolution; verify with EXPLAIN and source query metrics.

### C++ prototype to preserve time-ranges for unselective queries

- `.github/ts-lab-pending/apply_selector_exact_no_timeranges_patch.py` commit `b4d5adbc675f9c85377e661a0a6d88d7af5dfc34`: source-pinned Python patch for `src/Storages/StorageTimeSeriesSelector.cpp::readImpl` **ONLY**. For v11 bucketed TimeSeries with at least one nonempty exact EQ label matcher other than `__name__`, it bypasses the `time_ranges` join during ID resolution, keeping samples time range & `notEmpty(samples)` filters. For all other queries and for `makeSelectIDsQuery`, retains original path. This is **experimental** automatic heuristic and is not yet proven correct for long-term churn/gaps; do not use in prod. Source contains TODOs for safety.
- `ts-lab-selector-fast-cxx.yml` commit `0122e680556c371c70a0b90370afcc577ce81147`, build run **41333837985**, compiling pending/success check; restored warm C++ ccache from adaptive run `41319688851`. Full compiled artifact expected `tslab-selector-fast-binary-0122e680556c371c70a0b90370afcc577ce81147`, archive `clickhouse-selector-fast-linux-amd64.tar.zst`, SHA256 manifests, 13 PromQL regressions.
- `.github/ts-lab-bench/capture_native_selector_outputs.py` commit `4d454ca53f2eb1a537665f561462c3d17aa46d12` captures exact 7 native selector outputs, tags, all timestamps and float values; gives query_log `log_comment` per phase/case to separate table read_rows/bytes.
- Pending workflow `.github/ts-lab-pending/ts-lab-selector-cxx-aba.yml` commit `d0481c5879450f10984ada27f5d592f4a77d7570`; it is NOT ACTIVE until moved to `.github/workflows/ts-lab-selector-cxx-aba.yml`. It reuses finished C++ artifact from **41333837985** and compares baseline/candidate/baseline full native PromQL 7 selector queries for 5k and 50k series, numeric labels/timestamps and query_log read_rows, once source C++ build SUCCESS. Do not activate before compiled artifact exists; no need to burn runner on idle polling.

### Two independent schema variants launched

- `ts-lab-dedicated-host-ab.yml` commit `276fc71d0622cb36e5cbdb3f842969c197432ac6`, run `41334029882`, experiments 5k and 50k. Compares `tags_to_columns=map('host','host_col')` to default TimeSeries: physical `host_col String` vs Map label access; five exact PromQL expressions checked for equality, A/B/A p50.
- `ts-lab-granularity256-ab.yml` commit `877b2df939a6e96a729675d911851993312a2ad8`, run `41334034658`, experiments 5k and 50k. Compares `tags_index_granularity=256` vs default 8192; tests granule vs tag index pruning, same A/B/A correctness checks. Watch for schema/insert failures and fix from complete job logs; results pending.
- Branch-only commits, no PR/upstream or master mutation. Long builds never cancelled.

### Automation

- The hourly automation `6ae6f0c0fe9081919f499187f801dd8c` was found DISABLED after a run with `requires_user_action=true` (last recorded 15:37 UTC). We explicitly **re-enabled** it in the current conversation with a shorter task-specific prompt (2910 chars) requiring >=2 independent experiments, actual code changes, reading GitHub and this journal, and error repairs each run. Schedule unchanged `BEGIN:VEVENT\nDTSTART:20261101T180000\nRRULE:FREQ=HOURLY\nEND:VEVENT` timezone Europe/Moscow. Still cannot guarantee future autonomous work if connector asks user to approve/reconnect; do not claim continuous functioning without verifying next scheduled run.


## 18:05+ UTC 2026-11-01 — selective C++ fast path and schema tests

### Compiled C++ fast path validated on dense samples

- `41333837985`, source commit `0122e680556c371c70a0b90370afcc577ce81147`, **SUCCESS**; on pinned source `52529f0c711566d696917678bf15e7068772fdde`, experimental `StorageTimeSeriesSelector::readImpl` skip time-ranges JOIN for bucketed exact nonempty label EQ, no changes to `makeSelectIDsQuery`. Ninja full binary build+link ~**50 seconds**, warm ccache restored from `41319688851` (24130 cumulative hits/35378 cumulative requests; do not report cumulative as per-build hits). 13/13 PromQL regressions pass. Artifact `13094801178` name `tslab-selector-fast-binary-0122e680556c371c70a0b90370afcc577ce81147`, sha256 ZIP `dff447035985120368bc9b0549cf3a1b977d3f92c4a0b4dce12c83caec128336`.
- `41334320903`, commit `de7b33cf3d8b68a9cc152b04270e6022c36483fe`, **SUCCESS** 5k and 50k native PromQL paired baseline -> patched -> baseline recheck; 7 query cases, all tags timestamps values identical pointwise, source & binary SHA256 checked, read_rows captured via `system.query_log.log_comment`.
  - 50k /6M samples, exact host=h0001: CH p50 baseline **15.097ms**, C++ patched **9.378ms**, baseline recheck **14.751ms** => **1.61x faster**. read_rows **58,448 -> 8,448** (EXACTLY 50k fewer), read_bytes **3,030,849 -> 865,281**. This confirms full time-ranges 50k scan/merge is removed for selected EQ; remaining 8192 tags granule +256 samples granule. All values equal.
  - 5k host: 21.907 ->20.384 ->21.560ms, read_rows **10,256 ->5,256** exactly -5000. 5k team=t00 37.008->33.109->36.198; 50k team 65.186->60.897->63.756 and rows363040->313040.
  - Broad 50k exact name only 205.176->267.068->208.942ms, regex 261.836->338.940->264.931ms despite C++ branch disabled. **VM run on same phases also slows 74.048->90.252->78.582ms for all series**, so systemic runner/load drift or cache effect suspected; do NOT attribute 30% regression to code uncritically. Other grouped broad CH cases ~265ms on both binaries and all read_rows identical. Need randomized A/B/A with more trials, count CPU.
  - Artifact successful 50k `13094078736`, ZIP sha256 `b71c84efdfaadf955855f5adc4eee7eb7521af004b646983208c8464e32e9c94`, 5k `13094457440`.
  - IMPORTANT: production equivalence not proved for inactive long history/holes/stale/late samples. This is a LAB compiled candidate, not production-ready.

### Index/schema hyperparameters (same original ClickHouse binary)

- `41334034658` commit `877b2df939a6e96a729675d911851993312a2ad8`, **SUCCESS** 5k & 50k, changes tags index granularity **8192 ->256**. 50k exact host p50 33.500 (baseline) ->30.867 (candidate) ->31.482ms (recheck), small (~2–8%) and noisy; all-case 895.144->890.875->851.058ms, grouped by team 956.737->991.516->965.726ms (slightly worse). No robust improvement; all tested results pointwise identical.
- `41334264434` commit `87b0e28968e43c1f5611e9df73cf1e1d4bd5fb7f`, **SUCCESS** 5k/50k, physical dedicated host label `SETTINGS tags_to_columns={'host':'host_col'}` (documented Map literal). On 50k host p50 36.528->36.050->35.460ms, **no meaningful win**; 5k host 23.210->20.864->23.946ms (~10% gain but low absolute). Values identical. Initial attempt `41334029882` failed CREATE HTTP400 because `map('host','host_col')` function-call syntax invalid in setting; server log from artifact `13094272428` confirmed Code62 SYNTAX_ERROR at end of CREATE. Fixed to `{'host':'host_col'}`, reran success. No need to optimize separate tag column now unless matching many other labels.
- New **combined CXX patch + 256-row tags granule** test `41334723686`, commit `7ce5475000318ef9037e075e3bc7baba78077078`, runs 5k/50k independently, checks exact outputs. Hypothesis: after removing 50k time-ranges reads, remaining 8192 tag granule dominates; reducing to 256 may reduce remaining read_rows from8448 to~512 and improve 9.378ms latency. Await full jobs/logs; should show `GRANULE_SELECTOR_ROWS`.
- New **boundary/churn C++ correctness** `41334674215`, commit `c4f9d234004fc7dead517dd4410a01951a4bbc57`, uses 2 slots x2 generations 720 samples per lifetime @15s, checks exact, negated, regex, absent, offset and group expression labels+all time points for baseline/candidate/recheck (9 cases) on same persisted TimeSeries data and same runner; await logs for `CHURN_SELECTOR_EQUIVALENCE`. No gap/stale/HA 15s late data tests yet; experiment success alone does not prove prod safety.
- Next: if combined CXX + 256 tags granule faster, quantify per-index memory/storage and write amplification at 500+ new series/s before recommendation. For long history, ensure time-ranges join removal does not cause irrelevant old series to be read/produce incorrect results.

### Automation run caveat

- Re-enabled hourly task `6ae6f0c0fe9081919f499187f801dd8c` with shortened prompt requiring >=2 independent CI experiments each iteration, read this journal, code fixes. Because it previously disabled itself after `requires_user_action=true`, background continuity is still unverified. No long jobs cancelled, only lab branch changes.


## 18:12 UTC 2026-11-01 — completed churn semantics, combined index, planner floor

### C++ exact-label fastpath correctness on short-lived series

- `41334674215`, commit `c4f9d234004fc7dead517dd4410a01951a4bbc57`, **SUCCESS**, job `124931853145`. Baseline -> compiled exact-label selector CXX fastpath -> baseline recheck on same 4-series, 2-generation dataset (2 node slots x2 short-lived lifetimes, 720 samples per lifetime, 15s cadence).
- All **9** native PromQL expressions passed exact tag/timestamp/value comparison (`rel_tol=1e-10, abs_tol=1e-8`): exact old/new instance, ungrouped rate, sum, project label filtering, group by region, nonexistent series, regex, negated matcher and `offset 1h`. Result points vary 0, 179, 180, 184, 355, all identical. Artifact `13095151247`, digest `sha256:729c89856d5a3c1c6940df4d8b548d5c3ea2b3f1713939acbf747a23446e283f`. **Still not a full proof for stale markers, gaps, late/OOO samples, HA 15s dedup, 14d historical churn.**

### Combined C++ fastpath + tags granularity 256

- `41334723686`, commit `7ce5475000318ef9037e075e3bc7baba78077078`, **SUCCESS** both matrix jobs 5k/50k series, same compiled C++ fastpath binary `41333837985`, compared physical TAGS `index_granularity` 8192 vs256 with A/B/A and exact PromQL output validation.
- 50k exact host: tags granule 8192 => 8,448 read_rows, 858,321B; granule256 => **512 read_rows, 90,108B**. p50 17.782ms baseline ->16.963ms candidate ->17.379ms baseline. **~10x fewer bytes but only ~4.6% faster**. 5k host 5256->512 read_rows, 545498->90074B, p50 12.435->12.284->13.031ms, negligible latency change. This establishes the dominant floor is NOT reading tag granule payload. Index_granularity=256 increases index/metadata footprint; not recommended by default.
- 50k broad sum(rate()) p50 baseline892.761 -> candidate896.264 -> recheck863.975; grouped by team 978.267->1006.731->974.494 (small regression).

### Detailed query planner and execution latency floor

- `41334957698`, commit `e7e1174cbce042278dd3aa6ab87155dea84dd079`, **SUCCESS** 5000/50000 matrix, 24 interleaved timings after 5 warmups on compiled experimental CXX selector binary and same runner; synthetic input same points, with `system.query_log` and `EXPLAIN PIPELINE/indexes=1` saved in artifacts; no unsupported queries.
- 50k series, p50 ms: HTTP POST `SELECT 1` **2.213**; direct tags lookup `SELECT count FROM .inner_id.tags WHERE host='h0001'` **4.817**; samples fetched via ID-set `SELECT sum(length(samples)) FROM .inner_id.samples WHERE id IN (...)` **8.002**; `SELECT count FROM timeSeriesSelector(... host='h0001' ...)` **13.460**; native `prometheusQueryRange(sum(rate(host='h0001'[5m])))` **19.483**; missing host PromQL **16.222**; whole 50k sum(rate()) **1052.852**. CXX candidate on a different matched job `41334320903` host p50 9.378ms, so cross-job latency is volatile: compare WITHIN this run only, no simplistic literal subtraction as total component costs (different query shapes).
- 5k, p50 ms: select1 1.676; direct tags3.791; samples ID 6.323; timeSeriesSelector11.514; full host PromQL16.355; missing13.489; all125.584. Distinct ~5–6ms overhead appears in `timeSeriesSelector` and `prometheusQueryRange` even when <1k rows read. Source review should inspect `StorageTimeSeriesSelector::readImpl`, nested SQL AST/InterpreterSelectQueryAnalyzer, resolver, tag-to-ID execution, PrometheusQueryToSQL conversion caching, and explain query-planning `query_duration_ms`.
- This is microbenchmark evidence of an expensive query path. The benchmark doesn't distinguish AST creation vs tag join setup vs query executor startup, and captures Python HTTP/network overhead; profile stack/CPU before a C++ redesign.

### Automation is blocked, not repaired

- `6ae6f0c0fe9081919f499187f801dd8c` re-enabled and given active engineer prompt, but **scheduled run 2026-11-01 18:00 UTC**, started17:53:59 and ended17:56:23, again returned `requires_user_action=true` and **task disabled itself**. Existing automation cannot provide guaranteed unattended GitHub writes without user approving/reconnecting the required permission. DO NOT report this as autonomous agent operating. Do not waste effort repeatedly updating `is_enabled=true` without resolving the permission; ask user to approve the task/connection if needed. All CXX and schema runs in current conversation were manually launched by the active assistant, NOT the background automation.


## 2026-11-01 ~18:26 UTC — bounded-grid memory and time-range profiling pushes

### Two measurable hypotheses and launched independent experiments

1. **Long14 grid memory hypothesis:** reducing bounded query width from 1440/2880 evaluation points to 60/180 while retaining 360/720 reduces peak `system.query_log.memory_usage` and potentially CPU for sparse 14d churn. Expected direction only, not proven. In workflow `ts-lab-long14-width-sweep-20261101.yml`, bounded widths changed `360,720,1440,2880` -> `60,180,360,720`; keeps native PromQL baseline, exact numerical comparison for all output timestamps, 1 warmup, 3 repeats, p50/p95/p99 and per-query read_rows/read_bytes/memory in exported query log. Commit **df47dc68b86f3c5620b50570582001cad8144ee6**, push-run **41336659441**, status **in_progress at observation**; never claim successful measurements before logs/artifacts. Bounds are evaluated in chunks, total runtime can increase due to extra requests, so RAM alone is not enough for acceptance. This is SQL/workflow algorithmic optimization, not a compiled C++ improvement.
2. **Time-ranges materialization/RSS hypothesis:** `store_time_ranges=false` reduces expensive selector join scans but might increase sample-table work for inactive series; compare A/B/A on 5k/50k, same generator, 2 warmups + 6 measured repeats, p50/p95, exact output comparison, query_log and RSS after import. Added process RSS evidence and restricted query-log to `QueryFinish` to avoid mixing QueryStart rows. Commit **c80d2b304b5610fca70d9ac7e6035f7c4c131774**, push-run **41336672056**, observed **in_progress**; separate workflow and unique concurrency group, cancel-in-progress=false, path-only push trigger. This is measurement instrumentation and not yet an established memory optimization.

### Existing completed run 41333353318 (source SHA 5594259b1324c1959c4f73fcf20135f6c6e6f89d)

Both 5k/50k A/B/A matrix jobs **SUCCESS**, archive + ClickHouse + VM binary SHA256 checks passed, identical tags/timestamps/values for 5 expressions between two ClickHouse tables. 50k exact `host=h0001` p50 **31.934 -> 17.244 -> 31.938 ms** (store ranges on/off/on), 6 measurements per phase; 5k **17.190 ->13.130 ->17.379 ms**. The 50k all-series exact `sum(rate())` is **861.024 ->884.548 ->853.402 ms**, a small regression with no ranges; no global default-disable recommendation. Time-range JOIN is confirmed in pinned source `StorageTimeSeriesSelector.cpp`, `addTimeRangesTableJoin`, merge join `full_sorting_merge`. These are latency data, not memory measurements.

### Open checks

- Inspect the full completed job logs and artifact ZIP SHA256 on 41336659441 and 41336672056. Until completion no p50/p95 or RSS effect may be claimed. Review accuracy under churn/gaps/late/OOO inputs; these two existing workflows alone DO NOT prove it.
- CPU, peak memory, data storage, 13.4M live series, 650-800k samples/s, >500 new series/s, 15s HA dedup remain production blockers.
- No master/upstream change; no PR; no active job cancellation. HTTP403 prerelease not used.


## 2026-11-01 18:38+ UTC — source-based memory work, bug fixes, and independent actions

### Proven bug in experiment harness and repair

- **Attribution bug:** previous `bench_long14_chunked.py` exposed total wall time but collected query_log with `LIKE '%timeSeriesRateToGrid%'`, mixing all grid widths and possibly QueryStart records. Commit **8ed5f38da9d3b0bc7535f88b1ec90e8d430d1d6e** adds unique `query_id` for each bounded query/native evaluation and each warmup/repeat; flushes `system.query_log`, asserts full QueryFinish coverage, reports p50/p95/max per-query memory, per-trial read_rows and total execution time. No memory benefit is claimed until completed post-change run.
- **Float reduction test false-positive:** first source comparison under gaps/late input, run **41337224258**, commit **e67ec51a65a9938b971a4c59b4dc2cd93463ba34**, **FAILED** at baseline negated selector on repeat iteration3: `AssertionError: ('baseline','negated',3,'non-deterministic repeated output')`. Test used Python list/dict equality on floating point values; no raw value delta was saved, so the precise cause cannot be inferred from this log. Fixed `capture_churn_selector_outputs.py` to retain **exact labels/timestamps and series/point counts**, compare float results with `rel_tol=1e-10, abs_tol=1e-8` including NaNs; out-of-tolerance errors still fail and report first value delta. Commit **83f0f6b3fcf52c365cc3db6f3f612718740ef082**; new workflow commit **ec92166f8ead1b10055802643639a9b53eaf18e2**, run **41337390564**.
- **Storage snapshot race:** width-Pareto workflow run **41337240971** (commit **088c8545071632d273090b696698bc5e3b008b7b**) FAILED **after all 112 ingest generations**, not in the core query, because `du -sb` hit an ephemeral `tmp_merge_202611_1_86_17` folder deleted by background MergeTree compaction; the `du` exit status 1 aborted later benchmark steps. Artifact **13095434104**, ZIP sha256 **b5d65188980aa94fd92da167cae27e71da04f25584fa07b54923e3d334405cd8** captures this, not benchmark results. Fixed optional `du` with diagnostic stderr and nonfatal status, added stable `system.parts` active `sum(bytes_on_disk)` snapshot. Commit **a44674a8d9169eb317600b1f8b428973778e9fe8**, retry run **41337482721** queued at observation. No prior active run cancelled.

### Completed new independent measurements

- **Width sweep**: run **41336659441**, commit **df47dc68b86f3c5620b50570582001cad8144ee6**, SUCCESS, artifact **13094749766**, ZIP sha256 **59b3533fde143e5eb28c5787b2642b35c11775331ef050435ec1bc2faa5d0515**. Exact 20,155 CH output points vs native, mismatch_count=0 all widths, relative error <=3.695e-15, 3 repeats / 1 warmup. CH p50/p95 ms: native 10025.287/10242.666, width60 20243.636/20509.755 (0.495x native), width180 8974.246/9071.043 (1.117x), width360 5619.051/5641.046 (1.784x), width720 3820.349/3865.907 (2.624x). Calls per evaluation: 1,336,112,56,28. SMALL grid can be over 2x slower: it repeats scans and HTTP. Prior 41332762159 found width2880 p50 3443.64ms and native 10823.87ms on a different runner, not directly same-run comparison.
- **Timeranges A/B/A**: run **41336672056**, commit **c80d2b304b5610fca70d9ac7e6035f7c4c131774**, SUCCESS both 5k and 50k, exact CH labels/timestamps/values match across tables. 50k host=h0001 p50 35.858 (range ON) -> 18.313 (OFF) -> 32.792 (ON recheck) ms, 50k all-series p50 890.414 ->898.284 ->864.618ms (no broad improvement). 5k host 22.879 ->18.450 ->24.310ms. Artifacts 50k **13096040029** ZIP sha256 **f97f9fd989b9de1845710d26b8cf720d6d2de616c4cd7494b4e91150e9070fac**; 5k **13095422157** ZIP sha256 **40b106461af9a577ebe223deb2477945bc28e4e23df2499f148b1be214657c0e**.

### Completed C++ selective JOIN bypass on injected gap/reset/late data

- **41337390564**, commit **ec92166f8ead1b10055802643639a9b53eaf18e2**, **SUCCESS**, artifact **13095099507**, ZIP sha256 **24f48c8d8a8fb51b28d2a935959c01623ffc96219b649c05d70e7197b568e632**.
- Same CH TimeSeries table, baseline binary 41301314345 -> compiled exact-label CXX skip-range candidate 41333837985 -> original baseline again. 4 generated short-lived series + 2 injected edge series (**28 samples**, with two reset/gap lifetimes and 3 late out-of-order inserted samples); CH raw input verified **2908 total samples**. All **14** PromQL cases exact tag set, timestamp and point count; numeric isclose with rtol1e-10, atol1e-8 across phases and repeats. Each case 2 warmups+6 measurements. This checks only controlled inputs and C++-vs-C++ equivalence; NOT VM rate equivalence.
- Edge old `rate(instance=edge-gap-old[5m])`: p50/p95 baseline **20.919/21.142ms**, candidate **15.140/15.367ms**, recheck **20.548/21.424ms**. `query_log` median read_rows **19 ->11 ->19** and peak tracked memory **8,634,175 ->8,422,359 ->8,634,055 bytes**. Edge new: p50 **21.308 ->15.168 ->23.392ms**, read_rows **19->11->19**. Edge offset: **23.997 ->17.090 ->22.961ms**. Edge project `sum by(region)`: **23.849 ->17.310 ->23.483ms**, read_rows39->31->39. 8 extra rows in these small datasets match bypassing time-ranges read; memory difference ~200KiB is **not** sufficient proof of meaningful prod RSS savings.
- Existing pinned `StorageTimeSeriesSelector.cpp` source confirms LEFT JOIN `full_sorting_merge` time-ranges path, and the experimental C++ patch bypasses it for bucketed exact nonempty tag selectors while sample-window filter remains. Exact-match improvement is real in matched A/B/A, but selectivity heuristic could **hurt broad high-churn label matches**, so neither broad disable nor default patch is approved for prod.

### New experimental C++ memory change and build

- Pinned source `src/AggregateFunctions/TimeSeries/AggregateFunctionTimeSeriesResultWriter.h`: `reserve(rows_count)` eagerly allocates dense `rows_count*grid_size` numeric and null arrays before first `addRow()`; tuple writer does the same for four arrays. Added experimental source-pinned patch `.github/ts-lab-pending/apply_writer_reserve_cap.py`, commit **73de3381be4b40564c606a742d56013350b4e0b6**: caps **eager reservation** to about 16MiB (per batch total cell bytes) while retaining exact dense output and Array type. The patch is *not* a streaming rate evaluator, may cause incremental reallocations and memory regressions on large blocks, and must not be treated as proven improvement. Overflow-safe division computes cap before multiplying by grid size.
- New warm C++ build workflow `.github/workflows/ts-lab-writer-cap-cxx-20261101.yml`, commit **88681d67f9821451acdf369efcbc3482bec7adfe**, run **41337447895**, **in_progress at observation**. Uses pinned ClickHouse 52529f0c7115, existing converter patch, warm `ccache` and pinned LLVM, unique group, cancel-in-progress:false, path-only trigger, 13 PromQL smoke regressions, binary/archive SHA256 manifests; NO prerelease step. Compare candidate vs baseline and retain/rollback only on verified p50/p95 RSS/peak query-log evidence.
- Independent 14d width-Pareto with same measured query_id source at widths 720/1440/2880: run **41337482721**, commit **a44674a8d9169eb317600b1f8b428973778e9fe8**, launched/queued. Requires complete logs to confirm memory and speed before conclusions.

### Remaining gates / caution

- Do not claim production-ready memory efficiency or C++ speedup from only compiled object or running job. Need finished build+binary SHA, same-run baseline/candidate/recheck, full 14d 1m grid output, 5k/50k selective workload, memory/RSS per actual CH child process (watch process is NOT CH server), storage after flush/compaction, high-card 113k and prod-sized 13.4M live/650-800k samples/s/500+ new series/s/HA15s dedup. VM MetricsQL `rate` is not PromQL `rate`; record `rate_prometheus` separately.
- Only `lab/timeseries-fastpath-ci` changed, no upstream/master/PR, no cancellation. Never equate partial diagnostic artifact with successful benchmark.

## 2026-11-01 18:42 UTC — per-query RAM Pareto and compiled C++ cap

### Long14 bounded SQL RAM and total IO fully measured

- Fixed `du`/tmp_merge race retried successfully in **run 41337482721**, workflow commit **a44674a8d9169eb317600b1f8b428973778e9fe8**, job **124940256099** SUCCESS. Complete finished log reviewed, all 20,155 timestamps and values match native PromQL exactly within 1e-10 (max relative error <=3.695e-15), with deterministic 44,800 short-lived series and 32,256,000 samples. Each variant has 1 warmup, 3 measured repeats, and each ClickHouse request uniquely linked by query_id to QueryFinish. Archive **13095678742**, ZIP SHA256 **1aaea27d4c6052565be9e30e5c1cab0ec6c9159c31f6f5ec85527fe8d8dcfb64**.
- Within this SAME run (1m step, 14 days): **native** p50/p95 **13150.460/13161.269 ms**, max `query_log.memory_usage` **250263114B**, total `read_rows` **268800**; **bounded720** p50/p95 **5194.930/5206.051ms**, peak **56219191B**, total read_rows **1601962** over 28 requests; **bounded1440** p50/p95 **3903.509/3949.199ms**, peak **84718296B**, total read_rows **859842** over14 requests; **bounded2880** p50/p95 **3614.745/3616.367ms**, peak **151440661B**, total read_rows **512000** over7 requests. These are tracked *per query* max allocations, NOT server RSS or total memory across in-flight queries.
- Bounded1440 thus **3.37x CH faster** with about **66% lower per-query memory** than native at the expense of **3.20x total read_rows**; bounded2880 **3.64x faster** but uses 151MiB vs 84.7MB. For a cap around 100MiB in THIS runner, bounded1440 is best tested time; do not extrapolate hard width to prod. VM MetricsQL `rate` 1m p50 **4921.812ms** in same run, but NOT equivalent to CH PromQL rate at generation transitions (max relative mismatch 0.5). Need `rate_prometheus` if claiming equal semantics.
- Source limitation: the bounded SQL prototype works only for `sum(rate(one exact metric[5m]))` over one internal samples table, assumes sorted output with exact timestamps. It repeats scans and is **NOT** a general PromQL C++ fix. It proves a material Pareto surface for designing a fused streaming aggregator/chunk evaluator.

### C++ writer bounded eager reserve build verified

- Commit **73de3381be4b40564c606a742d56013350b4e0b6** adds experimental `apply_writer_reserve_cap.py` for pinned CH writer header, numeric and paired numeric result variants, 16MiB initial reservation cap, dense output unchanged. Build workflow commit **88681d67f9821451acdf369efcbc3482bec7adfe**, **run 41337447895**, job **124940156325**, complete log reviewed: **SUCCESS**, all **13** PromQL regression tests pass, restored pinned submodule and ccache (used fallback restore-key selector-fast-41333837985). Ninja `clickhouse` stage started **18:38:52.923 UTC**, next step started **18:40:49.929 UTC**: under **117 s stage**, satisfying <=180s warm goal (wall time file in artifact has exact value). GitHub Actions artifact **13095549155** name `tslab-writer-cap-binary-88681d67f9821451acdf369efcbc3482bec7adfe`, ZIP SHA256 **fda6de97185d54f9af19b0aa4805b6f3e4a6113995962fa9ac6cc21f900e41b8**. Ccache saved key `tslab-ccache-Linux-52529f0c711566d696917678bf15e7068772fdde-writer-cap-41337447895`.
- C++ writer change only caps upfront `reserve`, may increase reallocation/CPU; DOES NOT reduce final dense grid allocation. It cannot yet be described as actual RAM optimization. Independent full 14d A/B/A workflow `.github/workflows/ts-lab-writer-cap-aba-20261101.yml` commit **acc67f575384b4b3bca8bcecc10c1d66e37a53bf** triggered **run 41337740889**; when measured it must check all **20,155** CH output points equal, same binary artifacts SHA256, p50/p95, QueryFinish memory/read_rows and RSS/CPU of **actual CH worker PID** (not the `ClickHouseWatch` guardian). 4 measured repeats per phase 900/300/60s + warmup, baseline->writer-cap->baseline recheck, VM p50 for context but semantic mismatch.
- Production gates remain open: cold/rich rate semantics, HA dedup with timestamp jitter, massive cardinality, sustained scrape ingest, 13.4M active series, persistent storage and compaction. No prod deployment or merge recommended. No cancellation, master/upstream/PR changes.

## 2026-11-01 ~18:45 UTC — 14d writer RAM A/B/A and broad-selector danger experiment

- **New A/B/A full 14d memory regression** `.github/workflows/ts-lab-writer-cap-aba-20261101.yml` commit **acc67f575384b4b3bca8bcecc10c1d66e37a53bf**, push run **41337740889** (in_progress at observation), job **124941040892**. This runs baseline ClickHouse -> newly built 16MiB eager writer-reserve candidate binary 41337447895 -> original baseline, with the same 44,800 series/32.256M samples, 900/300/60s evaluation step, 4 measured requests and warmup per phase, raw 20,155 1m points checked exact, `QueryFinish` per query ID, p50/p95 latency/memory/read_rows, actual CH child RSS and CPU. Candidate binary/archive SHA256 checked by runner before use. New script `.github/ts-lab-bench/summarize_writer_cap_aba.py` commit **253dcd35e186bff0636944147a0b1802b6f2ebb2**, and `.github/ts-lab-pending/bench_long14_http.py` adds optional query-ID prefix commit **173e2a8d9d44873654927ef311abc0df3f0e908d**. **No RAM benchmark result yet; do not infer from build success.**
- **Hypothesis: exact label C++ JOIN bypass may hurt broad matches on historical churn.** The exact-label C++ bypass from run41333837985 uses the presence of *any* nonempty EQ label as criterion, not the number of matching series. A `project=project-00` matcher may select ~1400 historical series across 112 generations, and `instance!="node-000-000"` is nearly all series. Skipping time-range pruning could increase samples scanned or cause memory pressure when only a small time window is queried, even though it helps single-instance exact selectors. This is source-grounded and falsifiable, NOT yet measured.
- Added `--focus` optional targeted selector cases to `.github/ts-lab-bench/capture_churn_selector_outputs.py` commit **5c5211bc59f97a949b74efc7f0ba3be0b71b5d03**. Added `.github/ts-lab-bench/summarize_selector_long14.py` commit **aaddb5fe613fec05da38bb4a6a711e11bc490f2c**. New independent same-run A/B/A `.github/workflows/ts-lab-selector-long14-cxx-20261101.yml` commit **40cde9eb987a9a0a8372f2938d6ef95e68de18a4**, run **41338111360**, status **in_progress** on 112-generation ingest, job **124942158665**. Reuses successful pinned C++ fastpath artifact from41333837985. 9 exact/regex/negated/project/offset/broad cases, 2 warmups+6 repeats, all labels/timestamps/numeric results must match baseline->candidate->recheck, p50/p95 QueryFinish read_rows/read_bytes/memory. If broad project regresses materially while exact instance improves, restrict fastpath heuristic rather than enabling globally.
- Both workflows use unique `concurrency.group`, `cancel-in-progress:false`, path-only trigger; no build cancellation or upstream/master/PR modifications. Await **complete** job logs, SHA256 zip evidence, and note failures/repro before any conclusion.

## 2026-11-01 ~18:49 UTC — 16MiB writer cap disproved by same-run A/B/A; corrected selector window

### Rejected memory candidate (do NOT promote)

- **Full 14d writer cap A/B/A run 41337740889** commit **acc67f575384b4b3bca8bcecc10c1d66e37a53bf** SUCCESS, job **124941040892** complete log inspected. Exact 20,155 CH 1m timestamps/labels/numeric outputs in baseline, 16MiB writer-reserve candidate, baseline-recheck. QueryFinish correlated by all 45 tagged CH requests, with 4 measured trials per step/phase + warmup. Artifact **13096008043** ZIP SHA256 **9245007fa14a0eca388402821cb6c445bc023057be8fbc306faa97d2524b94cb**.
- **1m `sum(rate())` (44,800 historical series)**. CH p50/p95 ms: baseline **14487.639/14542.120**, candidate **7942.826/8146.169**, baseline recheck **7511.920/7608.434**. The initial vs recheck baseline p50 drift ~**1.93x** (14.49s ->7.51s) invalidates a claim that candidate nearly doubled speed. Candidate is **5.7% slower than recheck**.
- QueryFinish p50 `memory_usage`: baseline **250342010B**, candidate **378971548B**, baseline-recheck **352911308B**. Candidate **7.4% MORE tracked query memory** than warmed baseline. Peak child-process RSS baseline **2012680192B**, candidate **1565495296B**, recheck **1541005312B**. This shows warm state drift; it is **NOT a reliable RAM win** for candidate. CH CPU seconds p50 **14.830 ->15.395 ->14.820** (candidate +3.9% vs recheck). Read rows **268800** and bytes **532385897** identical in every phase.
- 5m grid: wall p50 baseline **4186.967**, candidate **2511.090**, recheck **2225.776** ms, candidate slower than recheck. 15m grid: p50 baseline **1096.398**, candidate **1267.301**, recheck **1073.818** ms; candidate slower than both. VM MetricsQL 1m p50 baseline5304.729, candidate-run5190.889, recheck5448.236ms; VM rate != CH PromQL rate on churn edges.
- **Verdict: REJECT 16MiB writer eager reserve cap as an optimization.** It compiled and passed 13 PromQL regressions, but real A/B/A found no improvement in CPU/RAM/latency. Keep patch as historical experimental evidence only; do NOT enable or deploy by default. Root bottleneck is dense intermediate materialization, not merely eager pre-reserving. Added `acceptance_gate` to `.github/ts-lab-bench/summarize_writer_cap_aba.py`, commit **17fda6591533b5bc40256965fbc3b58fae7a0e53**: requires >=10% lower QueryFinish memory and no >5% CPU or wall-latency regression against warmed baseline; logs first baseline/recheck drift. This is a future checker; it was committed AFTER run and did not alter this completed run.

### 14d data plus short-time-window selector test, avoiding methodological gap

- We realized initial `41338111360` "long14" selector experiment evaluates the **whole 14d range**. Since virtually every short-lived historical series intersects that window, it cannot expose the danger of bypassing time-ranges pruning when asking for only the latest few hours. Still useful for broad full-history semantics, but **not sufficient to validate production selective short-window performance**.
- Extended `capture_churn_selector_outputs.py` with `--recent-hours`, new exact last-generation node cases, stable --focus filter; commit **1b78a332db4f3a5d833dd4f3215ad53b8d5d80c8**. New **independent** A/B/A `.github/workflows/ts-lab-selector-3h-cxx-20261101.yml`, commit **e07f37b7e169815f8096401a78327001bffddc08**, run **41338335077** (started/queued at observation). Uses same 44.8k/32.256M 14d data, selects **final 3h only**, 8 cases including last node, `project` with ~1400 matched historical series across 112 generations, negated and offset. 2 warmups+6 measured native CH queries in baseline -> C++ skip-range candidate -> baseline recheck; exact labels/timestamps/numerics required, QueryFinish p50/p95/read_rows/memory recorded. If broad EQ candidate reads more samples or increases memory, do not enable generalized fastpath without selectivity gates. The original full14d run **41338111360** stays active and is NOT cancelled.

No master/upstream, PR, or current Action jobs were modified or cancelled. Production validation still incomplete.

## 2026-11-01 ~18:50 UTC — verified generic exact-label fastpath memory regression; guarded C++ lab fix

### 3h recent window on 14d historical churn — completed critical regression

- **Run 41338335077**, workflow commit **e07f37b7e169815f8096401a78327001bffddc08**, job **124942814174** SUCCESS (complete log inspected), same 44,800 historical short-lived series/32,256,000 samples, query **only last 3h**, 8 PromQL selector cases, baseline original -> C++ any-exact-label bypass -> original baseline recheck, 2 warmups+6 repeats per case, QueryFinish per-id p50/p95/memory/read_rows, exact tags, timestamps and float values across phases. All 8 equivalence checks pass. Artifact **13096556355**, ZIP SHA256 **609ceabfbf2ff1c1728a51735b6e19a3094bd1fa2e9cd2dfea3225e05e5da1fa**.
- **Exact latest `instance`**: p50 baseline **25.057ms**, C++ bypass **21.207ms**, recheck **27.606ms**; read_rows **1607 ->807 ->1607**, read_bytes **1,286,851 ->1,250,019B**. Bypass useful for one instance.
- **Broad exact `project=project-00`** (~1400 historical matching series across112 3h generations): p50 baseline **56.238ms**, candidate **60.960ms**, recheck **56.485ms** = **8.4% slower** than first; read_rows **51203 ->54800 ->51203**; read_bytes **23,332,658 ->35,818,718 ->23,332,658B** (**53.5% MORE I/O**), query_log p95 memory **20,222,821 ->35,190,144 ->23,265,407B** (candidate ~51% over warmed recheck). For `sum by(region)` project: p50 **58.315 ->61.910 ->59.000 ms**, query memory peak **20.4 ->35.4 ->23.5 MB**.
- Negative matcher control `instance!="node-000-000"` does not use bypass: p50 **183.122 ->177.629 ->183.893ms**, read_rows55600 identical; regex no matching current instances retains read_rows17184 identical. This confirms fastpath harms broad exact equality where time_ranges JOIN prunes historical IDs; the heuristic `any nonempty exact tag matcher` IS NOT SOUND AS A GENERAL LATENCY OPTIMIZATION. Semantic output unchanged in tested cases, but memory/CPU production risk is real.

### Immediate C++ lab guard pushed — pending build and acceptance

- Modified `.github/ts-lab-pending/apply_selector_exact_no_timeranges_patch.py` commit **7c4ea2915c122799a68b1e39def31d110e2c7cef**: no longer skip JOIN for arbitrary nonempty exact `project`, `region`, `team` matchers. Test-only gate now requires an exact nonempty `instance` or `host` matcher on v11 bucketed samples. This restores time-ranges pruning for the observed broad-project regression and should keep performance gain on synthetic unique instance/host.
- WARNING: `instance` and `host` are **NOT guaranteed globally unique in production** and this lab-only name whitelist is not a sound universal optimizer. Production design must use estimated or measured matching series count / indexed id-set pushdown or explicit opt-in, not names.
- New C++ build workflow `.github/workflows/ts-lab-selector-guarded-cxx-20261101.yml` commit **4a4b4a7cd94b8431f6449f8bc4130a1d0bbb8e1b**, run **41338651049** `in_progress` at observation, source pinned 52529f0c7115, warm ccache, compiler/sha pinned, 13 PromQL tests and SHA256 artifact. **Do not claim compiled success or benchmark performance until complete logs and A/B/A**. Old full14d C++ run **41338111360** remains in progress on pre-guard binary and will not be cancelled.
- Also updated `capture_churn_selector_outputs.py` commit **1b78a332db4f3a5d833dd4f3215ad53b8d5d80c8** with final-3h window and new last-generation exact cases; keeps older full-range tests valid. The next necessary independent run is **new guarded binary vs original baseline vs old buggy fastpath** on same 3h/14d dataset for both unique `instance` and broad `project`, with correctness and memory gate.
