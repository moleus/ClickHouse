# E04 source audit 03 — VM TSID physical locality prioritizes namespace/job and pod/instance

2026-11-03. **SOURCE-VERIFIED, NOT A NEW PERFORMANCE RESULT.** Pinned VictoriaMetrics `v1.134.0`; ClickHouse source `52529f0c711566d696917678bf15e7068772fdde`.

## Key correction

The VM TSID is **not** just a random MetricID sorted beside a metric name. Its storage sort key is `(MetricGroupID, JobID, InstanceID, MetricID)` ([`lib/storage/tsid.go::Less`](https://github.com/VictoriaMetrics/VictoriaMetrics/blob/v1.134.0/lib/storage/tsid.go#L90-L105)). For a new series, [`index_db.go::generateTSID`](https://github.com/VictoriaMetrics/VictoriaMetrics/blob/v1.134.0/lib/storage/index_db.go#L421-L435) assigns:

```
MetricGroupID = xxhash64(metric_name)
JobID         = uint32(xxhash64(first priority tag value))  // 0 when absent
InstanceID    = uint32(xxhash64(second priority tag value)) // 0 when absent
MetricID      = unique series identifier
```

The first two tag values are selected by **priority ordering**, not simply alphabetic key order. [`MetricName.sortTags`](https://github.com/VictoriaMetrics/VictoriaMetrics/blob/v1.134.0/lib/storage/metric_name.go#L569-L617) uses `normalizeTagKey` before sorting; [`commonTagKeys`](https://github.com/VictoriaMetrics/VictoriaMetrics/blob/v1.134.0/lib/storage/metric_name.go#L661-L723) has explicit tiers:

| VM first priority (job-like) | VM second priority (instance-like) |
|---|---|
| `namespace`, `ns`, `datacenter`, `dc`, `environment`, `env`, `cluster`, `service`, `job`, `model`, `type`, `sensor_type`, `db` | `instance`, `host`, `server`, `pod`, `node`, `device`, `tenant`, `client`, `name`, `measurement` |

The exact ordering among recognized tags follows priority codes, e.g. `namespace` wins over `job` when both present. The rest use their original key name ordering. Uppercase-first variants are recognized. Priorities are **source-defined defaults**, not chosen by SQL/CBO or learned from workload statistics.

VM [`part_search.go`](https://github.com/VictoriaMetrics/VictoriaMetrics/blob/v1.134.0/lib/storage/part_search.go#L60-L305) reads TSID-sorted block headers and skips by TSID/time bounds. Consequently, **the layout can physically cluster series from the same namespace (and then pod/instance) near one another**, with fewer seeks for some filtered queries. This is an architectural layout optimization *in addition to* the VM tag-to-MetricID inverted index.

## ClickHouse contrast

Pinned CH v11 default SAMPLES ([DDL](https://github.com/moleus/ClickHouse/blob/52529f0c711566d696917678bf15e7068772fdde/src/Storages/StorageTimeSeries.cpp#L1277-L1302)):

```sql
id Tuple(UInt64, LowCardinality(UUID))
ORDER BY (id, bucket)
-- id prefix = sipHash64(metric_name)
-- id UUID hash = sipHash128(tags)
```

A typical high-churn metric with many different namespace/pod tag combinations has **no explicit namespace or pod sort position** in this default ID. The label hash distributes different namespace/pod combinations across the UUID order. This may lead to more SAMPLES primary-key marks visited for a selective namespace, even when TAGS text-index matching already finds exact IDs. It is **not proved** to be the cause of the [R03 12/12 VM>CH diagnostic](https://github.com/moleus/ClickHouse/actions/runs/41624246411), which also used CH -O1 and different caches.

## Mathematical discriminating bound (hypothetical, not actual marks)

If the label selects independently a fraction `s` of series and each CH primary-key mark contains `g` distinct, uniformly distributed IDs, the probability that the mark contains any selected ID is:

`P(mark contains selected ID)=1-(1-s)^g`.

For a single namespace out of 16, `s=1/16`, and hypothetical `g=256`, the occupancy fraction is **very close to 100%**. An ideal prefix-clustered layout could instead place a namespace into approximately **1/16 = 6.25%** of marks, aside from part boundaries. **Do not claim the actual CH 256-row marks contain 256 distinct IDs**; bucketed data often has multiple rows per ID, and key-range envelopes can be selected without exact IDs, as [R05 surrogate](../ts-lab/experiments/R05_MARK_COVERAGE_FEASIBILITY_20261103.md) demonstrated.

This is a **potential mark-locality effect** distinct from per-day metadata lookup and independent of a universal regex/negative matcher plan.

## Why not change the ClickHouse ID immediately

- CH's `id` is a stable logical identity for TAGS, TimeRanges and SAMPLES. It may be used for metric-prefix ID-range pruning and for deduplication. Truncating/repacking it to make room for namespace can silently break correctness, as earlier R07 packed-ID studies warned.
- Changing `ORDER BY` to `(metric,namespace,pod,id,bucket)` would invalidate the existing native SAMPLES primary-key planning assumption `ORDER BY(id,bucket)` unless the selector is adapted. A new projection or separate physical sort key has write/merge/space costs.
- VM's 32-bit `JobID`/`InstanceID` are locality hints; full `MetricID` preserves identity. A collision does not imply two time series become one, but changes locality.
- A fixed namespace/pod layout is not globally optimal. Dense stable series and queries by other labels may have different costs. The candidate must be *adaptive to expected workload* or explicitly opt-in.

## Handoff, no duplicate experiment ownership

E03 (native CBO) should account for whether a selector's leading label is a **physical locality prefix** when estimating selected marks and plan cost. E04 supplies VM source facts and VM-only R26/R27 measurements. R05 owns CH mark-hit modeling; R07 owns time-day metadata access. No C++ change or CI launch is authorized solely by this note.

First cheap empirical check after this source proof: on the existing pinned R01 fixture, compare VM actual block seeks and CH actual SAMPLES SelectedMarks for namespace versus an equally selective non-locality label (same result cardinality), with warm/cold cache controls and matched source/binary policy; then quantify any advantage of layout independent of pure label-index search. This must be coordinated with R05 and E03 before a new canonical claim.

**Decision:** SOURCE_LOCALITY_PROOF_ACCEPT, NO_RUNTIME_GAIN.
