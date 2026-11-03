# R35: measured native TimeSeries execution improvement

## Result

R35 removes a redundant intermediate aggregation from native PromQL when a conservative AST proof makes it unnecessary. The compiled candidate passed semantic, physical-plan, native performance, dense-counter, and two-client memory tests.

| Full native 1.33m-series whole-metric query | Stock | R35 |
|---|---:|---:|
| Wall time, ms | 11974.00 | 7777.25 |
| Query CPU, ms | 11861.79 | 7644.27 |
| Query memory, bytes | 1416391850 | 870315896 |
| Sampled process RSS, bytes | 1956302848 | 1464402944 |

This is about 1.54x faster, with 35.6% less query CPU and 38.6% less tracked query memory. The repeated whole-query controls differ by 0.63% for stock and 1.13% for the candidate. These are local synthetic-workload results, not a production SLA or a VictoriaMetrics comparison.

A separate dense-counter test, 8192 series x256 samples at15-second intervals, reduced native `avg by(stage)(rate(dense_counter[5m]))` wall time from179.75 to148.5ms. Query memory was nearly unchanged. Do not apply the sparse memory percentage to dense series.

At two simultaneous broad requests and a2.85GB server memory limit, stock completed6/12 requests and the candidate completed12/12, including warmups. Stock stopped exactly one request in each wave with total-memory-limit error241. Excluding warmups: stock4/8, candidate8/8. All successful outputs matched the complete oracle. This is a finite capacity test, not a general safe-concurrency limit.

## Evidence

- [Large native ABBA, dense control and limitations](../ts-lab/evidence/R35-native-1330k-local-20261104.json).
- [Two-client capacity, including every stock failure](../ts-lab/evidence/R35-two-client-capacity-local-20261104.json).
- [Source proof and native plan guards](SOURCE_PROOF.md).
- [Source-pinned C++ patch helper](apply_metric_drop.py).
- [Candidate build41732238195](https://github.com/moleus/ClickHouse/actions/runs/41732238195).
- [Separate R34 sparse-map results](../ts-lab/evidence/R34-sparse-map-local-20261104.json). R34 is not included in R35; combined gains are unmeasured.
- [R32 negative result](../ts-lab/evidence/R32-local-native-133k-20261104.json). Do not promote the earlier finalizer-only patch as a speedup.

## What changed

Stock computes the per-series range function, removes the metric name, groups all series again to check duplicates after name removal, then performs the requested outer aggregation.

For a direct selector with one exact nonempty metric name, removing the constant name cannot merge two distinct full-label groups. If the immediate ordinary parent also discards that name, the parent can perform the only necessary label projection. R35 skips the intermediate label copy and duplicate-check aggregation. It retains the original per-series range function and final aggregate. The positive plan changes from3 Aggregating nodes to2, with identical inner read rows/bytes.

The fallback remains active for metric-name regex, multiple metrics, `by(__name__)`, `without`, offsets, subqueries, binary operations, topk and root range functions.147 native compatibility cases passed, including preservation of the mixed-metric duplicate-series error.

## Build identities

Pinned source: `52529f0c711566d696917678bf15e7068772fdde`.

Stock executable: `5d629ddfda91919402fdf8475ec3da9fe718d48ef386bc51be677264df6dc9ef` from run41685467438.

R35 executable: `68d6e8f954a44beeb137acea61b0b77afac32cc705e59404413c233b57e6a06b` from run41732238195.

Both use LLVM22.1.8, Release O2, jemalloc, and identical recorded CMake options. The only candidate source delta is `applyFunctionOverRange.cpp`. No R32/R34/metadata-JOIN patch is included.

## Reproduce the large native comparison

Use a dedicated Linux scratch directory with Python3.11+, cgroup-v2 visibility and the verified executables. Never point this harness at production or an existing database directory. The server binds loopback ports18199/19099 and rejects an occupied HTTP port.

Set `TS_LAB_ROOT` to that scratch directory. Extract stock to `$TS_LAB_ROOT/r29-stock-o2/bin/clickhouse` and candidate to `$TS_LAB_ROOT/r35-metric-drop-o2/bin/clickhouse`. Verify both archive and executable checksums. Use the same CPU/memory envelope for both variants; the recorded run had4 CPU quota cores and a4GiB cgroup memory limit.

From this directory:

```sh
python native.py --builds 2500 --work native-large
python guard_suite.py stock
python guard_suite.py r35
python run_phase.py stock 40 --builds 2500 --work native-large --label r35large --reps 2
python run_phase.py r35 41 --builds 2500 --work native-large --label r35large --reps 2
python run_phase.py r35 42 --builds 2500 --work native-large --label r35large --reps 2
python run_phase.py stock 43 --builds 2500 --work native-large --label r35large --reps 2
```

Each phase starts a new server and uses the same stored parts. Each selector has2 warmups plus2 measured requests. Every response is checked against an independent generator-derived oracle. The runner retains incremental output and waits for all terminal query-log records.

Report means of the two within-phase medians and control drift, not unqualified p50/p95/p99. There are too few measured requests for tail-latency claims. Selective stage/project latency gains are less certain because of drift; their tracked-memory reductions are recorded separately.

The original local scripts, complete raw JSON, native output vectors, semantic fixtures, patches and file checksums are also included in the conversation evidence archive. The public runtime adds scratch-directory configuration and startup cleanup; those changes do not alter query construction or the result oracle.

## Promotion status

Research candidate accepted for the next gate. Master, upstream and production were not changed. Remaining production gates include longer open-loop mixed-selector load, concurrent ingestion/merges, retention, broad label distributions, tenant/ACL checks and distributed execution. Do not multiply isolated R34/R35 gains without a combined build and measurement.
