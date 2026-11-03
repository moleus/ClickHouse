# R05 — Candidate-ID selectivity is not the same as SAMPLES mark pruning

Status: **source-supported path and conditional mathematical bound only**. No new performance improvement is claimed. Owner: `chatgpt-r05-mark-bound-20261103`. Canonical lock: [R05.json](../claims/roots/R05.json).

## Pinned source path

- Source commit: `52529f0c711566d696917678bf15e7068772fdde`.
- [StorageTimeSeriesSelector.cpp](https://github.com/moleus/ClickHouse/blob/52529f0c711566d696917678bf15e7068772fdde/src/Storages/StorageTimeSeriesSelector.cpp): `readImpl` constructs an ID-set filter for bucketed SAMPLES, and explicitly says the set participates in primary-key index analysis; it does **not** imply selective primary-key marks. The TAGS/TimeRanges join is built in `addTimeRangesTableJoin`. Verify generated SAMPLES `ORDER BY` and part granularity before applying any numeric bound to a real run.
- Negative prerequisite: [M06/#41587537130](https://github.com/moleus/ClickHouse/actions/runs/41587537130): a TimeRanges minmax index appeared in EXPLAIN but pruned **zero** marks. This is not a measurement of SAMPLES primary-key ID pruning.
- Immutable R01 fixture evidence remains owned by R01: [#41612120162](https://github.com/moleus/ClickHouse/actions/runs/41612120162). Do not rerun or treat its RAW-vs-LC query latency as a proof of SAMPLES mark pruning.

## Exact conditional occupancy model

Assume a mark has `g` **distinct**, independently and uniformly selected IDs, and each ID matches a predicate with probability `s`. Let `H` mean at least one matching ID is in a mark. Then:

`P(H) = 1 - (1-s)^g`.

This is not a worst-case bound for arbitrary physical ID distributions. An ID-ordered part with correlated predicates can touch many fewer or many more marks. Real mark row counts may differ; repeated samples for a single ID reduce effective distinct-ID occupancy.

For **illustration only**, take `g=8192`, not a verified ClickHouse SAMPLES part setting:

| Selected ID fraction s | Expected touched marks |
|---:|---:|
| 10% | effectively 100% |
| 1% | effectively 100% |
| 0.1% | 99.9724% |
| 0.01% | 55.9234% |
| 0.001% | 7.8655% |

To touch at most half on average, `s <= 1 - 0.5^(1/8192) = 0.0000846091` (0.008461%). To touch at most 10%, `s <= 1 - 0.9^(1/8192) = 0.0000128613` (0.001286%). For a 1.33 million-distinct-ID example, the half-mark threshold is about 113 independently distributed matched IDs; this is illustrative only.

A finite, exactly-K, without-replacement model has `P(no match in a mark) = C(N-g,K)/C(N,K)` for N IDs and K selected IDs, provided N-g >= K.

## Cheap discriminating test, not a build request

1. On frozen existing parts, record the actual inner SAMPLES `ORDER BY`, `index_granularity`, rows/mark and distinct IDs/mark. Separate LC UUID and RAW UUID physical layouts.
2. For **the same native query**, read matched distinct IDs and their distribution across physical marks. Compute actual touched-mark fraction. Distinguish mark pruning (selected marks), post-read filtering (returned rows), and native aggregation CPU. Require nonempty PromQL output parity.
3. Compare existing `EXPLAIN indexes = 1` and `SelectedMarks`/`read_bytes` with the model; test narrow stage/project and broad metric selectors. Do not perform a C++ build unless actual marks are pruned and a distinct cheaper read path is proven possible.
4. A/B/B/A and fresh-process RSS checks are mandatory **only after** a meaningful candidate intervention exists; until then this is a source/math gate, not a performance claim.

## Failure criteria

Reject the sparse-read hypothesis if actual SAMPLES selected marks remain approximately unchanged over the relevant selectors, or if a selective candidate increases read CPU/latency, write/merge cost, or RSS. Never interpret reduced matching IDs alone as fewer compressed blocks read. Keep negative and invalid outcomes as immutable evidence.
