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
