# SAMPLES index granularity: pre-registered mathematical cost and falsification

2026-11-02; pinned ClickHouse 52529f0c711566d696917678bf15e7068772fdde. No C++ change, fork lab only. Original 14x95000 fresh Jenkins rows, input fixture SHA256 8f3a36846f80d6be5a8604d142e934399f2687094766c57bf105e43977f52858.

## Established evidence

Native 14d exact stage CH ~484ms versus VM ~77.5ms (41486787371). Isolated diagnostic 41492609952: exact selected IDs 35000; SAMPLES id IN 35000 IDs still reads 2.66m total rows (1.33m TAGS + 1.33m SAMPLES). SAMPLES native physical EXPLAIN: 7 parts, 5197/5197 granules touched. Current SAMPLES sorting key (id,bucket), granularity256, id is hash of all labels, so stage labels are approximately uniform over id.

## Mathematical derivation (conditional)

N=1330000 physical samples/rows, n=35000 selected unique IDs. Selected fraction p=n/N=0.02631578947. If labels are independent across the sorted hash IDs and a granule contains g rows, P(granule touched) = 1-(1-p)^g. E[rows in touched granules] is approximately N * P(touched); E[granules touched] approximately (N/g)*P(touched). This is an expectation under uniform mixing, NOT a theorem for arbitrary label layouts or index selectors. The observed 5197/5197 at g=256 is consistent.

| g | P touched | Expected rows | Expected touched granules |
|---:|---:|---:|---:|
| 8 | 19.212% | 255524 | 31941 |
| 16 | 34.734% | 461956 | 28872 |
| 32 | 57.403% | 763459 | 23858 |
| 64 | 81.855% | 1088670 | 17010 |
| 128 | 96.708% | 1286210 | 10049 |
| 256 | 99.892% | 1328558 | 5190 |
| 512 | approximately100% | 1330000 | 2598 |

Smaller g increases index marks and seek/scatter operations. Illustrative cost: T(g) = c_mark * E[granules touched] + c_decode * E[rows read] + c_index*N/g + memory cost + fixed. Even if g16 saves 866k rows, it must pay for ~23.7k additional touched granules, bigger sparse index, possible extra compression overhead and ingestion/merges. It can regress. For all-series query p=1, all rows are required and g256 is expected to pay fewer mark/metadata operations; no universal small-g recommendation.

## Predeclared experiment and acceptance

On same pinned unmodified ClickHouse binary, create TimeSeries tables with samples_index_granularity=16 and 64 plus existing default g256 (no recent samples table; same UUID/ID generator behavior). Ingest exactly the same frozen 1.33m JSONEachRow source, confirm row and per-stage counts. Verify identical output keys, UTC timestamps, Float64 values for exact stage and full metric 14d PromQL versus g256, preserve all failures.

Observe physical inner SAMPLES table parts, marks, bytes, granules from EXPLAIN, query_log read_rows/read_bytes/memory/CPU, /proc process and host disk counters, active merges, output samples and wall latency. Compare g256 baseline before/after treatment to detect system drift. Full SQL alone may read TAGS/TimeRanges too, so separately profile sample-only ID filter. Use sequential per-table imports and stop merges only after data is fully prepared; no uncontrolled overlapping ingest during benchmark.

A positive claim for a *specific* stage-filter workload requires (a) exact results, (b) fewer physical SAMPLES rows/granules or proven CPU path benefit, (c) no regression in broad metric query and ingestion/storage budgets, (d) stable A/B/A and no background merges. A negative result also is valuable: granularity tuning is not a solution if seek/metadata dominates. Do not compare all physical tables' mixed metrics without normalizing part layouts and CPU/cache state. No production promotion or general PromQL rewrite.
