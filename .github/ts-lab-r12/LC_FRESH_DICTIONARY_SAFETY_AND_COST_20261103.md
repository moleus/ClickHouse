# R12: fresh LowCardinality dictionary path — cost bound and promotion blocker

Date: 2026-11-03. All code and measurements are confined to
`lab/ts-cbo-adaptive-20261103`. Source SHA
`52529f0c711566d696917678bf15e7068772fdde`.

## Exact matched experiment

[Build #41531656434](https://github.com/moleus/ClickHouse/actions/runs/41531656434)
is the `O1` LLVM22.1.8 C++ baseline with the same fast-rate patch and
opt-in native JOIN override. [Build #41591617794](https://github.com/moleus/ClickHouse/actions/runs/41591617794)
is source-identical except for
[`apply_lc_fresh_dictionary_cpp.py`](../ts-lab-cpu/apply_lc_fresh_dictionary_cpp.py).
Both compiled successfully. Their CMake flags, source SHA, and shared
patch file blobs were compared; the two CMake command blocks are identical.

[Native R12 A/B/B/A #41620022189](https://github.com/moleus/ClickHouse/actions/runs/41620022189)
downloads and checks both existing binaries and tests the **same frozen
1.33m-series v11 LC UUID** parts, with a new server process in each
baseline/candidate/candidate/baseline phase. It verifies the downloaded
`combined.patch` files differ only in
`src/DataTypes/Serializations/SerializationLowCardinality.cpp`, archive and
binary SHA manifests, complete nonempty native PromQL labels/timestamps/
values, query CPU, RSS, read rows/marks and profiler stacks.
No C++ compilation or master mutation is needed for this comparison.

**Performance pass != correctness or security pass.** The performance
test uses valid synthetic input only. This is not a deployable binary.

## Source call chain and cost

- [`SerializationLowCardinality.cpp`](https://github.com/moleus/ClickHouse/blob/52529f0c711566d696917678bf15e7068772fdde/src/DataTypes/Serializations/SerializationLowCardinality.cpp#L703-L756),
  no-global branch: deserialize dictionary keys of size `D` and row indexes
  of size `N`, then call `insertRangeFromDictionaryEncodedColumn`.
- [`ColumnLowCardinality.cpp`](https://github.com/moleus/ClickHouse/blob/52529f0c711566d696917678bf15e7068772fdde/src/Columns/ColumnLowCardinality.cpp#L301-L345),
  stock implementation validates each index against `D`, then calls
  `ColumnUnique::uniqueInsertRangeFrom` to build a reverse index for all
  dictionary keys and translate row indexes.
- Candidate constructs a `ColumnUnique` directly from the deserialized
  dictionary, attaches a shared `ColumnLowCardinality`, and calls
  `insertRangeFrom` on a fresh destination.
- The [fresh/shared branch in `insertRangeFrom`](https://github.com/moleus/ClickHouse/blob/52529f0c711566d696917678bf15e7068772fdde/src/Columns/ColumnLowCardinality.cpp#L235-L283)
  may attach the same dictionary and copy indexes without rebuilding the
  reverse index.

For a decoded block with `N` row indexes and `D` dictionary keys,
the **stock** path has unavoidable `O(N)` index validation/copy plus
`O(D)` key hashing/remapping and associated memory. The candidate can
avoid some `O(D)` hash work on fresh, non-nullable, structurally matching
columns. It cannot avoid `O(N)` copied indexes, `O(D)` key
deserialization, JOIN/aggregation or physical reads.

A representative earlier Jenkins stage trace identified 201/1050 CPU samples
(~19.1%) in a broad LowCardinality/dictionary category. Even deleting
**all** work in that category gives a mathematical upper bound of
`1/(1-0.191) ≈ 1.24×` total CPU speedup under the unrealistic
assumption that sampling exactly measures CPU share and every category
sample is removed. The patch affects only a subset of the category.
This is a **rough prior**, not a measured bound on wall latency.
It also ignores changes in allocations or downstream work.

## Exact safety gaps that block deployment

1. **Out-of-bounds dictionary indexes.** The stock helper
   `checkIndexesAreLimited(indexes, keys.size())` is file-local in
   `ColumnLowCardinality.cpp` and checks unsigned widths 8/16/32/64.
   A malformed index >= `D` throws `INCORRECT_DATA`. The new
   fresh/shared branch bypasses this check. A later indexed access could
   then read past the dictionary. Reuse or factor out the same validation
   before attaching a shared dictionary. A cheap fixed-width maximum
   scan costs `O(N)` without allocating an `O(D)` hash table.
2. **Duplicate keys.** The stock `uniqueInsertRangeFrom` deduplicates
   keys. The candidate's direct `ColumnUnique` wrapper does **not**.
   `isDefaultAt(0)` only validates the first key and does not prove all
   keys are unique. A sound fast path must either prove uniqueness by a
   validated writer/part-format contract, verify it at read time, or
   fall back to the stock route. Full exact uniqueness verification
   itself requires work proportional to `D`, which can erase the CPU
   benefit. There is no safe "trust unique" claim for arbitrary
   serialized bytes on the basis of current source.
3. **Incomplete or unexpected index arrays.** Ensure deserializer
   returns exactly the requested `num_rows` or an explicit end-of-stream
   condition and rejects incompatible column widths without unsafe copies.
   Preserve stock exception/error semantics for damaged input.
4. **Nullable/reserved keys/continuation.** Current fast branch excludes
   Nullable keys but does not prove all reserved/duplicate-key cases.
   Test Compact/Wide parts, continuous reading, skipped granules,
   dictionary updates, reused output column, repeated inserts and merges.

Candidate promotion conditions: pass an independent suite of crafted
malformed index columns of each supported width, duplicate and reserved
dictionary keys, truncated arrays, Nullable data, and repeated/
discontinuous read patterns, with exact stock-matched results or exceptions.
A sanitizer build adds evidence but is not a substitute for semantic
parity. Then compare new **corrected** candidate against the same patched
baseline at the same O1 and production allocator/optimization settings.
The historical unguarded candidate is **never** production-ready even
if R12 shows lower query CPU.

## Scientific decision rule

- If R12 shows negligible gain, reject the direct dictionary-adoption
  approach without spending another multi-hour compilation.
- If R12 shows a substantial CPU gain beyond A/A drift, keep it only as
  an exploratory upper bound. Build and validate a corrected C++ path
  only after the safety design can prove original behavior.
- If R12 fails native numerical/label parity or suffers new OOM, reject
  the unguarded prototype immediately and retain the raw evidence.
- A gain in the experimental decoder must not be multiplied by the
  RAW_UUID schema improvement; the latter removes the same category of
  work, so their benefits overlap.

Reference: [original static safety audit](../ts-lab-cpu/LC_DECODER_FASTPATH_SAFETY_AUDIT_20261103.md).
