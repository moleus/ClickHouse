# R26 — VM v1.134.0 global vs per-day index search: source-pinned A/B/B/A

**Status: SUCCESS, ACCEPT_DIAGNOSTIC_PATH_ABBA** on [CI 41670634959](https://github.com/moleus/ClickHouse/actions/runs/41670634959). Four of four broad primary cases valid; seven of eight selector-window cases pass. **One narrow single-tick case excluded** due to A/A drift. This is a VM-only **read-path mechanism test**, not a measured ClickHouse optimization or production-normalized CH/VM comparison.

[Machine-readable immutable evidence](../ts-lab/evidence/R26-vm-perday-vs-global-query-index-path-v1/41670634959.json) · [Raw complete GitHub artifact](https://github.com/moleus/ClickHouse/actions/runs/41670634959/artifacts/13247797677) · Artifact ZIP digest `sha256:9d3d8f2c2fff7744db2b36ac87eb490cf8e6303727b0006014a951a3f2a85274` · [R26 root](../ts-lab/claims/roots/R26.json) · [Claim](../ts-lab/claims/R26-vm-perday-vs-global-query-index-path-v1.json).

## What changed and what did not

| Control | Fixed / variable |
|---|---|
| Binary | Same VM v1.134.0 optimized release SHA-pinned archive `cd6e109bae85ff7c138d838b32be9a7b3a387a7cf9234f22991da10`; source tag commit `eaf7a68c92c8069a67ae02e601e81453a5e002de` |
| Fixture | Same exact R01 400-slot, 3-day synthetic sample JSONL SHA `ea9a8d43c9e58ed916f1bf43949556d8ed18169ddd14033c293af4cd54c9881d`: 28,896 series, 2,488,320 points; aligned starts |
| Data | Import once into VM with **default per-day indexing enabled**; stop cleanly, then copy a byte-identical storage snapshot per phase. No writes during timed tests |
| Cache | VM result response cache disabled `-search.disableCache` + `nocache=1`. Matching-ID cache capped to **1 byte** in both variants via `-storage.cacheSizeIndexDBTagFilters=1` |
| Memory and cap | VM `-memory.allowedBytes=4294967296`, `-search.maxUniqueTimeseries=165000`; same runner |
| **Only experimental variable** | `-disablePerDayIndex` for **global**; absent for **per-day** index search |
| Order | GLOBAL → DAY → DAY → GLOBAL, four fresh processes |
| Repetitions | Per phase and each of four selectors × two tick counts: 8 warmups +48 measured HTTP `/api/v1/query_range` calls |
| Correctness | Exact nonempty labels, timestamps, numeric values with tolerance; querytracer `trace=1` used **after** measurements to confirm selected global versus per-day read path |
| Measures | Client request p50, p95, p99; VM whole-process CPU delta/request; process RSS, cache metric deltas and trace (artifact) |

One tick means one evaluation timestamp with a 1d lookbehind window. Three ticks means three daily evaluation timestamps, each computing over a 1d lookbehind. These two cases do **not** select a single continuous [3d] function window with identical computation.

## Successful comparisons only

The displayed p50 and CPU values are **mean of the paired phase medians**, not a pooled run median. Ratio `global / per-day >1` means the per-day path is better in this test.

| Native selector | Global p50 ms | Per-day p50 ms | Latency ratio | Process CPU ms/query (global → per-day) | Max A/A control drift |
|---|---:|---:|---:|---:|---:|
| Все узлы, 1 tick | 11.696 | 9.001 | 1.299× | 28.542 → 24.375 | 0.143 |
| Все Pod, 1 tick | 21.271 | 15.616 | 1.362× | 38.958 → 33.438 | 0.099 |
| Все узлы, 3 ticks | 26.559 | 26.247 | 1.012× | 74.479 → 74.583 | 0.078 |
| Все Pod, 3 ticks | 47.599 | 45.090 | 1.056× | 109.583 → 105.521 | 0.108 |
| Один namespace, 3 ticks | 3.785 | 3.320 | 1.140× | 7.917 → 7.917 | 0.211 |
| 15s серия, 1 tick | 0.819 | 0.752 | 1.089× | 1.458 → 1.146 | 0.182 |
| 15s серия, 3 ticks | 1.085 | 1.081 | 1.004× | 1.771 → 2.083 | 0.200 |

Primary broad one-tick savings:

- Node: 11.696 → 9.001 ms (23.0% less wall), 28.542 → 24.375 ms CPU (14.6% less).
- Pod: 21.271 → 15.616 ms (26.6% less wall), 38.958 → 33.438 ms CPU (14.2% less).

Three-tick broad workloads had much smaller gains. That is compatible with per-day selection becoming a smaller fraction of full query work, or with more dates being visited. It is **not** direct measurement of a particular internal operator's exclusive CPU.

All four broad primary cases have full trace-path verification, exact output and under-25% A/A baseline drift for both CPU and wall time. `pod_namespace_three_tick` and the two `burst15` rows pass the same gate, but very short times and coarse process CPU ticks limit interpretation.

### Explicitly excluded case

`pod_namespace_one_tick` had global p50 2.254 ms and per-day p50 1.256 ms, but **global A/A wall drift 47.98% and CPU drift 46.15%**, beyond the preregistered 25% ceiling. It is invalid as timing evidence. Do **not** cite its apparent 1.79× ratio as a successful improvement. The CI job passed because all four preregistered primary broad cases passed; the machine-readable record retains this invalid secondary case.

## Why the result is actionable for ClickHouse

Source: [VM `Storage.adjustTimeRange`](https://github.com/VictoriaMetrics/VictoriaMetrics/blob/v1.134.0/lib/storage/storage.go#L1623-L1651) selects a **global index** for `-disablePerDayIndex`, date ranges spanning more than 40 day-number differences, and full-indexDB coverage. It otherwise tries **per-day** index search. [VM `indexSearch.updateMetricIDsForTagFilters`](https://github.com/VictoriaMetrics/VictoriaMetrics/blob/v1.134.0/lib/storage/index_db.go#L2269-L2286) dispatches accordingly, and [day-by-day union](https://github.com/VictoriaMetrics/VictoriaMetrics/blob/v1.134.0/lib/storage/index_db.go#L2462-L2516) may run in parallel.

ClickHouse TimeSeries v11 has a global TAGS inverted text index, separate TimeRanges metadata, and SAMPLES `ORDER BY(id,bucket)`. Its native read path has a forced metadata JOIN method: [selector implementation](https://github.com/moleus/ClickHouse/blob/52529f0c711566d696917678bf15e7068772fdde/src/Storages/StorageTimeSeriesSelector.cpp#L1167-L1340). The R26 evidence makes a **time-aware plan** worth evaluating, but does not prove a CH per-day sidecar or a VM port will have the same speedup. CH's text index already exists and R07 owns time-local metadata experiments.

The general decision bound for a proposed metadata access path is:
```
Use-day iff Cost(day-index probe + union + downstream sample work + write/merge overhead)
         < Cost(global TAGS + TimeRanges + downstream sample work)
and peak process memory + correctness constraints hold.
```
The VM source's fixed `40-day` cutover is a **rule**, not measured ClickHouse CBO selectivity. Actual partition coverage, per-day activity, ID hit rate per mark, cache state, write amplification and multi-tick aggregation must enter any CH cost model.

## Limitations and next gate

- Read-only and single client. Does not include continuous remote write, late backfill, TTL, compaction, replication, cold caches or 1/4/8 concurrent clients.
- A 3-day synthetic fixture is not production nor the Jenkins 1.33m series /14-day churn test.
- VM index-cache limited to 1 byte in **both modes** to expose index cost. VM with normal warm default index cache may behave differently.
- Index storage and ingestion overhead of the per-day scheme were **not measured** because both variants reused the same fully indexed base data.
- No source-patched or optimized CH binary was involved in R26; the data cannot be combined with old CH O1-vs-VM release timing as causal attribution.

Next independent E04 measurement should use the existing 14d sparse Jenkins fixture (or a frozen derived subset) and the exact VM read-path switch with 1/3/7/14d range selectors, including warm-cache and write-cost controls if performance is promising. E03 owns CH CBO; R05 owns mark pruning; R07 owns time-local metadata sidecar. Do not duplicate their CI claims.
