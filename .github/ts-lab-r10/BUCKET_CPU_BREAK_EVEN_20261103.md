# R10: Choose SAMPLES bucket duration from query mix, not only physical rows

Date: 2026-11-03. This is a **derived decision model**, not a new CI
experiment. Source of each input: source-pinned same-runner nested native
A/B/B/A [#41619669176](https://github.com/moleus/ClickHouse/actions/runs/41619669176),
[immutable full record](../ts-lab/evidence/R10-alignment-factorial-41619669176.json),
downloadable complete `report.json` artifact `13222667352`.
Stock O1 ClickHouse source `52529f0c711566d696917678bf15e7068772fdde`,
same executable SHA256
`f0a2fa1f814c58853f43423705cac10ab9df3bfe5887e27ebe9620ba7cb9660e`.
3d multi-cadence 400-slot synthetic gauges, 28,896 IDs, 2,488,320 points.

## Exact native CPU cost per completed query

Table entries are the **mean of two per-panel mean/median CPU results**,
all from one runner, with fresh ClickHouse processes and nested A/B/B/A.
Both storage configurations have RAW_UUID ID encoding. The candidate
sets `samples_bucket_step_seconds=10800` instead of 3600.

| Query | Aligned 3600→10800 CPU ms | Shifted 3600→10800 CPU ms | Shifted candidate − baseline |
|---|---:|---:|---:|
| `node_broad` | 181.360→179.266 | 309.884→259.293 | **−50.591 ms** |
| `pod_namespace` | 65.838→60.417 | 82.256→106.758 | **+24.502 ms** |
| `pod_broad` | 190.728→175.110 | 211.151→231.762 | **+20.611 ms** |
| `burst15` | 46.762→49.619 | 50.683→68.911 | **+18.228 ms** |

All four selectors and both time alignments have two valid full-native
ABBA panels; all exact output parity/physical count gates pass.
**Do not mix** an aligned baseline query's absolute CPU with a shifted
candidate query's absolute CPU. The rows above always compare the
*same fixture* and only the bucket step.

## A falsifiable CPU break-even rule

Let `λ_n, λ_s, λ_p, λ_15` be the observed completed query arrival rates
(in requests/second) for these four workload classes; their units matter.
For **this shifted synthetic fixture only**, the predicted change
in CPU-seconds consumed per second is

```text
ΔCPU_seconds_per_second
 = 0.001 * (-50.591 λ_n + 24.502 λ_s + 20.611 λ_p + 18.228 λ_15)
```

If `ΔCPU < 0`, the 10800s layout uses less total *query* CPU at the
measured execution model; if `ΔCPU > 0`, 3600s uses less. This only
models measured queries, and intentionally omits write/merge CPU, cache,
cross-request contention, p95/p99, retention, and allocator RSS.

- With just `node_broad` and `pod_namespace`,
  10800s has lower query CPU only if
  `λ_n / λ_s > 24.502 / 50.591 ≈ 0.4843`.
  That means node-wide requests must exceed **32.6%** of the count of
  these two request types, under the measured costs.
- If all three other request types have the **same query arrival rate**
  and the node request share is `p`, their average penalty is
  `(24.502+20.611+18.228)/3 ≈ 21.114ms`.
  The CPU break-even node share is
  `p > 21.114 / (21.114+50.591) ≈ 29.45%`.
- Equal frequency of all four query types (`p=25%`) gives
  `(-50.591+24.502+20.611+18.228)/4≈+3.188ms/query`,
  so **10800s is worse** for that particular synthetic mix.
- The aligned fixture gives the opposite direction for equal frequency:
  `(-2.094−5.421−15.618+2.857)/4≈−5.069ms/query`
  for 10800s. This is the test's timestamp-alignment interaction,
  not a forecast for real Kubernetes.

For decisions with real traffic, measure query arrival rates and the
distribution of `__name__`, labels, lookback, time range, concurrency,
and staggered start times. Do not infer the mix from one Jenkins query or
run a single broad selector as an all-query representative.

## CPU is only one objective

The same 3d test produced these three-target compressed disk totals:

| Data shape | 3600s | 10800s | 10800s saving |
|---|---:|---:|---:|
| Aligned node starts | 7,424,806 B | 6,833,238 B | 8.0% |
| Shifted node starts | 8,228,678 B | 7,689,765 B | 6.5% |

The separate [fresh-process R10 write+merge test #41616086196](https://github.com/moleus/ClickHouse/actions/runs/41616086196)
demonstrated explicit aligned-data merge CPU 0.825→0.720s and
ingest CPU 1.590→1.520s for 2.49m points, but was not performed
on the shifted fixture. **Do not blindly combine** the aligned merge CPU
saving with shifted-read cost estimates. Peak RSS, p99 and effective
write/merge throughput must be included before operational promotion.

## Storage engine constraint

The pinned source [`StorageTimeSeriesSelector::readImpl`](https://github.com/moleus/ClickHouse/blob/52529f0c711566d696917678bf15e7068772fdde/src/Storages/StorageTimeSeriesSelector.cpp#L1233-L1255)
loads `samples_bucket_step_seconds` from the **table storage settings**
and passes that physical step into its SAMPLES reader. It does not
choose between multiple main SAMPLES bucket lengths for each query.
The only built-in alternative target is **RecentSamples**, selected
when the entire range fits its configured TTL, and it has its own
`recent_samples_bucket_step_seconds` storage setting.

Therefore any new per-query `3600/10800` chooser outside this existing
RecentSamples TTL path would require an extra physical representation
or a storage-engine change, with additional writes/merge/storage and
query-plan correctness conditions. R04 owns RecentSamples research;
do not start a duplicate prototype simply to tune a single R10 fixture.

**Decision at this gate:** 10800s can lower CPU for broad node queries
but makes namespace and 15s pod queries much more expensive when node
starts are unaligned; there is no generally dominant bucket size.
Do not change the global default. Extend the model only with
source-pinned, same-runner workloads that include real-shaped HPA
and continuous concurrent writes, and obtain actual query-mix weights.
