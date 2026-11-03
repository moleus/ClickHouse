# R15 — Day-partitioned SAMPLES: source and mark occupancy feasibility

Status: **synthetic source/math feasibility only**. No native ClickHouse EXPLAIN, partition pruning, query CPU, or end-to-end latency claim. Owner [R15 root](../claims/roots/R15.json); [primary claim](../claims/R15-samples-calendar-partition-pruning-v1.json). Independent of R10 bucket duration, R05 ID filtering, R14 precomputed dashboard snapshots.

## Pinned source contract

ClickHouse source \`52529f0c711566d696917678bf15e7068772fdde\`:
- [StorageTimeSeries.cpp](https://github.com/moleus/ClickHouse/blob/52529f0c711566d696917678bf15e7068772fdde/src/Storages/StorageTimeSeries.cpp) documents default v11 SAMPLES \`PARTITION BY toYYYYMM(toDateTime(bucket,'UTC')) ORDER BY(id,bucket) SETTINGS index_granularity=256, index_granularity_bytes=524288\`. It also documents explicit \`SAMPLES INNER ENGINE\` customization.
- [TimeSeriesSettings.cpp](https://github.com/moleus/ClickHouse/blob/52529f0c711566d696917678bf15e7068772fdde/src/Storages/TimeSeries/TimeSeriesSettings.cpp) defines \`samples_partition_by\` override, \`samples_index_granularity\` and \`samples_bucket_step_seconds\` as independent TimeSeries settings.
- [StorageTimeSeriesSelector.cpp](https://github.com/moleus/ClickHouse/blob/52529f0c711566d696917678bf15e7068772fdde/src/Storages/StorageTimeSeriesSelector.cpp) derives a bucket condition for SAMPLES and an ID set from TAGS/TimeRanges. **A bucket condition does not, by itself, prove that ClickHouse prunes \`toDate(bucket)\` partition keys**; this must be measured in \`EXPLAIN indexes=1\` and \`SelectedMarks\`.

Candidate DDL idea for a **new** synthetic table only: keep source version, raw-UUID ID, 3600-second buckets and all other targets unchanged, but set SAMPLES \`PARTITION BY toDate(toDateTime(bucket, 'UTC'))\` instead of default \`toYYYYMM(toDateTime(bucket, 'UTC'))\`. An alternative table-setting expression is \`samples_partition_by\`. The exact syntax and generated DDL need a source-pinned stock binary test before calling the intervention valid. Disable recent-samples \`recent_samples_ttl_seconds=0\` in BOTH tables: otherwise native queries within recent TTL might read the other target and produce a false comparison. Do not change production or existing lab tables.

## Conditional math: when time-only pruning fails inside a monthly id-first part

Assume D distinct calendar days of uniformly distributed series IDs, one \`(id,bucket)\` row per ID, independent of its day. If a merged monthly mark covers g distinct IDs and the query covers w days, the probability that a mark **contains** at least one matching-day row is

\`P(hit) = 1 - (1 - w/D)^g\`.

This is an **expected occupancy**, not a ClickHouse primary-key index estimator and not a universal physical bound. Even when a mark contains no match, ClickHouse's min/max key envelope may select it. When a mark contains a matching row, the mark cannot be discarded by a correct index alone.

For D=14, g=256, w=1: P(hit)=**0.999999994236**. Thus in a synthetic month-sorted-by-random-ID part, a date predicate alone has virtually no effective mark-level selectivity. This explains why **day partition pruning** could have high leverage independent of smaller SAMPLES buckets. In a UTC day-partitioned schema, partitions outside the query date can be discarded *before* reading marks, provided the partition key is recognized by the query optimizer.

## Deterministic result, not a native benchmark

[Model](r15_partition_oracle.py) / [four tests](test_r15_partition_oracle.py). Local Python 3 ran **4/4 tests successfully**. Fixed seed 20261103, 14 days ×95,000 distinct uniform random UInt64 IDs, one row/ID, all data within one default monthly partition, 256 rows/mark, one merged monthly part or one merged part per day:

| Query width | Monthly marks containing day(s) | Required daily-partition marks | Ideal SAMPLES mark ratio |
|---:|---:|---:|---:|
| 1d | 5,196 | 372 | 13.9677x |
| 3d | 5,196 | 1,116 | 4.6559x |
| 7d | 5,196 | 2,604 | 1.9954x |
| 14d | 5,196 | 5,208 | 0.9977x |

The 14-day full-range model exposes a negative: daily partitions have slightly more total marks due to per-day rounding. A granularity=1 counterexample has **no advantage** for a one-day query because monthly marks already contain exactly one row. Neither model measures compressed bytes, real marks selected, average samples/series, block decoder cost or MergeTree parts. Real day partitions may have multiple parts per day and much more merge/metadata overhead.

## Isolate actual end-to-end win with a cheap same-binary experiment

1. Reuse SHA-verified stock O1 executable/fixture. Produce two new tables, change **only** SAMPLES partitioning. Force \`recent_samples_ttl_seconds=0\` on both. Keep \`samples_bucket_step_seconds=3600\`, ID type, ID generator, TAGS and TimeRanges settings identical.
2. Validate DDL with \`SHOW CREATE\` and metadata. Insert identical pre-hashed deterministic series; confirm input hashes, unique ID, point counts, native nonempty query labels/timestamps/values and per-target compressed bytes. Prefer same merged state (explicit \`OPTIMIZE FINAL\` on all targets before read test), not only \`INSERT\` completion.
3. For 1/3/7/14d native selectors including broad whole-metric and narrow stage/project, measure \`EXPLAIN indexes=1\`: **Partition/MinMax/PrimaryKey granules**, \`SelectedMarks\`, \`ReadCompressedBytes\`, \`read_rows/read_bytes\`, query CPU, wall time, RSS. The generated \`WHERE bucket\` must actually support day partition pruning. Run 1/4/8 clients only after basic parity and pruning pass.
4. Include write and merge CPU, part counts, part metadata, partitions touched per INSERT batch, disk bytes/sample and background merge backlog. A 14d range cannot enjoy the same pruning advantage; broad/full-window and long-lived series are required regression gates.
5. A/B/B/A same runner, fixed binary/compiler/settings, 2+ warmups, 4+ measurements, control drift thresholds and exact native output parity; no C++ build needed. If no source-pinned runner or artifact access, stop and record uncertainty.

## Rejection conditions and risks

- If the planner cannot prune \`toDate(bucket)\` from generated bucket predicates, the main optimization premise fails regardless of mathematical occupancy.
- If the workload's queries are mostly 14d+ or narrow ID selectors, day partitioning may be neutral or worse.
- Many small daily partitions can increase insert fanout, part counts, merge and metadata CPU, cold storage overhead and object-store request counts. The invariant is full-system cost and performance, not selected marks alone.
- Series that span multiple UTC days appear in multiple partitions and can increase write amplification. Bucket floor and timezone boundary correctness matter; test late, out-of-order and boundary samples.
- There is **no** measured 14x ClickHouse acceleration: 13.97x is only the ratio of simulated positive SAMPLES granules in the stated one-point-per-ID model.

**Decision:** strong, falsifiable source-backed candidate for a cheap schema-only native test; no global settings change and no production recommendation.

## Independent CI verification

[Minimal GitHub Actions #41622964101](https://github.com/moleus/ClickHouse/actions/runs/41622964101) finished **success**. Inspected decoded job logs: **4/4** unittest cases OK, exact deterministic full 1.33-million-series surrogate output includes 5,196/372 marks for 1d and 5,196/5,208 for 14d. This is still **not** real ClickHouse EXPLAIN or `SelectedMarks`.

## 2026-11-03 native experiment started: source-specific Amdahl gate

[Native month/day stock-O1 ABBA workflow](../../workflows/ts-lab-r15-native-calendar-20261103.yml) is triggered by a workflow-path-only change, run [41625086352](https://github.com/moleus/ClickHouse/actions/runs/41625086352); inspect live outcome before claiming success. [Benchmark source](r15_native_partition_probe.py) uses a **new independent smaller** 14-day ×5,000 series/day (70,000 total) deterministic fixture, same v11 and raw UUID in both layouts, identical one-hour SAMPLES buckets and \`recent_samples_ttl_seconds=0\`. DDL explicitly changes **only** \`PARTITION BY\` of SAMPLES; all other layer engines remain unchanged. Samples are one-point-per-unique-ID; no production/human data. Native A/B/B/A is performed for broad 1/3/7/14d and stage-exact 1/14d after SHOW CREATE, explicit merges, immutable logical ID digest and native output parity.

Pinned [StorageTimeSeriesSelector.cpp lines 678-743](https://github.com/moleus/ClickHouse/blob/52529f0c711566d696917678bf15e7068772fdde/src/Storages/StorageTimeSeriesSelector.cpp) builds \`bucket>=rounded_min AND bucket<=max\` **and** min/max actual sample-time predicates before \`id IN(select IDs from TAGS/TimeRanges)\`. This makes an eligible **bucket predicate** source-supported, but the day-partition pruning still depends on ClickHouse's actual KeyCondition/partition-analysis engine. Direct \`EXPLAIN indexes=1\` and \`SelectedMarks\` are the decisive evidence, not source intent.

### End-to-end speed ceiling: why 14x SAMPLES marks need not be 14x PromQL latency

Let \`f\` be the fraction of baseline query cost due to physical SAMPLES work, excluding TAGS, TimeRanges, planner, aggregation and network. Suppose day partitioning makes SAMPLES *exactly* 14x cheaper with zero overhead. The **optimistic** wall speedup is \`1/(1-f+f/14)\`:

| SAMPLES fraction of total work, f | Optimistic whole-query ceiling |
|---:|---:|
| 50% | 1.87x |
| 80% | 3.89x |
| 90% | 6.09x |
| 95% | 8.47x |
| 99% | 12.46x |

For a whole-query 5x gain, \`f>=0.86154\` is necessary in this simplified model; for 10x, \`f>=0.96923\`. New partition overhead and unchanged metadata scans make the true ceiling lower. This experiment must report both **direct SAMPLES marks** and **full native PromQL latency/CPU**, not substitute one for the other. The smaller 70k test proves only layout feasibility; the canonical 1.33m high-churn workload remains an untested follow-up.

**Important**: do not interpret a GitHub Actions green job as a scientific result without reading its artifact, inspecting EXPLAIN, verifying ABBA drift and full native equality. If the first run fails on DDL/parser or setup, keep failure and repair the benchmark, rather than changing multiple variables or calling it a failed engine optimization.

## 2026-11-03: **Native source-pinned result, #41625086352 — PASS (bounded synthetic)**

[GitHub Actions](https://github.com/moleus/ClickHouse/actions/runs/41625086352), [immutable machine evidence](../evidence/R15-native-calendar-41625086352.json), [exact benchmark script](r15_native_partition_probe.py) and original 16,661-byte uploaded GitHub Actions artifact. Independently downloaded and SHA256-verified the artifact ZIP: \`58a3e3586c172c304687033e609d3a9bf1ac40b5484de3db90edd45e8534670e\`. The server's \`BUILD_INFO\` confirms pinned ClickHouse source \`52529f0c711566d696917678bf15e7068772fdde\`, LLVM 22.1.8, and **O1 unmodified stock C++**; binary SHA256 \`f0a2fa1f814c58853f43423705cac10ab9df3bfe5887e27ebe9620ba7cb9660e\`.

This is a smaller *new* deterministic independent fixture, **70,000 one-sample-per-ID series over 14 days**, SHA256 \`351dff6b4f49986bb7f0f586459b86b918d99fd4d4cbb0b3293b4dfa680a8efb\`. Both tables use TimeSeries v11 RAW UUID, one-hour buckets, RecentSamples off, identical serialized inserts and ID digest, 70,000 actual samples, explicit OPTIMIZE of all targets. SHOW CREATE confirms \`ORDER BY(id,bucket)\` and default granularity 256/524288 in both; **sole physical schema change is SAMPLES PARTITION BY UTC month vs UTC day**. One merged SAMPLES part vs 14 daily merged parts. No private telemetry.

### Direct physical SAMPLES probe, exact EXPLAIN and QueryFinish

| Query | Monthly selected marks | Daily selected marks | Monthly read rows | Daily read rows | Monthly direct CPU | Daily direct CPU |
|---|---:|---:|---:|---:|---:|---:|
| 1d | 273 | **20** | 70,000 | **5,000** | 10.006ms | **3.118ms** |
| 3d | 273 | 60 | 70,000 | 15,000 | 10.872ms | 5.281ms |
| 7d | 273 | 140 | 70,000 | 35,000 | 11.178ms | 7.375ms |
| 14d | **273** | 280 | 70,000 | 70,000 | **9.000ms** | 10.901ms |

**The decisive engine proof exists**: direct 1d EXPLAIN reports \`Min-Max ... Parts: 1/14 Granules: 20/280\` with a day partition; monthly reports \`Parts: 1/1 Granules: 273/273\`. This is **actual** source-pinned engine pruning; the earlier mathematical 14x mark model was directionally correct. The selected-mark ratio on native physical data is 273/20=**13.65x**, not a wall-speed ratio.

### End-to-end native PromQL, same binary and A/B/B/A

6 independent cases × 4 phases \`month/day/day/month\` × (2 warmups+4 measured)=**144 native timed queries**. All cases have exact **nonempty** label/time/value parity, all baseline A/A drift gates under 25%, all QueryFinish records present, no failures or OOM. No fresh server between phases; p95 per phase exists in artifact, **not** a concurrent throughput/p99/RSS claim.

| Native case | p50 month → day | Speedup | Query CPU month → day | Selected marks month → day | A/A p50 drift |
|---|---:|---:|---:|---:|---:|
| broad1d | 67.982→55.447ms | **1.226x** | 118.02→83.44ms | 291→38 | 1.65% |
| broad3d | 87.545→74.500ms | **1.175x** | 182.80→146.16ms | 291→78 | 5.20% |
| broad7d | 142.682→140.574ms | 1.015x | 307.94→288.98ms | 291→158 | 12.47% |
| broad14d | 243.618→240.857ms | 1.011x | 510.95→515.44ms | 291→298 | 4.77% |
| exact-stage1d | 40.079→34.255ms | **1.170x** | 54.19→41.60ms | 124→38 | 1.07% |
| exact-stage14d | 57.487→59.423ms | **0.967x** (regression) | 99.68→97.09ms | 291→298 | 1.62% |

**New upper-bound insight:** broad1d \`read_rows\` fell only **210,000→145,000** even though SAMPLES fell **70,000→5,000**. Thus around 140,000 query-read rows remain, consistent with scanning the 70,000-row TAGS and 70,000-row TimeRanges metadata layers (table part row counts and source path confirmed). Exact per-operator attribution is still a separate probe. No SAMPLES-only layout change can eliminate this fixed metadata work. The high-potential next research is to avoid full metadata materialization while preserving tag/time semantics; coordinate with [R05](../claims/roots/R05.json) and [R07](../RESEARCH_COORDINATION_20261103.md), not another SAMPLES index-granule sweep.

### Disk/write observations and limits

After a single explicit merged load, SAMPLES compressed bytes are **2,289,798 monthly vs 1,422,855 daily (−37.86%)**; all three internal targets together **6,712,017 vs 5,845,074 bytes (−12.92%)**, with extra partitions (14 versus 1). These are **measured for this one synthetic fixture only**. Each table received 35 HTTP ingest batches and 14,181,160 raw input bytes. Single-pass insert wall was 2.840s monthly vs 2.842s daily, while explicit OPTIMIZE SAMPLES was 0.142 vs 0.071s; **these values are not paired repeated write/merge evidence**. Do not claim better production disk footprint or write cost.

**Decision: CONDITIONALLY ACCEPT** UTC daily SAMPLES partitioning as a short-lookback read-path hypothesis; **REJECT universal default replacement**. The 7/14d full-native latency effect is zero or mildly adverse, and the one-point synthetic case is not a 400-node HPA workload or mixed 15s/60s/300s cadence. Next: correct combined cost-model and test a real-shaped 14d 1.33m fixture plus long-lived series only if the expected full-native benefit clears the metadata-cost lower bound. The immediate larger target is **metadata/time selectivity**, not repeat partition-only microbenches.
