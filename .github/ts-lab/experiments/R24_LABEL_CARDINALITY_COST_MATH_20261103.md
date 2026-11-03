# R24 — TimeSeries label-pair count at exactly equal input JSON byte volume

Source pinned to `52529f0c711566d696917678bf15e7068772fdde` (unmodified LLVM22.1.8 `-O1`, glibc binary SHA256 `f0a2fa1f814c58853f43423705cac10ab9df3bfe5887e27ebe9620ba7cb9660e`). Root `R24-equal-wire-bytes-tag-map-cardinality-cost-v1`. Unlike R23, this intentionally changes the number of labels per series, **not the order of labels in Map**. R21/R22 separately own cache capacity/expiration and are not repeated.

## Source-exact cost model

`TimeSeriesSink.cpp` executes `fillTagsColumns` for each input metric-series row, creating a vector of every tag, adding `__name__`, calling `sortTagsAndRemoveDuplicates` (`std::sort`) and evaluating the hash-derived ID before `TimeSeriesDeduplicationCache::filterOutWrittenRows`. The latter can avoid repeated TAGS writes, but **cannot** eliminate the preceding per-row label normalization and ID work. No fastpass for known repeated IDs is present in this source path.

A useful symbolic decomposition, not an instrumented CPU profile:

```
Ingest CPU ≈ ParseJSON(B,K) + BuildMap(K) + Sort(K log K)
           + HashLabelBytes(Btags) + DedupLookup
           + TimeRangesWrite + SamplesWrite
Read CPU ≈ ReadTAGS(Bstored,K) + NativeLabelCollection(K)
         + TimeRanges/SAMPLES read + Grid/Aggregation + SerializeResult
```

Here `K` is the number of normalized tag pairs, `B` is the wire length, `Btags` is all normalized tag bytes, and the costs overlap in the real engine. Equal `B` removes the simple **more network bytes** explanation but does not isolate `std::sort`, parser, hashing, MergeTree write bytes, memory allocation, or physical compression. A source-level optimization requires CPU attribution and an exact correctness proof.

## Fixed equal-byte fixture (independently computed before CI)

Four fresh-server A/B/B/A phases: A0 and A3 have 8 input labels (9 including injected `__name__`); B1 and B2 have 36 input labels (37 normalized). All use the same 4096 active series ×24 chronological 1-minute inserts = **98,304 sample points/phase**, the same source and binary, raw UUID TimeSeries v11, 1MiB tags cache and 3600-second cache TTL. All serializations use a deterministic **sorted** JSON Map key order. The common `project/rack/series` labels, metric, row order, timestamp, and values are exactly equal across variants. Each low-label row adds a longer `payload` label value of exactly **372 ASCII characters**, and each high-label row uses **8 characters** for that value, yielding **exactly the same raw JSON byte length for every corresponding row**.

- **Common projection SHA256:** `4d92a699ce39aaaa539985e8c9f03581a4316931fe5e44dfaae3901379e5192f`.
- **8-label wire SHA256:** `3c2818c73c3ddb39e72b36fb4d7ee69202dbba2d70e54cbd589dcb2073345e63`.
- **36-label wire SHA256:** `055f8e78aa8cb79321c93b5dcbd748d9afff05b6018957150d48d779479b0f44`.
- **Exact per-phase raw wire bytes:** `57,871,560` (both); **same row length at every (series,batch)**.
- Normalize per-row pairs: 8+1=9 versus 36+1=37 labels. Thus 98,304×9=**884,736** versus 98,304×37=**3,637,248** tag-pair visits (**4.11×**). An asymptotic upper-order comparison `K log2 K` is about 6.8× higher for 37 vs9, but this is **not** an observed number of comparisons and source's std::sort may behave differently on almost sorted labels.
- TAGS cache has nominal `floor(1048576/144)=7281` entries, covering 4096 stable IDs in each mode. Expected global cache hits **94,208**, misses **4096**, raw TAGS rows **4096**, and 98,304 physical sample points; the rates must **match both modes**. We do not equate these counters with CPU.
- Query exactly the same `avg by(project)(avg_over_time(tslab_r24_tags_cardinality[30m]))`, one timestamp after 24 batches, 3 warmups and 6 measured native requests per phase. An independent Python oracle checks every output group, timestamp, and float value.

## Measurement and falsifiers

The R21 tested fresh-server framework collects whole-process watchdog+worker CPU and write bytes, global cache hit/miss events, QueryFinish, full native metrics and physical TAGS/TimeRanges/SAMPLES counts. Additional R24 audit checks `min/max(length(mapKeys(tags)))` is exactly 9 or37 and that the full stable ID+Map digest is the same **within** each variant's two phases but different across variants.

Reject performance inference as `INVALID_METHOD` on mismatch of any fixed SHA, same-byte invariant, canonical core tags, 4096 stable IDs, 94,208/4096 cache counters, 98,304 points, nonempty full native labels/time/values or A/A process CPU/native p50 drift >25%. Compare *within this runner* only. A net ingest CPU effect must exceed both **5%** and the observed low-label A/A control drift to warrant a separate independent-runner replication, not a production recommendation. Report native read bytes, TAGS compressed bytes, RSS and physical parts as **different by design**, never claim physical storage parity across 8 and36 labels.

No source patch, no production changes, no scheduled workflows, no cross-workload speedup extrapolation. [Experiment code](r24_equal_wire_label_count_abba.py), [path-isolated CI](../../workflows/ts-lab-r24-label-cardinality-20261103.yml).
