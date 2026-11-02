# Source-guided samples mark granularity model and falsification test

2026-11-02. Pinned CH 26.11 source `src/Storages/TimeSeries/TimeSeriesSettings.cpp:34` defines `samples_index_granularity` default 256 for bucketed v11. Tags and samples have ORDER BY `(id,bucket)` and sample IDs are hash-derived. Read plan from 41464136451 selected an exact 1080-ID set but still had ~122,248 total read rows, similar to broad selection.

## Mathematical model (uniform random ID positions, not theorem of actual engine IO)
Let N=41,000 distinct IDs in shared metric fixture (40k sparse plus 1k dense); n≈1080 selected sparse IDs for a stage; f=n/N≈0.0263. For granule g containing g independently placed IDs, probability no selected ID in that granule is `(1-f)^g`. So expected fraction of selected granules is `q_g=1-(1-f)^g`, and expected physical rows touched approximately `N*q_g`. This neglects separate metric ranges, dense buckets and adaptive index byte limits.

Approximate granule fractions:
- g=256: q~0.9989 (virtually whole sparse row region)
- g=64: q~0.819
- g=16: q~0.347
- g=4: q~0.101

Index marks may grow ~256/g, creating a **memory and disk trade-off**. The total selected rows includes TAGS, TimeRanges and SAMPLES and will not necessarily fall by q_g. Dense IDs contribute multiple buckets, so this Bernoulli approximation is for sparse ID positions only.

## Controlled experiment
- Import the same frozen 40d fixture SHA256 `e8912028a0abf03a97b85b2d354e3aaaa91f9e4a16121e4fc606951066f562d4` into four isolated TimeSeries tables with `samples_index_granularity` 256 (default), 64, 16 and 4. All table layouts equal except the stated sample mark setting. Disable recent samples. Verify 41k unique outer series and 1m samples for each; dataset values bit-for-bit identical at source.
- Run compiled v4 binary (run 41447045583), with lab fast path ON and OFF; query whole metric, exact stage, project, and label regex over E3 and E40 for dense and sparse data. Randomized order, 2 warmups and 6 repeats; compare all output labels/timestamps/values with the g256 baseline. Collect `system.query_log` selected rows/marks, elapsed, tracked peak query memory, OS CPU, plus `system.parts` bytes and marks for physical sample tables.
- **Predeclared win threshold:** exact stage query should improve by >=2× p50 vs g256 while broad query should regress by <15% and persistent sample index bytes increase by <4×; all three conditions required to choose a candidate. If not, reject unconditional small granularity; only investigate separate table layouts for short-lived high-cardinality workloads.
- Actual selected marks can depart from q_g due primary-key range, adaptive bytes granularity, multiple parts, TimeRanges join and query cache. Report observed values; don't claim model confirmed by timings alone.
- Verify no actual new version-specific query semantics, access rules or TimeSeries option mismatches. No upstream or prod changes. The pinned O1 binary remains a lab binary and any improvement must be checked against a fresh same-data VM benchmark.
