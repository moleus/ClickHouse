# TimeSeries engine performance work: ClickHouse vs VictoriaMetrics

**Owner goal (2026-11-02):** Change the **ClickHouse TimeSeries engine**, not replace it with a standalone events table. It must aggregate huge numbers of one-sample or short-lived series very quickly AND stay competitive with VictoriaMetrics on normal, continuously scraped metrics. Correctness, ingestion cost, retention, and predictable memory usage are non-negotiable.

This is an engineering workplan and experiment record, not a claim of production readiness.

## Target workloads

| Workload | Required queries | Correctness |
|---|---|---|
| CI-like high-cardinality: 2,474 builds/day × 38 stages = 94,012 samples/day, each series only one sample, up to 14–90 days | `avg by(stage)`, filtered by `branch,stage,project,jenkins_job`; explicit `avg_over_time[1d]`; arbitrary step and time windows | Every stage/time/value; no unweighted mean of unequal means; late/back-delta samples |
| Normal dense: >=20K series ×120 points; extend to real 13.4M active region, 650K–800K samples/s | `sum(rate(m[5m]))`, `sum by(team|region)(rate(m[5m]))`, instant `{host=...}`, regex, negative, offsets, dedup | Bit/equivalence against ORIGINAL CH C++ PromQL for same program; VM MetricsQL comparisons only for well-defined matching semantics |
| Historical churn: 44.8K historical series ×32.256M samples and 14-day retention | `rate` at step 60/300/900, last 3h selective queries over 14d | Exact 20,155-point outputs across all phases, range/lookback boundaries |

Acceptance conditions: same GitHub-hosted runner and identical input for each engine, same CPU cap/threads where possible, separate O1 research builds from realistic O2/O3 builds, disable VM cache consistently, 4+ repetitions and full A/B/A baseline recheck, exact output validation, p50/p95/p99, CH query_log read_rows/read_bytes/memory_usage and process RSS+CPU where possible. Include 15s HA, back-delta 69%, real cardinality, 14–90d retention and ingest+merge throughput before any production recommendation. Do not hide regressions or infer storage production behavior from 3d unmerged parts.

## Measured baseline: real gap, same runner

**[C++ A/B/C/A job 41350915125](https://github.com/moleus/ClickHouse/actions/runs/41350915125)** (20K regular, 2.4M samples and 282036 single-event series):

| Query | Original CH TimeSeries p50 ms | VM v1.134.0 p50 ms |
|---|---:|---:|
| Jenkins group avg, explicit [1d] | 1549.689 | 535.477 |
| Jenkins project filter | 314.541 | 12.843 |
| Dense `sum(rate[5m])` | 474.246 | 51.556 |
| Dense grouped rate by team | 557.162 | 65.776 |
| Dense grouped rate by region | 592.676 | 62.716 |
| Dense exact host selector | 30.263 | 1.652 |

All C++ candidate variants' output match original CH, Jenkins also matches event SQL and VM. VM `rate()` uses MetricsQL, not necessarily exactly PromQL semantics. Full CH query-memory in Jenkins ~507MB (QueryFinish p95), with 282036 samples and 4344 final values. These results demonstrate **both highcard and normal TS bottlenecks**, not just VM's 165000 series search limit.

Original binary uses `-O1` because this CI research setup compiles ClickHouse from pinned sources. Repeat strongest hypotheses under production optimization before claiming a cross-engine performance ratio.

## Measured successful direction: avoid per-series grids for grouped scalar rollups

**[Same TS inner-table SQL experiment 41350787466](https://github.com/moleus/ClickHouse/actions/runs/41350787466)** used ONLY the existing TimeSeries target tables `samples` and `tags`. It kept the original metric, all labels, timestamps and values; the SQL event table served **only as an independent correctness oracle**, not the tested storage.

| 3d, 282036 series, group avg by 1448 stages | p50 ms | QueryFinish memory p95 |
|---|---:|---:|
| CH native PromQL converter (arrays per series) | 1623.876 | 505790848 B |
| CH TS inner tables, `avg per id/day` then `avg by stage/day` | **476.183** | **127955525 B** |
| CH TS inner tables, flat `avg samples by stage/day` | **309.466** | **91226689 B** |
| VM standalone v1.134.0 | 663.616 | VM equivalent not measured |

The two-stage aggregation preserves per-series weights for disjoint, non-overlapping daily windows; it is 3.4× faster and uses ~4× less tracked memory than the current converter, and is faster than VM on this **broad** test. Flat averaging is only correct with a proven single sample per series per window (other data cannot use it without runtime guard or metadata). Other filters still lag VM, so this is not a complete fix.

**Source path:** `src/Storages/TimeSeries/PrometheusQueryToSQL/applyFunctionOverRange.cpp` turns `avg_over_time` into `timeSeriesAvgToGrid`. `src/Storages/TimeSeries/PrometheusQueryToSQL/applyOneArgumentAggregationOperator.cpp` then maps grouped avg to `avgForEach`. It materializes per-series `Array(Nullable(Float64))` and repeatedly filters missing grid cells. The narrow `avg by(stage)(avg_over_time(...[1d]))` equivalent SQL implementation is in `.github/ts-lab-bench/bench_jenkins_ts_inner_sql.py`; it has **NOT** been integrated into the C++ PromQL converter.

**Implementation direction:**
1. Implement a narrowly guarded C++ query-planner lowering for *disjoint* windows with `step >= window` and safe time-grid alignment, without any offset/subquery/`@/` mixing. Keep per-ID aggregate exactly; grouping by stage comes AFTER per-ID rollup. No universal “one sample” assumption.
2. Add an optimized fused per-ID/per-grid aggregator for short-lived sparse workloads that emits group-level sum/count without materializing nullable arrays for each individual series. Preserve lookback, range endpoints, empty windows, staleness and duplicate labels. Keep old converter as default/fallback until all original CH PromQL outputs agree pointwise.
3. Measure memory and high-card output first; ensure normal `rate` queries do not use this special-case lowering and suffer no regression.

## Proven failed optimization: smaller C++ bucket hash table

**Source patch** `.github/ts-lab-pending/apply_bucket_grower_patch.py` changes `TimeSeriesBucketsHashTableGrower` start from 16 to 4 or 8 slots. **Both C++ binaries compiled**, 13 PromQL regression cases passed, complete same-run A/B/C/A benchmark **41350915125** succeeded with exact results.

| Query | 16 cell original p50 | 4 cell | 8 cell | 16 cell recheck |
|---|---:|---:|---:|---:|
| Jenkins group avg | 1549.689 | 1731.218 | 1739.742 | 1713.024 |
| Dense `sum(rate)` | 474.246 | 496.018 | 490.900 | 474.448 |
| Dense `sum by(team)(rate)` | 557.162 | 596.246 | 582.925 | 532.492 |

Jenkins p95 tracked memory stays approximately **507–509MB**, read_rows unchanged **846108**, read_bytes unchanged **103784412**. Dense rate is 3.5–4.5% slower vs recheck and group by team up to 12% slower; no material memory savings. **REJECT** this candidate; leave as lab-only patch, do not commit its changes to engine source.

## Proven partial improvement: physical label clustering

**[TAGS stage/project sorting test 41351343306](https://github.com/moleus/ClickHouse/actions/runs/41351343306)**: four native TimeSeries tables, all 282036 same series/samples, `store_time_ranges=false`, compare default map tags, dedicated columns, `TAGS ENGINE=ReplacingMergeTree ORDER BY(metric_name,stage,id)` or `... (metric_name,project,id)`. All output values/labels/times equal VM and native SQL oracle.

| Case | baseline p50 ms | indexed candidate p50 | VM p50 | CH read rows |
|---|---:|---:|---:|---|
| project_00 | 189.145 | **158.299** | 10.524 | 564072 → 306612 |
| stage_0000_0009 | 235.135 | **181.917** | 6.058 | 562024 → 304564 |
| all stages | **850.718** | 890.104 | 498.328 | 564072 unchanged |

This improves a specific physical tag key but **does not support arbitrary labels** and makes broad query a little worse. `tags_to_columns` alone did not improve read rows. VM's per-day inverted label index resolves arbitrary combinations using postings lists and MetricIDs, while CH currently scans tags MergeTree and applies primary-key/skip pruning only for the sort key.

**Implementation direction:** investigate a native TimeSeries label-value -> IDs index (postings), maintained during ingestion and correctly deduplicated across replacements, late writes, deletes, retention and distributed shards; intersect postings before sample reads. The current TS MergeTree samples reader may still pay high costs for random ID lookups because of large granules. Indexing changes need both tags and samples physical access experiments, including negative regex cases and high-card broad queries. Do NOT add a label-name heuristic that bypasses time_ranges: the verified 10-fanout test in the existing notes showed it regresses in 9/9 shared-instance variants.

## Regular metrics path: independent pipeline profiling and host index

- A normal 20k-series, 2.4M-sample dense set sees CH rate p50 474ms vs VM 51.6ms in the same runner. This is a true engine research target; optimizing Jenkins alone is not acceptable.
- [Host physical index CI 41351535820](https://github.com/moleus/ClickHouse/actions/runs/41351535820) compares CH default tag Map, materialized `host`, and `TAGS ORDER BY(metric_name,host,id)`, including `rate()`, `sum by()`, regex, exact host, and VM baseline. Output equality is checked across all CH layouts. Status/results must be read from the completed job log; no claim of speedup before completion.
- Need separate cost accounting for TS tags scan, sample decode, `timeSeriesRateToGrid`, group-by, full PromQL conversion. The existing `.github/ts-lab-bench/bench_inner_rate.py` isolates inner-samples rate and `max_threads`, but is not a full PromQL-equivalence proof.
- Engineer a streaming/grouped rate path that can combine series with matching group labels, while preserving per-series counter-reset, extrapolation, staleness and empty/null semantics. The current `sumForEach` of per-series dense grids is a candidate bottleneck.

## Source/reproducibility

Pinned CH: `52529f0c711566d696917678bf15e7068772fdde` with known TS lab patch, VM: `v1.134.0`. All compiled CI fixes use read-only pinned source checkout, restored submodule metadata, SHA256-verified LLVM and binary artifacts and ccache. Workflows trigger only on their own file paths, have unique concurrency groups, `cancel-in-progress:false`. The lab branch is the only writable destination. **Do not push to master/upstream, cancel long builds, or create a PR without authorization.**

Raw scripts, per-query JSON and GitHub Actions artifacts are under `.github/ts-lab-bench/`. Original engineering history: `ENGINEERING_NOTES_20261101.md`.

### 2026-11-02 full PromQL query: forced parallel read DOES NOT improve the engine

Script .github/ts-lab-bench/bench_full_promql_parallel.py commit **16b4edb76c4cdd6b4cd496ebae1b1fbf750e4550**, workflow .github/workflows/ts-lab-full-promql-parallel-20261102.yml commit **1890b09cce35c131e93461756ff16cc1fda7f94f**, **run 41352456219**, job **124985398862 SUCCESS**. Full completed log read; artifact **13101429937**, ZIP SHA256 **20abd0234d8946fb2fe5b1a1d0e10c13ce0443b51588a80c8de2d5d44252f38a**, downloaded and independently sha256 verified; parsed 25 EXPLAIN PIPELINE variants and per-query JSON. 20000 dense series, 2.4M same samples, same CH TS table and VM, all five settings variants EXACT same labels, timestamps and numeric CH output on rate_sum, rate_team, rate_region, host_exact, host_rate.
- Full TimeSeries PromQL rate_sum p50: baseline 4 threads **181.690ms**, small read chunks 4 threads **180.083ms**, unordered+min1 4 threads **179.306ms**, unordered+min1 8 threads **178.669ms**, 4 thread recheck **180.225ms**, VM MetricsQL baseline **53.767ms**. CH read_rows=80000 and query memory p95 ~78.69MB unchanged.
- Grouped team rate p50 275.671 -> 284.243 (forced) -> baseline recheck286.263ms, VM67.175ms. Group region rate p50 295.504 ->290.800 -> recheck292.057ms, VM65.085ms. host exact 16.197 ->16.469 -> recheck16.611ms, VM0.585ms. host_rate 17.980 ->19.078 -> recheck19.923ms, VM0.484ms. NO robust general speedup.
- Crucial EXPLAIN: **both** baseline and forced full-prometheusQueryRange rate_sum already have MergeTreeSelect(pool: ReadPool, algorithm: Thread) ×4 and AggregatingTransform ×4 before final merge. The earlier direct inner-rate SQL EXPLAIN was serial ReadPoolInOrder ×1 and forcing min_chunks made it ×4, yielding 2.4x speedup. That is a **different query plan**; do NOT change default whole-engine thread settings based on the inner SQL prototype. Full PromQL is already parallel; primary remaining overhead is per-series timeSeriesRateToGrid and grouping/nullable grids, plus label lookup/IDs.
- **Decision: no global threshold or max_threads tweak promoted to source**. Strong path remains C++ fused per-series rolling window plus grouped aggregation (avoid intermediate Array(Nullable) per series), and an arbitrary-tag inverted postings index with efficient sample-ID access. Existing prototype over TS inner tables reduced highcard broad query p50 1623.876 ->476.183ms and memory p95 505.8 ->128.0MB, but is not yet integrated into C++.
