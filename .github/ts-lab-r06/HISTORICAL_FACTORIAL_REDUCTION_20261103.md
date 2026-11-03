# R06: independently reduced native JOIN x projection factorial (2026-11-03)

Status: **PASS for re-analysis of archived evidence, not a new benchmark and not a validated planner patch.** Owner: `chatgpt-e03-order-aware-20261103`; [R06 root](../ts-lab/claims/roots/R06.json). No production or master change.

## Raw evidence, exact provenance

- Original native four-mode, eight-phase A/B/C/D/D/C/B/A run: [41589798782](https://github.com/moleus/ClickHouse/actions/runs/41589798782), historical pinned source `52529f0c711566d696917678bf15e7068772fdde`; old LLVM22 **-O1** lab JOIN override, not release/stock parity.
- Stage artifact `13209263811`: ZIP SHA256 `b3a6363caf178831254f19f7f41afea5e2a4e891fdaf0f3568bcb44e4627b733` (633230 B); report `native-factorial-stage/report.json`.
- Project artifact `13208894365`: ZIP SHA256 `e0841466460b5aceaf3199eb1a5a65139ec44edbeafe8e6a1360e9b955643604` (638248 B); report `native-factorial-project/report.json`.
- Each artifact contains pinned fixture manifest: 1,330,000 distinct IDs across 14 days, fixture SHA256 `8f3a36846f80d6be5a8604d142e934399f2687094766c57bf105e43977f52858`. It contains original binary-hash verification **status** (`clickhouse: OK`), but not binary hash itself; do not invent an actual executable SHA from this file.
- [New strict reducer](reduce_native_factorial.py) checks ZIP manifest/SHA, expected eight-phase order, 3 warmups+4 measured requests per phase, unique query IDs, recorded CPU/read/marks/QueryFinish fields, representative profile IDs and the original parity check errors. [Seven negative/positive unit tests](test_reduce_native_factorial.py) pass locally.
- Both archived experiment reports are `passed=true`, errors `[]`. Raw native output rows are **not** archived; semantic parity in this re-analysis is inherited from the original script's full native `normalize_any/assert_same` checks, not independently retested.

## Reconstructed measurements, seven-day selectors

Values: mean of two matched phase medians. CPU is `OSCPUVirtualTimeMicroseconds` from QueryFinish; query peak is ClickHouse tracker, **not process RSS**. Exact float values are in the original artifacts; shown rounded.

| Selector | Physical variant | p50-style wall ms | CPU ms | Query peak MiB | read rows | read bytes | selected marks |
|---|---|---:|---:|---:|---:|---:|---:|
| stage | OFF + merge | 818.25 | 2118.36 | 116.87 | 3,945,712 | 347,193,792 | 5,349 |
| stage | AUTO + merge | 1114.50 | 2797.49 | 124.81 | 3,282,160 | 319,323,984 | 5,268 |
| stage | OFF + hash | 1608.50 | 3107.82 | 192.71 | 3,945,712 | 347,193,792 | 5,349 |
| stage | AUTO + hash | 1547.50 | 2916.82 | 165.89 | 3,282,160 | 319,323,984 | 5,268 |
| project | OFF + merge | 541.50 | 1422.92 | 130.30 | 3,990,000 | 346,668,512 | 5,522 |
| project | AUTO + merge | 718.75 | 1813.63 | 142.66 | 3,326,448 | 318,798,704 | 5,441 |
| project | OFF + hash | 661.75 | 1585.18 | 228.36 | 3,990,000 | 346,668,512 | 5,522 |
| project | AUTO + hash | 697.00 | 1545.32 | 201.91 | 3,326,448 | 318,798,704 | 5,441 |

Projection saves exactly **663,552 query-level read rows**, **27,869,808 read bytes** and **81 selected marks** on each seven-day selector. These aggregate counters mix internal tables and cannot be mapped directly to TimeRanges rows or SAMPLES read savings. `read_rows` decreases about 16.8% on stage and 16.6% on project.

With merge JOIN, projection raises wall **296.25 ms** / CPU **679.13 ms** on stage, and wall **177.25 ms** / CPU **390.71 ms** on project. Per-position A/B matched wall deltas agree in sign: stage +344.5/+248.0 ms; project +241.0/+113.5 ms. Control drift of AUTO+merge is 19.47% stage and 22.47% project (valid under archived 30% gate, but too large to trust a small effect).

## Factorial interaction, not a claim that every extra millisecond is sort cost

Difference-in-differences:

```text
interaction = (wall_AUTO_merge - wall_OFF_merge)
            - (wall_AUTO_hash  - wall_OFF_hash)
```

- Stage: `296.25 - (-61.00) = +357.25 ms` wall interaction; CPU interaction `+870.13 ms`.
- Project: `177.25 - (+35.25) = +142.00 ms` wall interaction; CPU interaction `+430.58 ms`.

Representative `processors_profile_log` source rows show AUTO+merge minus OFF+merge work across `MergeSortingTransform`, `MergingSortedTransform` and `PartialSortingTransform`: **+922.0 ms** (stage) and **+450.6 ms** (project) of mean sampled *processor work*. The mean was taken across two separate representative trials, **not** across all 56 queries. Processor elapsed work overlaps across threads and must not be added to native wall time. The factorial interaction includes JOIN, read-path and pipeline effects; it does not isolate sorting mathematically.

The result is still a strong warning: **smaller input bytes do not imply a faster native query when a physical projection loses the order needed by a merge JOIN.** Hash JOIN avoids this ordering requirement, but its build work and query peak grow. On these two historical selectors, OFF+merge is the observed lowest wall, CPU and query peak among the four modes. This is fixture-specific, not a production decision.

## New finding: top-level EXPLAIN cannot distinguish the variants

Both original ZIPs include four `plans` strings generated by `EXPLAIN PLAN indexes=1,projections=1 SELECT * FROM prometheusQueryRange(...)`. Within **each selector**, all four top-level plans are byte-for-byte identical:

- Stage SHA256 prefix for each: `bab3382e6a549721`.
- Project SHA256 prefix for each: `fe422f1e3ce0bdbd`.

But the native processor logs differ strongly (sort vs `FillingRightJoinSide`). Therefore a top-level EXPLAIN is **not sufficient** for choosing an inner TimeSeries physical plan. The selector itself logs the generated nested SQL at DEBUG level in pinned [StorageTimeSeriesSelector.cpp:1352](https://github.com/moleus/ClickHouse/blob/52529f0c711566d696917678bf15e7068772fdde/src/Storages/StorageTimeSeriesSelector.cpp#L1352-L1360). Next action is [the isolated source-pinned generated-SQL probe](probe_nested_selector_plan.py) via [workflow](../../workflows/ts-lab-r06-nested-plan-20261103.yml). It captures exact generated nested SQL plus inner PLAN/PIPELINE and verifies native values across variants. That probe does **not** claim speedup.

## Immediate planner gate

Do not enable hash or time projection by default on the basis of read-row estimates. First represent required **sorted-by-id** property and whether the chosen projection preserves it; if not, model added sort and/or hash build with a conservative resident-memory ceiling. If planner statistics cannot bound build rows, sorting size and concurrent RSS, fall back to the existing ordered merge path.

A usable planner needs held-out 1/3/7/14d native selectors and 1/4/8-client memory gates from E06/R08; this archival re-analysis supplies neither a fitted threshold nor a new correctness oracle. Status for adaptive native code: **NOT IMPLEMENTED**.
