# ClickHouse TimeSeries v11: storage, indexes and time alignment

Source reference: ClickHouse pinned `52529f0c711566d696917678bf15e7068772fdde`, especially `src/Storages/TimeSeries/TimeSeriesSettings.cpp`, `normalizeTimeSeriesDefinitionImpl.cpp`, `StorageTimeSeriesSelector.cpp` and `src/Storages/MergeTree/MergeTreeIndexConditionText.cpp`.

The following diagrams describe the **engine source**, not a claim that the new C++ experiment is fast. Labels and IDs in the example are illustrative.

## 1. Logical data and physical indexes

```mermaid
flowchart TD
  IN["Insert: metric name, label map, samples"]
  TS["TimeSeries v11 logical table"]
  ID["Generate series ID from metric name + labels"]
  IN --> TS --> ID

  subgraph TAGS["INNER TAGS target: MergeTree, ReplacingMergeTree"]
    TR["Rows: metric_name, id, tags Map(String,String)"]
    PK["Primary/sort key: (metric_name, id) by default"]
    IDX["Native text index tags_idx
    tokenizer = keyValuePairs
    per-part token -> row postings"]
    TR --- PK
    TR --- IDX
  end

  subgraph SAMPLE["INNER SAMPLES target: MergeTree"]
    SR["Rows: id, bucket, min_time, max_time,
    samples Array(Tuple(timestamp,value))"]
    SPK["Primary/sort key: (id,bucket)"]
    PART["Partition: year-month of UTC bucket"]
    SR --- SPK
    SR --- PART
  end

  subgraph RANGE["Optional INNER TIME_RANGES target"]
    RR["Rows: id, min_time, max_time"]
  end

  subgraph RECENT["Optional INNER RECENT_SAMPLES target"]
    RC["Same series ID + 15-minute buckets by default
    TTL: 4 days by default"]
  end

  ID --> TR
  ID --> SR
  ID -. "if store_time_ranges=true" .-> RR
  ID -. "if recent_samples_ttl_seconds > 0" .-> RC

  Q["PromQL matcher: {project='p01', stage='compile'}"]
  Q --> IDX
  IDX -->|"candidate tag-row ordinals"| TR
  TR -->|"resolved series IDs"| IDSET["Selected ID set"]
  RR -. "optional time-range pruning / JOIN" .-> IDSET
  IDSET -->|"id + time bucket seek"| SPK
  SPK --> SR
  SR --> ROLL["Per-series window evaluation"]
  ROLL --> GROUP["Group by stage, sum/avg"]
  GROUP --> OUT["Output timestamps, labels, values"]
```

**Index detail.** The built-in `tags_idx` exists in source; it is not an index added by our experiment. It uses `text(tokenizer=keyValuePairs)` on `tags`. It maps tokens to *row positions within a MergeTree data part*. Those positions identify matching tag rows. Tag rows carry series IDs, which locate samples through the **separate** samples-table primary key. A text-index row position is not itself a globally stable MetricID. The candidate patch tests whether `mapContainsKeyValue(tags, key, value)` activates the existing index more efficiently than `tags[key] = value`, while keeping the same exact row semantics.

**Data layout.** The default samples bucket is **3,600 seconds**. A bucket contains all samples of ONE series whose timestamps fall in that hour. The recent target uses **900-second buckets** by default and has a **345,600-second (4-day) TTL**, unless changed or disabled. The main samples target uses UTC calendar-month partitioning by default and a granularity of **256 bucket rows**. The default tags primary/sort key is `(metric_name,id)`; a custom `TAGS ORDER BY` can replace it, but benefits only compatible predicates.

## 2. Example: how the same ID connects three targets

```mermaid
flowchart LR
  M["metric: jenkins_avs_build_stage_duration
  project=p01; stage=compile; build=42"]
  I["series ID: ID_A
  derived from metric + all labels"]
  T["TAGS row
  metric_name=jenkins...
  id=ID_A
  tags={project:p01,stage:compile,build:42}"]
  X["tags_idx
  token(project,p01) -> tag row T
  token(stage,compile) -> tag row T"]
  R["TIME_RANGES row
  id=ID_A
  min=max=10:16:20"]
  S["SAMPLES row
  id=ID_A
  bucket=10:00:00
  samples=[(10:16:20,42.1)]"]
  M --> I
  I --> T
  I --> R
  I --> S
  X -->|"posting row index, then read ID_A"| T
  T -. "ID_A" .-> S
  R -. "window pruning ID_A" .-> S
```

When a Jenkins build produces **one point per series**, the tags and time_ranges metadata can dominate the cost. The value `42.1` is a sample value, not the numeric series ID. Multiple builds with different `build` labels have **different series IDs** even if they share the same `stage` label. Many IDs must be aggregated to produce one line per stage.

## 3. How bucket boundaries and evaluation windows align

```mermaid
flowchart TB
  B0["SAMPLES bucket 10:00–11:00 UTC
  ID_A → (10:16:20, 42.1)
  ID_B → (10:29:00, 35.0)"]
  B1["SAMPLES bucket 11:00–12:00 UTC
  ID_C → (11:05:00, 50.0)"]
  W["Evaluate at 12:00, avg_over_time[2h]
  reads samples in (10:00, 12:00]"]
  P["First compute per-ID window value
  ID_A=42.1, ID_B=35.0, ID_C=50.0"]
  G["Then avg by(stage): (42.1 + 35.0 + 50.0) / 3
  only if all three IDs have stage=compile"]
  B0 --> W
  B1 --> W
  W --> P --> G
```

The **stored bucket** is not an evaluation window. A 1h stored bucket is an IO unit; PromQL may need 5m, 2h or a 14-day sliding window. Samples exactly on the right edge of a Prometheus window are included; the left edge is excluded. Correct evaluation requires this boundary behavior, as well as duplicate-timestamp handling, NaN/staleness, reset/extrapolation for rate(), offsets, and groups with missing labels.

For `avg by(stage)(avg_over_time(m[1d]))`, use two stages: first compute the average **within each series and time window**, then average those per-series results across the group. Replacing it with a flat average over all raw samples changes the weights for series that contain different numbers of points. The direct C++ grouped-avg experiment tries to avoid carrying a full tag map and a dense grid through every intermediate aggregation state.

## 4. Comparison with VictoriaMetrics

```mermaid
flowchart LR
  V["VictoriaMetrics inverted index"] --> VPL["metric name / label postings
  -> intersect MetricIDs"]
  VPL --> VDATA["Time series sample blocks by MetricID"]
  VDATA --> VROLL["Per-series rollup + grouped aggregate"]

  C["ClickHouse native tags_idx"] --> CPOST["token postings -> tag-row IDs"]
  CPOST --> CID["tag rows -> series IDs"]
  CID --> CSAMP["SAMPLES key (id,bucket), monthly parts"]
  CSAMP --> CROLL["timeSeries*ToGrid, avgForEach/sumForEach"]
```

The main distinction is *where* the index resolves to an ID and how the samples are organized. VictoriaMetrics postings directly list stable MetricIDs in an inverted index. ClickHouse text postings identify tag **rows inside parts**; then it must resolve those rows to series IDs and find samples. Neither approach eliminates the cost of per-series rollup. The new lab experiments target BOTH the label predicate and the intermediate grouped-aggregate work.

## Measured constraints, not yet solved

- Earlier cross-engine benchmark: 282,036 one-sample Jenkins series, default CH TimeSeries grouped `avg` p50 about 1.5 seconds, VM about 0.5–0.6 seconds.
- Same TimeSeries inner targets with separate SQL per-series grouped avg gave 476 ms p50 and ~128 MB query memory; direct C++ implementations are not yet this fast.
- Original C++ direct inner scalar argMax path p50 803 ms in one A/B/A run but the following native groupArray variant p50 1,758 ms in a different A/B/A run.
- New experiments: late tags join after per-series/window aggregation (remove Map from GROUP BY keys) and direct exact `mapContainsKeyValue` for the existing text index.
- Regular dense metric processing, 13.4M live series, 650–800K samples/s, 500+ new series/s, 15s HA dedup and late writes remain mandatory production gates.
