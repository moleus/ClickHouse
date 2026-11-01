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
