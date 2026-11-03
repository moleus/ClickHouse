# LC decoder fast-path: source-level safety gate

Date: 2026-11-03. Repository: `moleus/ClickHouse`.
Write branch: `lab/ts-cbo-adaptive-20261103`.
Pinned C++ source: `52529f0c711566d696917678bf15e7068772fdde`.
Proposed C++ patch (lab only): [apply_lc_fresh_dictionary_cpp.py](apply_lc_fresh_dictionary_cpp.py).
Compile (historical read-only run): [41591617794](https://github.com/moleus/ClickHouse/actions/runs/41591617794).

## Blocking discrepancy in the proposed fast path

The current stock path is:
`SerializationLowCardinality.cpp` `read_indexes` (around line 724) ->
`ColumnLowCardinality::insertRangeFromDictionaryEncodedColumn(keys,indexes)`
(`src/Columns/ColumnLowCardinality.cpp`, around lines 339-345).

The first operation of this stock method is
`checkIndexesAreLimited(indexes, keys.size())` (around lines 304-335).
It validates UInt8/UInt16/UInt32/UInt64 index-column type and checks that every
index is **strictly below** the dictionary size. On an out-of-range value it
throws `INCORRECT_DATA` with the dictionary size and maximum invalid index.

The experimental patch instead constructs a `ColumnUnique` from the raw keys,
wraps it in a shared `ColumnLowCardinality`, then calls
`low_cardinality_column.insertRangeFrom(*local_column, 0, num_rows)`.

The stock `ColumnLowCardinality::insertRangeFrom` (around lines 236-265)
has an empty-destination shared-dictionary branch. It calls
`setSharedDictionary` and copies indexes directly from the source.
There is **no equivalent maximum-index check in this branch**.
The `ColumnLowCardinality` constructor (around line 142) also only stores the
dictionary/indexes: it does not validate that the indexes fit the dictionary.

**Consequence:** with malformed or corrupted input, the patched path can
adopt an out-of-range index that the stock path rejects before publication.
A later `get()`, `getDataAt()`, or indexed dictionary access may then
read out of bounds or fail differently. This is a source-proven missing check,
not an observed exploit or measured crash.

The patch only tests `!keys_column->empty()` and
`keys_column->isDefaultAt(0)`. These conditions do **not** prove that all
indexes are in range or that dictionary values are distinct.
`ColumnUnique::ColumnUnique(MutableColumnPtr && holder, bool)`
(around lines 299-312 of `ColumnUnique.h`) stores the supplied keys
without deduplicating them. Existing on-disk data from a conforming writer
should normally contain unique keys, but the fast path does not verify that
assumption.

## Why the optimization could still help

The original path performs dictionary-key remapping and
`ColumnUnique::uniqueInsertRangeFrom`, which may build a large
`ReverseIndex` hash table and zero its allocation.
The shared-dictionary path can avoid the hash rebuild **only when** its
empty/shared/structural preconditions are true.

Let D be the number of serialized dictionary keys and N the number of rows
in a decoded block. A bounded linear index validation is O(N) and need not
allocate a D-entry hash table. For a nearly unique UUID dictionary, this
may preserve most of the potential gain; it remains an unmeasured hypothesis.
It must be compared against stock with the same source, LLVM, optimization
flags, exact data and native PromQL.

## Required gate before any promotion

1. Restore stock-equivalent index-column type and max-index validation
   **before** adopting the shared dictionary. Check `indexes_column->size()`
   against the expected `num_rows`; preserve `INCORRECT_DATA` semantics
   for invalid dictionary index values.
2. Use a safe fallback when dictionary keys are duplicated, an unexpected
   reserved key is present, the destination is not empty, or the type is
   Nullable. Prove how a trusted on-disk writer guarantees dictionary
   uniqueness; do not infer this from `isDefaultAt(0)`.
3. Compare stock vs candidate on crafted invalid indexes of every supported
   unsigned index width, truncated indexes, empty dictionary, duplicate
   keys and reserved-key corruption. Require rejection without unsafe memory
   access and unchanged normal-data results.
4. Test Native serialization, Wide and Compact MergeTree parts, continuous
   and discontinuous reads, skipped granules, repeated inserts, merge reads,
   nullable columns, and sanitizer/ASan/UBSan builds where practical.
5. Require exact nonempty native `prometheusQueryRange` parity and per-query
   CPU, peak memory, RSS, physical IO, traces and A/B/B/A control drift.
   Do not call the patch production-ready based on compile success.

This is a **blocking audit of an unverified C++ prototype**. The ongoing
historical build is not cancelled. No upstream, master or production changes
are authorized by this note.
