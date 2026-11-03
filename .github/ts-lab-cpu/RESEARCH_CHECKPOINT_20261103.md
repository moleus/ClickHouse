# TimeSeries CPU attribution research checkpoint — 2026-11-03

Repository: moleus/ClickHouse
**Work only in branch:** `lab/ts-cpu-attribution-20261103`
Pinned source: `52529f0c711566d696917678bf15e7068772fdde`.
Fixture SHA256: `8f3a36846f80d6be5a8604d142e934399f2687094766c57bf105e43977f52858`.
The fixture has 1.33m unique Jenkins series, 95k new series daily for 14 days.
The tests run on isolated GitHub runners, NOT on production.
Do NOT edit master, old `lab/ts-cbo-adaptive-20261103` branch, or another agent's active runs.
Do NOT cancel builds. Use separate path-triggered workflows with `cancel-in-progress: false`.

## Validated source-driven root cause

`LowCardinality(UUID)` is the second element in v11 `id Tuple(UInt64,LowCardinality(UUID))`.
`src/DataTypes/Serializations/SerializationLowCardinality.cpp` reads an encoded dictionary; in the non-global or additional-key paths, it calls `insertRangeFromDictionaryEncodedColumn`.
`src/Columns/ColumnLowCardinality.cpp` then calls `getDictionary().uniqueInsertRangeFrom`.
`src/Columns/ColumnUnique.h` invokes `ReverseIndex::insert`, and `src/Columns/ReverseIndex.h` builds and initializes a hash table.
CPU stacks show massive `ReverseIndex` and libc `memset` samples. This work is large when nearly every UUID is unique.
The source's LowCardinality docs warn that >100k distinct dictionary values can be less efficient.
The *complete* CPU trace sample classification is a partition of samples. Processor sums overlap across threads and are NOT request wall time.

## Full native TimeSeries results, exact matching IDs and PromQL output

Run [41590051439](https://github.com/moleus/ClickHouse/actions/runs/41590051439), 4/4 PASS, SAME pinned O1 binary.
The sole schema change is all three v11 inner-target ID types from Tuple(UInt64,LowCardinality(UUID)) to Tuple(UInt64,UUID). TAGS keeps same SipHash generator and key/value text index.
Native PromQL A/B/B/A, warmups, control drift under 25%, strict labels and timestamps and tolerant floating-point parity.
- stage14d: default 918.167ms, raw618.449ms (1.4846x); CPU2063.619->1496.414ms; memory113672667->69452366B, drift16.5%.
- project14d: 1008.058->922.089ms (1.0932x); CPU2223.074->1772.637ms, drift15.3%.
- regex14d: 2054.457->1678.512ms (1.224x); CPU3752.101->2926.555ms, drift0.03%.
- whole1d: 1341.311->884.245ms (1.5169x); CPU2759.511->1903.306ms, drift8.1%.
Total TAGS+TimeRanges+SAMPLES disk: default149266597B, raw147191112B (~1.4% smaller).
Artifacts: stage13209971759; project13209293916; whole13209174457; regex13209169658. Each includes per-query processor and trace logs.
In representative native stage14d, 649 CPU samples fall in LowCardinality symbols (raw:11), while total CPU fell by 567ms. Whole1d lowcard955->58 CPU samples, total CPU fell by 856ms.
Dominant leaf for default: __llvm_libc::memset_gt128_baseline; ReverseIndex and realloc/malloc.
This shows the root cause beyond a slow JOIN alone.

## CPU attribution per physical inner target

[41590864544](https://github.com/moleus/ClickHouse/actions/runs/41590864544), all 6 cases PASS, A/B/B/A drift0.5%-13.7%.
Direct FORMAT Null scans on same physical order, exact ID source, all1.33m series:
- TAGS id only: default259.277ms CPU, raw11.675ms.
- TAGS id,tags: default337.848ms, raw108.364ms.
- TimeRanges id: 256.608ms, raw11.239ms.
- TimeRanges id,min,max: 427.916ms, raw33.998ms.
- SAMPLES id: 789.716ms, raw11.616ms.
- SAMPLES id,bucket,samples: 684.212ms, raw157.643ms.
These layer scans run separately. Do NOT sum their wall latency to explain the native query critical path.
Script: .github/ts-lab-cpu/bench_inner_layers.py

## Unfiltered synthetic per-TAGS scan result

[41589135007](https://github.com/moleus/ClickHouse/actions/runs/41589135007).
Equal standalone MergeTree, one part, 164 marks each, LC ID vs raw UUID.
LC disk48254770B, raw47567916B. Query CPU id-only LC269ms vs raw11ms; 90.7% of CPU samples in LC ReverseIndex/serialization. Some A/B/B/A *wall* baseline drifts >25%, so the run intentionally failed its drift gate. Do NOT cite wall speedups of those cases. Values/CPU are still useful descriptive diagnostics.
Script: .github/ts-lab-cpu/bench_uuid_storage.py.

## Thread, block-size and CPU sampling profile

[41588920790](https://github.com/moleus/ClickHouse/actions/runs/41588920790). 7 of 8 jobs PASS; stage-block experiment failed drift gate for one alternative only. No false claims.
stage14d max_threads=1 uses ~1985ms CPU /1961ms wall vs 4 threads ~2368ms CPU /940ms wall. 8 threads yields limited additional wall gains. Increasing parallelism uses more CPU but reduces latency.
max_block_size=1024 hurt several broad queries ~11%-13% wall vs65536; smaller blocks increase scheduling/processing overhead.
Script .github/ts-lab-cpu/bench_cpu_knobs.py; full CPU trace classifier, query log, operator wait/work and stack leaves saved.

## Sparse versus dense counterexample

[41590057398](https://github.com/moleus/ClickHouse/actions/runs/41590057398), PASS.
One stable population: 1k dense series x960 hourly samples/series. One churn population: 40k sparse series x1 sample/series. SAME 40-day fixture and O1 binary, full native PromQL parity. 
- dense_all: LC113.4ms, raw105.9ms (~7% raw faster).
- dense_stage: LC28.0ms, raw26.8ms (~4% raw faster).
- sparse_all: LC133.8ms, raw141.6ms (~6% raw slower).
- sparse_stage: LC30.6ms, raw30.1ms (~equal).
All control drift <8.2%. Do NOT generalize a single fixture, especially different sample density and 95k/day churn.
Script .github/ts-lab-cpu/bench_dense_uuid.py.
Initial dense run41589877979 failed because v11 **has no id_type setting**. Fixed by explicitly declaring ID type on SAMPLES, TAGS and TIME RANGES INNER COLUMNS, preserving identical TAGS default id hash expression.

## INSERT, merge and write CPU

[41591016459](https://github.com/moleus/ClickHouse/actions/runs/41591016459), PASS.
100k deterministic lines of the 1.33m canonical fixture, SHA256 44e7bdc715ebe8acb613a51d68f988d7f6a674f603030871b32bee9a23173305; 4 independent tables A/B/B/A, 2000 rows per INSERT; merges stopped during inserts.
INSERT total wall LC5.584s vs raw5.461s (~2.3% faster raw); recorded INSERT query CPU LC421.345ms vs raw414.434ms (~1.6% raw CPU reduction), drift6%.
Then sequential OPTIMIZE FINAL:
- TAGS CPU LC451ms vs raw374ms (~17% lower).
- TimeRanges CPU LC80ms vs raw28ms (~65% lower).
- SAMPLES CPU LC658ms vs raw561ms (~15% lower).
The CPU for background merges is not necessarily fully captured by the query log; avoid treating this as a steady-state ingestion benchmark.
Script .github/ts-lab-cpu/bench_insert_cpu.py.

## LowCardinality encoding setting investigation: verified NO-OP

[41590558774](https://github.com/moleus/ClickHouse/actions/runs/41590558774): changed INSERT SETTING low_cardinality_max_dictionary_size (0,128,8192,65536) and single dictionary flag. All produced byte-identical part totals after OPTIMIZE FINAL. Latency differences of a few percent are NOT evidence of tuning effect.
[41591296942](https://github.com/moleus/ClickHouse/actions/runs/41591296942): forced WIDE parts, computed **per-file SHA256** before and after merge for all four policies, all variants had identical content bytes. The run correctly FAILED a no-op intervention gate.
Root cause supported by pinned source:
- MergeTreeDataPartWriterCompact::getSerializationSettings hardcodes low_cardinality_max_dictionary_size=0.
- MergeTreeDataPartWriterWide reads that setting from writer settings.
- MergedBlockOutputStream constructs MergeTreeWriterSettings from data_part->storage.getContext()->getSettingsRef(), NOT the per-INSERT query context used by our tests.
Do not recommend per-INSERT low-cardinality tuning. To test global settings, create isolated servers with distinct storage contexts and compare actual file digests BEFORE timing.

## Active and staged C++ optimization

[41591617794](https://github.com/moleus/ClickHouse/actions/runs/41591617794) compiles pinned O1+same native JOIN override baseline with ONE extra experimental C++ patch:
.github/ts-lab-cpu/apply_lc_fresh_dictionary_cpp.py
It proposes adopting an existing immutable dictionary for the FIRST read of a **non-nullable** LowCardinality column with no global dictionary, default first entry, avoiding ReverseIndex rebuild in that branch. Other branches use original code.
Safety caveat: malformed/duplicated dictionary and invalid indexes may bypass original normalization. THIS IS NOT PRODUCTION READY. Must prove:
1. Source compiles with pinned LLVM, ccache, tests.
2. Same-on-disk native PromQL exact labels/time/numerics vs unchanged O1 baseline, including stage14/project14/regex14/whole1, stable A/B/B/A.
3. Extra corruption, Nullable, empty/duplicate key, continuous read, format Native, wide/compact part tests, memory and OOM.
4. Verify the targeted no-global branch is actually hit in the real workload, else a no-effect result is expected.
Staged only, intentionally NOT triggered until verified binary artifact:
.github/workflows/ts-lab-cpu-lc-decode-cxx-aba-20261103.yml
Contains placeholder __PATCHED_RUN_ID__; once build SUCCESS and artifact verified, replace placeholder by exact build run id and change on: workflow_dispatch to a self-triggered push (path only), then compare two binaries by restarting server on the SAME frozen data.
Benchmark script: .github/ts-lab-cpu/bench_lc_decoder_cpp.py
Baseline SHA-verified O1 binary comes from successful [41531656434](https://github.com/moleus/ClickHouse/actions/runs/41531656434). Candidate and baseline differ by only LC patch.

## Next independent research tasks

- Analyze compile logs if patch fails; patch only isolated branch; do not cancel other work.
- Introduce controlled corrupted dictionary test to confirm direct dictionary adoption cannot silently change values. Consider limiting patch to trusted dictionary states only.
- Profile memory allocations and HashTable zeroing per call site (native data path). Measure cumulative process RSS separately from per-query querylog peak.
- Stress v11 default versus raw ID with small frequent inserts and background merges; measure steady-state write amplification, background merge CPU, IO, server RSS.
- Test different proportions of stable/repeated vs churning IDs, not just 1k/40k. Find the break-even regime and report workload dependence.
- Audit cache warm/cold and concurrency effects (1/4/8 concurrent PromQL requests); compare CPU per request, throughput, tail latency and OOM.
- Consider optimized lazy read-only LowCardinality decoding that preserves original storage format, but require rigorous semantics tests.
- Keep all SHA pins, A/B/B/A, exact nonempty output parity, and failed-run evidence.
- NEVER just poll CI. While a build is active, design and run an independent hypothesis, capture source evidence and profile data, keep work pipelined. No production or master changes.
