# Native full-PromQL CPU stacks: measured attribution (2026-11-03)

Source: 1.33m-series canonical fixture SHA256 `8f3a36846f80d6be5a8604d142e934399f2687094766c57bf105e43977f52858`, pinned O1 JOIN-override binary from run [41531656434](https://github.com/moleus/ClickHouse/actions/runs/41531656434), source SHA `52529f0c711566d696917678bf15e7068772fdde`.

Primary run: [41587498126](https://github.com/moleus/ClickHouse/actions/runs/41587498126) with zipped original `native-join-{case}/report.json` evidence. The following numbers are **sampled CPU stacks from one representative query**, not experiment medians, and their shares do not represent wall-clock fractions. This is **native full PromQL**, not SQL diagnostic. Work times of concurrent operators are **not** additive wall times.

## A direct look at the critical path

Stage-only native query 14d, one sampled run per algorithm:

| Metric | Default merge JOIN | HASH JOIN |
|---|---:|---:|
| QueryFinish wall (one run) | 883 ms | 1,779 ms |
| QueryFinish CPU (sum process CPU) | 2,232 ms | 3,432 ms |
| CPU stack samples | 1,521 | 2,636 |
| Samples with `memset` exclusive leaf | 730 (48.0%) | 1,614 (61.2%) |
| `memset` under `ReverseIndex::insert -> HashTable::realloc` | 392 (25.8%) | 543 (20.6%) |
| `memset` under `HashJoin::addBlockToJoin -> insertFromBlockImplTypeCase -> HashTable::realloc` | absent / not observed | 747 (28.3%) |
| Join operator: `MergeJoinTransform` total work | ~80 ms | n/a |
| Join operator: `FillingRightJoinSide` total work | n/a | ~1,047 ms |
| Query read bytes | ~331 MiB | ~331 MiB |

The HASH-specific `memset` branch explains roughly 747 samples of additional hot-path activity, directly under right-side `HashTable::realloc`. This is consistent with hash table allocation/zeroing pressure. It does **not** yet prove a reserve-size patch is safe or beneficial; hashing/probing and allocation both need measurement.

Stage-only 7d:

| Metric | Default merge | HASH |
|---|---:|---:|
| QueryFinish wall | 793 ms | 1,667 ms |
| CPU | 2,146 ms | 3,197 ms |
| Samples | 1,314 | 2,412 |
| Exclusive `memset` leaf | 627 (47.7%) | 1,523 (63.1%) |
| `memset` via `ReverseIndex::insert` | 464 (35.3%) | 587 (24.3%) |
| `memset` via HASH right-side build | not observed | 746 (30.9%) |
| `FillingRightJoinSide` work | n/a | ~913 ms |

Project 7d (different selector population), one representative trial:

| Metric | Default merge | HASH |
|---|---:|---:|
| QueryFinish wall | 406 ms | 548 ms |
| CPU | 1,004 ms | 1,175 ms |
| Sampled CPU stacks | 624 | 771 |
| `FillingRightJoinSide` work | n/a | ~175 ms |
| Query peak memory | ~143 MiB | ~227 MiB |

These findings explain why a universal choice of HASH is not justified by 1d direct SQL gains: the native selector must register labels, read samples and build or merge ID sets, and HASH build pressure changes with selected cardinality/time window.

## Source-level path

- `src/Storages/StorageTimeSeriesSelector.cpp`: default inner TAGS/TimeRanges join uses full sorting merge and ordered reads, unless explicitly overridden in the O1 lab patch.
- `src/Functions/TimeSeries/timeSeriesStoreTags.cpp`: stateful function stores ID→tags into a per-query `ContextTimeSeriesTagsCollector`.
- `src/Interpreters/ContextTimeSeriesTagsCollector.cpp`: `storeTagsTyped` calls `id_map.emplace` after checking existing IDs. This builds a second hashed map separate from HASH JOIN, even when the merge JOIN option is selected.
- The trace directly associates much `memset` CPU with two hash-table populations: the `ColumnUnique/ReverseIndex` path and the HASH JOIN's right-side build path. A patch aimed only at HASH JOIN cannot remove `ColumnUnique/ReverseIndex` costs.

## Next falsifiable hypotheses

1. **HASH JOIN capacity planning**: source-identify the right-side map growth/reallocation trigger and test opt-in, conservative `reserve` only when available and cardinality is bounded by exact materialized row counts (never guessed selectivity). Compare reallocation counts, bytes cleared, CPU stacks and memory. Preallocating too much can worsen memory and OOM; must cap.
2. **LowCardinality dictionary construction**: investigate whether huge `ColumnUnique<UUID>::uniqueInsertRangeFrom` repeatedly repacks/rehashes across blocks. Measure per-block unique-ID counts, reverse-index growth, alloc size and stack samples in both merge and hash arms; use same binary, no untimed hashing in measured path.
3. **Time projection × join interaction**: new native factorial run [41589798782](https://github.com/moleus/ClickHouse/actions/runs/41589798782), which measures OFF/AUTO projection × ordered merge/HASH, captures operator work/waits and representative CPU traces. Do not infer the nested `time_by_max` choice solely from top-level `EXPLAIN`.

Reproduction: original `native-join-stage` and `native-join-project` artifact ZIPs from run 41587498126, `profiles[query_id].flame_stacks` (`CPU`, `samples`, semicolon-delimited root-to-leaf stack). The quoted percentages are literal sample ratios per single query. Baseline drift checks for published median timing are separately documented in the checkpoint.

No production/master changes; no cancellation/scheduled CI.

## Existing capacity hook in pinned source

Important source confirmation: `src/Interpreters/HashJoin/HashJoin.h` constructor already takes `size_t reserve_num_ = 0`; `src/Interpreters/HashJoin/HashJoin.cpp` stores `reserve_num` and `dataMapInit` dispatches `map_.create(data->type, reserve_num)`. Therefore the low-level HashJoin map is *already capable of taking a capacity hint*. The missing pieces are finding the production call chain that supplies it, verifying what cardinality is trustworthy, and bounding the memory cost.

This changes the proposed implementation: first instrument the actual value of `reserve_num`, map reallocation counts, and actual distinct build IDs across the 1/3/7/14d native selectors. Compare to possible hints based on operator row estimates, but never preallocate to total 1.33m IDs for a selector that matches only ~35k. If `reserve_num=0` in these plans, an opt-in planner hint (not a new hash implementation) may suffice. An O1 rebuild is only justified after this source-level mapping and mathematical memory bound are established.
