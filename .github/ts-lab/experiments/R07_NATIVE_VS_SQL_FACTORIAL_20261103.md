# R07 — Exact limited SQL query rewrite vs native PromQL: three matched ABBA controls

Status: **native stock O1 synthetic gauge proof only; candidate choice is query-dependent, no production or generic PromQL promotion.** Source pin \`52529f0c711566d696917678bf15e7068772fdde\`, stock O1 binary \`f0a2fa1f814c58853f43423705cac10ab9df3bfe5887e27ebe9620ba7cb9660e\`. Original root/claim: [R07](../claims/roots/R07.json), [primary](../claims/R07-time-day-covering-metadata-v1.json). Adjacent verified [R15 SAMPLES calendar-partition result](../evidence/R15-native-calendar-41625086352.json) is not a metadata rewrite performance baseline.

## Common physical and semantic controls

All experiments used the same deterministic **14 UTC-day ×5000 unique one-sample-per-series gauge** fixture SHA256 \`351dff6b4f49986bb7f0f586459b86b918d99fd4d4cbb0b3293b4dfa680a8efb\`; TimeSeries v11, RAW UUID full id, SAMPLES \`ORDER BY(id,bucket)\`, 1h buckets, daily SAMPLES partitions, RecentSamples off, identical label/value hashes and post-merge data. The day-cover sidecar is **optional and additive**; original TAGS, TimeRanges and SAMPLES are never removed. Both SQL variants execute per-ID \`avg\` over exact \`(start,end]\` sample timestamp window, then \`avg\` by stage, using a matching full ID set and a stage-label join. Distinct IDs are required before stage grouping. The sidecar pre-materializes \`(day,id,metric_name,stage,project)\` while the control reads original TAGS and \`tags['stage']\`.

Native parity means entire 38-stage (or 1-stage) label/time/value output matches independently. A floating-point tolerance is required because reduction order differs; max observed difference in the first smoke was <=~1.2e-13. The subset excludes staleness/rate/regex/histograms, multiple samples per ID, duplicate updates and independent-client throughput; **neither SQL form has general native PromQL equivalence**.

Each subsequent paired experiment uses \`A/B/B/A\`, 2 warmups and 4 measured requests per phase, four selector cases, 64 measured requests and 32 warmups, QueryFinish CPU/marks/read rows/bytes/query peak memory, and an A/A p50 drift gate <=25%. Three pairings ran on **different GitHub-hosted runners** and are **not pooled** or multiplied together as matched causal effects.

## Test A: native PromQL vs SQL with extra day-cover sidecar

[CI #41628618648](https://github.com/moleus/ClickHouse/actions/runs/41628618648) **PASS**, [immutable JSON](../evidence/R07-sql-sidecar-abba-41628618648.json), ZIP SHA256 \`2c64f7d9f308b8a5494ed5a5148a8dee982ff85fdd1a2a101e871f22b3bcecde\`.

| Query | Native p50 ms | Day-cover SQL p50 ms | Native/SQL |
|---|---:|---:|---:|
| broad1d | 33.072 | 33.165 | 0.997x, neutral |
| exact stage1d | 19.334 | 11.159 | **1.733x** |
| broad3d | 43.556 | 18.585 | **2.344x** |
| broad14d | 130.729 | 43.994 | **2.972x** |

All outputs matched, all four control drift gates PASS. On broad14d read rows **210k→210k** and selected marks **298→308** (worse), while read bytes **19.6MB→8.61MB**, CPU **282.27→85.43ms**. These data **refute the claim that row/mark pruning alone caused the ~3x speedup**: the query DAG, per-series aggregation, joins, label materialization and chosen columns changed too. R07 cannot claim the sidecar contributed 3x.

**Negative method record:** earlier ABBA attempt [#41628463197](https://github.com/moleus/ClickHouse/actions/runs/41628463197) failed before measured queries from missing \`import os\`; corrected in commit \`94687f8b434fd448a67be82a5cae4db8b051c48f\` without changing DDL or query formulas.

## Test B: SAME SQL operators, original TAGS versus day-cover

[CI #41629085028](https://github.com/moleus/ClickHouse/actions/runs/41629085028) **PASS**, [immutable JSON](../evidence/R07-tags-vs-cover-sql-41629085028.json), ZIP SHA256 \`b179205adc3b8bc1a2c4af0ddc1f06b9081a953b7f2058068cc36ea0b6f313c2\`.

| Query | Original TAGS SQL p50 ms | Day-cover SQL p50 ms | TAGS/day-cover |
|---|---:|---:|---:|
| broad1d | **31.466** | 37.418 | 0.841x, cover regresses |
| stage1d | 19.587 | **13.242** | **1.479x** |
| broad3d | 38.856 | **22.628** | **1.717x** |
| broad14d | **55.087** | 57.505 | 0.958x, cover slightly slower/inconclusive vs 18.7% baseline drift |

Stage1d and broad3d **are source-specific evidence of a useful covering metadata access path**. Broad1d is a negative case: rows read **150k→30k** with the cover, but wall **worsens by 18.9%** and CPU **38.03→42.28ms**. Merely reading 5x fewer rows is not enough. Broad14d reads **210k** in both and the extra sidecar gives **no defensible improvement**. The intervention changes metadata **day partitioning and stored label materialization together**; it does not isolate just the partition key.

## Test C: native PromQL vs SQL on ORIGINAL TAGS, with no sidecar

[CI #41629492828](https://github.com/moleus/ClickHouse/actions/runs/41629492828) **PASS**, [immutable JSON](../evidence/R07-native-vs-tags-no-cover-41629492828.json), ZIP SHA256 \`73c7950fd305c014cf4797dc7a67e4874fd6442ba7489d488a1e2b15e96c6a7a\`. No sidecar was created or read.

| Query | Native p50 ms | Original TAGS SQL p50 ms | Native/SQL |
|---|---:|---:|---:|
| broad1d | **69.421** | 175.358 | 0.396x, SQL **2.53x slower** |
| stage1d | 38.881 | **35.743** | 1.088x |
| broad3d | **105.842** | 191.383 | 0.553x, SQL **1.81x slower** |
| broad14d | 395.890 | **254.865** | **1.553x** |

All four controls passed with <=6.7% A/A drift. For broad14d, exactly **210,000 read rows and 298 selected marks** in both paths, yet native→SQL CPU **786.50→405.29ms** (−48.5%), query memory tracker peak **101.27→38.38MB** (−62.1%) and \`read_bytes\` **19.6→8.33MB**. This **proves an end-to-end benefit of the specialized SQL rewrite without extra index** for this restricted 14d synthetic query and demonstrates that the benefit is not from scanning fewer rows or marks. The underlying cause may include fewer projected columns, avoiding native group/tag materialization and different operator implementation; detailed per-operator CPU and join-state attribution remain to be measured.

For broad1d and broad3d, native plan is much better. A global rewrite is **REJECTED**. The numeric results are within-runner pairs only; absolute values are much higher in this runner than earlier R07 CI, so do not synthesize cross-runner speedups.

## Independent source and semantics

Pinned [applyOneArgumentAggregationOperator.cpp](https://github.com/moleus/ClickHouse/blob/52529f0c711566d696917678bf15e7068772fdde/src/Storages/TimeSeries/PrometheusQueryToSQL/applyOneArgumentAggregationOperator.cpp) already maps native PromQL \`avg\` to \`avgForEach(values)\` grouped by transformed tag group. [fromSelector.cpp](https://github.com/moleus/ClickHouse/blob/52529f0c711566d696917678bf15e7068772fdde/src/Storages/TimeSeries/PrometheusQueryToSQL/fromSelector.cpp) implements instant stale marker handling and range selectors; [StorageTimeSeriesSelector.cpp](https://github.com/moleus/ClickHouse/blob/52529f0c711566d696917678bf15e7068772fdde/src/Storages/StorageTimeSeriesSelector.cpp) builds TAGS/TimeRanges ID selection then SAMPLES. The SQL implementation bypasses some native path and changes vector-grid/label registration overhead. **Do not describe native AVG as unoptimized or missing:** it is already vectorized; the next candidate is conditional **whole-path** specialization, not adding another SQL aggregate.

The SQL oracle uses \`AVG\` per original ID then \`AVG\` per stage, equivalent to this finite gauge subset after full sample dedup. It is not a valid drop-in for arbitrary PromQL selectors, NaN/stale marker processing, duplicate same-timestamp samples, offsets, binary operators, regex/ACL, native histograms, or range queries with many time-grid evaluation points. All of these are mandatory falsifiers.

## Decision and next work

- **PROMOTE FOR RESEARCH ONLY**: Conditional native query plan choice for long-window broad gauge aggregations, using original TAGS first or a fused per-ID scalar/label grouping plan. No additional storage required in this promising 14d case.
- **CONDITIONALLY EXPLORE**: Extra day-cover sidecar for stage1d and broad3d high-churn workloads, but **reject universal index** due broad1d and long-window negatives, freshness and write amplification.
- Before any C++ build, repeat a **same-source and same-runner multi-sample fixture** (R01 staggered 15/60/300s, several samples/ID and bucket rows), native label/time/value parity for left-open boundaries, repeated updates/dedup and stale/NaN cases, per-operator CPU/READ breakdown, 1/4/8 concurrent clients and full write/merge costs. Explicitly compare unspecialized SQL and full native for each workload. If unable to meet exact semantics, only support a narrow opt-in expression family and fallback.
- No C++ compile, production mutation, scheduled workflows or changes to other question-owner roots. Historical run failures and slower cases are retained in original GitHub artifacts.

