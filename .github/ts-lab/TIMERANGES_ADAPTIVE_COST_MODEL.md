# Cost-based TimeRanges path selection — measured v11 baseline

**Source revision**: `52529f0c711566d696917678bf15e7068772fdde`, unchanged ClickHouse O1, LLVM22.1.8. **Fixture**: 1,330,000 Jenkins time series, 95,000 new/day for14d, one sample each, SHA256 `8f3a36846f80d6be5a8604d142e934399f2687094766c57bf105e43977f52858`. **Both table variants** use `Tuple(UInt64,UUID)` on TAGS and SAMPLES with identical tag-derived hashes. Variant A stores `TimeRanges`; variant B sets `store_time_ranges=0`. No C++ source patch or host binary change.

Full A/B/B/A fixture and query parity: [CI #41601352971](https://github.com/moleus/ClickHouse/actions/runs/41601352971). The table values are means of paired phase medians; large server RSS must be verified separately from the query tracker. The result does **not** imply a universal threshold.

## Measured median wall milliseconds

| Selector | Days | TimeRanges ON | TimeRanges OFF | Relative speed OFF / ON | Validity |
|---|---:|---:|---:|---:|---|
| `stage="stage-0007"` | 1 | 289.152 | 326.963 | 0.884× | pass, drift7.9% |
| `stage` | 3 | 403.894 | 375.593 | 1.075× | pass, drift8.1% |
| `stage` | 7 | 507.785 | 473.591 | 1.072× | pass, drift1.5% |
| `stage` | 14 | 539.525 | 518.516 | 1.041× | pass, drift0.7% |
| `project="project-00"` | 1 | 477.828 | 927.906 | 0.515× | pass, drift9.0% |
| `project` | 3 | 717.248 | 1075.329 | 0.667× | pass, drift2.9% |
| `project` | 7 | 957.450 | 1289.221 | 0.743× | pass, drift1.9% |
| `project` | 14 | 1637.970 | 1575.521 | 1.040× | pass, drift0.3% |
| Whole metric | 1 | 672.231 | 2392.443 | 0.281× | **INVALID**: control drift45.7% |
| Whole metric | 3 | 2041.261 | 3205.346 | 0.637× | pass, drift0.6% |
| Whole metric | 7 | — | — | — | **MEMORY_LIMIT_EXCEEDED** at server RSS~4.66GiB |
| Whole metric | 14 | — | — | — | Not run after 7d OOM |

## Why raw `read_rows` is insufficient

Example `project` at 1d:

- `TimeRanges` ON: `read_rows`≈3,855,344, `read_bytes`≈345MB, query CPU≈892ms, query memory≈42MB.
- OFF: `read_rows`≈2,660,000, `read_bytes`≈291MB, query CPU≈1420ms, query memory≈146MB.
- Despite reading **fewer total rows/bytes**, OFF is **much slower**, because those row counts mix TAGS/TimeRanges and SAMPLES with very different decode, set, and aggregate costs. Count per inner table, data type, decompression CPU, selected marks and post-filter rows separately.

Source [`StorageTimeSeriesSelector.cpp`](https://github.com/moleus/ClickHouse/blob/52529f0c711566d696917678bf15e7068772fdde/src/Storages/StorageTimeSeriesSelector.cpp) lines486–572 constructs `LEFT JOIN` with TimeRanges to filter TAGS IDs; lines1274–1325 always join when a TimeRanges target exists and the time range is selective. `applySettingsForSelectIDsQuery` forces `full_sorting_merge` to use sorted IDs. For v11 bucketed samples, the generated ID set then filters SAMPLES; this is not a planner choice between TimeRanges vs no TimeRanges.

## Correct model and a safe future experiment

At time of planning, estimate:

```text
C_with  = C_tags(Ntags) + C_timeRanges(NrangeRead) + C_mergeJoin + C_IDset(NidTime)
        + C_samples(NidTime, selectedMarksTime, bucketWidth)
        + C_grid(NsamplesTime)

C_without = C_tags(Ntags) + C_IDset(NidTags)
          + C_samples(NidTags, selectedMarksNoTime, bucketWidth)
          + C_grid(NsamplesNoTime)

Choose without only if:
  conservative_estimate(C_without) < estimate(C_with)
  AND upper_bound_memory_without < per-query + server memory budget
  AND semantics preserved under TTL, repeated/late samples, multiple buckets and label ACLs.
```

Potential estimates:
- `NidTags`: use actual TAGS predicate scan/join cardinality or statistics on materialized `tags_to_columns` label columns and `metric_name`. Selectivity of tag conjunctions need **correlation** handling; multiplying independent NDV fractions can be wrong.
- `NidTime`: time range overlap and temporal retention synopsis. Respect correlation with tag labels, high churn and node lifetime. Do not assume static series occupancy.
- `selectedMarks`: derive from actual `(id,bucket)` primary key and sample-part statistics rather than inferring from ID match count; random IDs can touch nearly every granule despite sparse matching.
- `C_grid`: true native PromQL aggregate and decoding cost; bound peak hash tables/sets and RSS, not just `query_log.memory_usage`.
- Feedback: record estimated vs real candidate ID counts, per-table marks, CPU, query latency, memory; never train solely from one synthetic daily fixture.

For validation, add an **opt-in per-query path switch** in a lab C++ build with a shared same-source time-on table. This isolates planner choice from independent table ingest and part layouts. Then test the cost model with exact A/B/B/A query parity (1/3/7/14d, all labels/time filters), multithread counts, dense15s stable series, high-churn short-lived pods, post-merge/reinsert behavior and an OOM guard. No unconditional source default change is justified by these data.

## Recommendation at this checkpoint

Keep `TimeRanges` enabled for the general schema. The skip path is useful only if planning can bound the cost **and** the memory risk for each selector and query window. For the tested 1.33m churn fixture, selector thresholds vary sharply; hardcoding "skip after 3d" would make `project` and whole-metric queries worse.
