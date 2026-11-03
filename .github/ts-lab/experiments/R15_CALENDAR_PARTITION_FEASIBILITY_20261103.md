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
