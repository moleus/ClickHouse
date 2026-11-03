# TimeSeries CPU attribution research checkpoint — 2026-11-03

**Current entrypoint:** [shared research index](../ts-lab/INDEX.md), [common agent guide](../ts-lab/AGENT_GUIDE.md), [methodology and failure ledger](../ts-lab/METHODOLOGY_AND_FAILURES.md).

Repository: moleus/ClickHouse
**Unified write branch:** `lab/ts-cbo-adaptive-20261103` (merged from historical `lab/ts-cpu-attribution-20261103` via PR #1).
Pinned source: `52529f0c711566d696917678bf15e7068772fdde`.
Fixture SHA256: `8f3a36846f80d6be5a8604d142e934399f2687094766c57bf105e43977f52858`.
The fixture has 1.33m unique Jenkins series, 95k new series daily for 14 days.
The tests run on isolated GitHub runners, NOT on production.
Do NOT edit master or older historical branches, or interfere with another agent's active runs. All new work is committed to `lab/ts-cbo-adaptive-20261103`.
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

## 2026-11-03: completed source-pinned concurrent-client campaign

Primary [CI #41605951284](https://github.com/moleus/ClickHouse/actions/runs/41605951284). [Full measured results and error log](NATIVE_MULTICLIENT_STOCK_RESULTS_20261103.md).
Pinned immutable fixture 1.33m, SHA256 `8f3a36846f80d6be5a8604d142e934399f2687094766c57bf105e43977f52858`.
Same unmodified ClickHouse O1 LLVM22 binary SHA256 `f0a2fa1f814c58853f43423705cac10ab9df3bfe5887e27ebe9620ba7cb9660e`,
source `52529f0c711566d696917678bf15e7068772fdde`. 4-vCPU runner, max_threads=2 per query.
A/B/B/A with 2 warmup and 8 measured waves per phase, identical full native PromQL output across LC UUID and raw UUID. Actual concurrent clients, **not** `max_threads` variation.

- stage14d 1/4/8-client p50 LC→RAW: **397→322 / 1172→969 / 2291→1715 ms**; all PASS. 8-client CPU/request1092→820ms and 3.294→4.356 requests/s.
- project14d 1/4-client p50 LC→RAW: **1763→1471 / 4623→3605 ms**, PASS. At 8 clients **Code 241 MEMORY_LIMIT_EXCEEDED** at configured 4.47GiB server RSS, so **FAIL**, with no 8-client speedup claim. Retained RSS from earlier phases is a confounder.
- whole metric1d 1/4/8-client p50 LC→RAW: **648→476 / 1699→1284 / 3396→2656 ms**, all PASS. 8-client CPU/request1575→1205ms.
- Original LC dictionary trace CPU remains high under concurrency: e.g. stage8 LC201/1050 sampled CPU events, RAW11/738; representative queries, not medians. After RAW_ID, physical reads and hash-set membership remain dominant in samples.
- OS runqueue delay at n8 stage LC1132ms/RAW841ms. Not mutex lock time. Pool lock ProfileEvents near zero; collector mutex per query cannot be inferred from concurrency among independent clients.
- Full report includes verified source/binary/data hashes, complete physical parity where jobs completed, baseline control drift, p95, throughput, RSS and CPU taxonomy.
- Isolated [fresh-process 8-client project14d #41607617562](https://github.com/moleus/ClickHouse/actions/runs/41607617562) measures whether memory retained between loads causes the OOM, with fresh server per variant ABBA; pending at checkpoint write.
- [Source safety audit](LC_DECODER_FASTPATH_SAFETY_AUDIT_20261103.md) confirms the staged C++ LC decoder bypasses `checkIndexesAreLimited` on fresh dictionary adoption. It cannot be promoted until type/index bounds and malformed-data cases are guarded; this is a static finding, not an observed crash.

## 2026-11-03: project8 OOM reproduced in four fresh-process trials

[Negative result #41607617562](https://github.com/moleus/ClickHouse/actions/runs/41607617562) and
[full report](NATIVE_FRESH8_MEMORY_FAILURE_20261103.md):
all four isolated freshly restarted ClickHouse processes (LC/RAW/RAW/LC)
with the same frozen 1.33m data, stock O1, 4.47GiB server cap,
`max_threads=2`, and **eight simultaneous independent clients** pass
two 8-client warmup waves but **all fail on the third wave** with Code 241
total-RSS OOM near 4.47–4.49GiB. Thus prior 1/4-client phase retention is
not a necessary cause. The failing `AggregatingTransform` is a termination
site, **not established memory source**. Full four server process PIDs,
per-phase logs, and original binary/fixture SHA checks are retained.

The pinned O1 lab binary uses glibc and lacks production jemalloc, so
these capacity results cannot predict an official optimized build or
production allocator without an exact allocator-matched control.

[Native RAW_ID threads1-vs-2 client-load CI #41608382567](https://github.com/moleus/ClickHouse/actions/runs/41608382567):
stage14d n8 and project14d n4 have completed full native parity A/B/B/A.
Stage: `max_threads=2` p50 1521ms/CPU737ms; `max_threads=1`
p50 1452ms/CPU696ms, baseline control drift 4.65% (effect same size
as drift, so no robust win claim). Project n4: threads2 p50
3959ms/CPU3615ms, threads1 p50 3849ms/CPU3522ms,
baseline drift 3.75% (p50 change only 2.76%; not robust).
Whole n8 pending at this checkpoint.
The follow-up [fresh-server project8 `max_threads=1` #41609140400](https://github.com/moleus/ClickHouse/actions/runs/41609140400)
is an independent memory-survival test and does not cancel the ongoing build.

## 2026-11-03: all fixed-client RAW UUID thread-budget jobs PASS

Complete [CI #41608382567](https://github.com/moleus/ClickHouse/actions/runs/41608382567),
[full report](NATIVE_CLIENT_THREAD_BUDGET_RESULTS_20261103.md),
identical binary SHA256 `f0a2fa1f814c58853f43423705cac10ab9df3bfe5887e27ebe9620ba7cb9660e`,
fixture SHA256 `8f3a36846f80d6be5a8604d142e934399f2687094766c57bf105e43977f52858`.
Fixed clients and full native PromQL parity, `max_threads=2/1/1/2`, all 3 jobs
PASS with physical part parity.
- stage14d n8: p50 1521→1452ms, CPU/request737→696ms, control drift4.65%;
- project14d n4: p50 3959→3849ms, CPU3615→3522ms, drift3.75%;
- whole1d n8: p50 2702→2684ms, CPU1234→1225ms, drift2.40%.
**No confidently large improvement:** stage/project p50 deltas are comparable
with A/A drift, whole negligible. Thread budget should not be promoted as
a generic tuning change. Fresh project8 max_threads1 [#41609140400](https://github.com/moleus/ClickHouse/actions/runs/41609140400)
remains a separate capacity experiment.

## 2026-11-03: project8 max_threads1 still OOM, fresh process A/B/B/A invalid

[CI #41609140400](https://github.com/moleus/ClickHouse/actions/runs/41609140400),
[complete evidence](NATIVE_FRESH8_THREAD1_CAPACITY_20261103.md).
Same O1 binary, SHA-verified 1.33m fixture, project14d native PromQL,
fresh ClickHouse process LC/RAW/RAW/LC per phase, server max4.47GiB.
Reduced per-query `max_threads` **2→1**, fixed 8 concurrent clients.
- LC A0: all 2 warmups +8 measured bursts PASS, p50 5102.359ms,
  CPU/request2361.657ms, observed RSS peak4.01GiB.
- RAW B1: 8 bursts then OOM; RAW B2: 1 burst then OOM;
  LC A3: 3 bursts then OOM.
- Three phase failures with Code241 near4.47GiB cap. Overall CI FAILURE;
  full LC/RAW A/B/B/A at 8 clients is **invalid**.
- Evidence shows small thread budget can prolong survival under this
  particular glibc lab allocator, but **cannot guarantee capacity**.
  First and last LC fresh-process phases disagree, so do not credit
  a schema with greater memory safety. Existing source-pinned jemalloc
  build must be separately verified and run before production judgment.

## 2026-11-03: R12 matched O1 C++ LowCardinality decoder prototype REJECT

[Full native A/B/B/A #41620022189](https://github.com/moleus/ClickHouse/actions/runs/41620022189)
completed all four stage/project/regex/whole jobs in one isolated CI job
and produced a SHA-verified [immutable evidence record](../ts-lab/evidence/R12-lc-decoder-matched-41620022189.json).
Old decoder control [build #41531656434](https://github.com/moleus/ClickHouse/actions/runs/41531656434)
`3990e373b43396d0faafd8feffe5dd6514bd57c520ec12e89b8e5796d64e6221` vs new experimental
[build #41591617794](https://github.com/moleus/ClickHouse/actions/runs/41591617794)
`bcafb369ccbfb063faea9c82694c4b3a527430432a81401225e2a8091a3c2cb9`.
Exact source `52529f0c711566d696917678bf15e7068772fdde`, LLVM22.1.8 `O1`,
identical CMake flags and two common C++ patch chunks, only extra candidate
patch path `src/DataTypes/Serializations/SerializationLowCardinality.cpp`.
Frozen Jenkins LC UUID 1.33m-series fixture SHA256
`8f3a36846f80d6be5a8604d142e934399f2687094766c57bf105e43977f52858`.
Four fresh-server per-case `baseline/lcpatch/lcpatch/baseline` phases, two
warmups + six measured native PromQL queries per phase; exact nonempty labels/
timestamps/values, same part rows/marks/bytes, QueryFinish CPU and control
drift below25%, no OOM.

- Stage14d: p50 **916.225→1022.784ms +11.6%**, CPU **1694.628→1876.203ms +10.7%**,
  baseline p50 drift **0.109%**, CPU drift **0.096%**. Strong **regression**, reject.
- Project14d: p50 1795.351→1774.902ms, baseline drift4.35%; effect inconclusive.
- Regex14d: p50 1956.876→1919.190ms, baseline drift2.29%; effect inconclusive.
- Whole1d: p50 1370.533→1403.063ms, baseline drift2.77%; effect inconclusive.
- Representative stage sum `processors_profile_log` elapsed work **1664→1666ms**
  while measured QueryFinish CPU rises **~182ms**; the artifact's
  `system.trace_log` CPU/hot leaves are **empty**. Do NOT claim that
  dictionary adoption, a named lock, or a specific operator consumed the extra
  CPU from these incomplete traces.
- Safety blocker persists: the experimental direct adoption path skips
  `ColumnLowCardinality::checkIndexesAreLimited`, assumes unique keys,
  and does not replicate stock malformed input behavior. Normal-data parity
  does not establish safety. [Source safety and cost report](../ts-lab-r12/LC_FRESH_DICTIONARY_SAFETY_AND_COST_20261103.md).
- Decision **REJECT**, do not stack, promote or compile the unguarded variant again.
  The proven new-table RAW UUID opt-in is a separate, overlapping
  dictionary-work optimization, and savings cannot be multiplied.
- Independent [R09 latest upstream source audit](../ts-lab/evidence/R09-upstream-source-audit-20261103.json)
  at exact `ClickHouse/ClickHouse` master `a79b6cef...` confirms local
  `StorageTimeSeriesSelector.cpp` and `TimeSeriesSink.cpp` blobs unchanged
  against pinned source; global LC index validation added upstream does not fix
  the no-global experimental bypass. No new source build is warranted from
  this audit alone.
- Separate existing jemalloc O1 build [41607779848](https://github.com/moleus/ClickHouse/actions/runs/41607779848)
  was still in progress at this checkpoint. No cancellation, duplicate compile
  or master/production write.

## 2026-11-03: R17/R18 output formatter falsified, value-codec storage CPU crossover

- [R17 native JSON versus FORMAT Null #41628966700](https://github.com/moleus/ClickHouse/actions/runs/41628966700)
  **416 queries** PASS with full nonempty JSON native PromQL parity,
  zero-byte Null output, identical read_rows/SelectedMarks and exact
  unchanged RAW UUID inner parts. Namespace CPU JSON94.337ms→Null93.260ms
  (only1.1% cost on191 JSON output bytes); burst15 CPU67.899→68.010ms
  (no benefit). Broad node FORMAT Null **slowed** CPU411.338→440.113ms
  despite same rows/marks; output sink affects pipeline scheduling and
  **is not a pure serializer cost measurement**. CPU trace on one
  representative namespace query read_decode45/90 samples; burst15
  read_decode29/60; broad node grid128/413 and expression121/413.
  Do not make strong call-path or mutex claims from one query.
  [Immutable evidence](../ts-lab/evidence/R17-output-format-cpu-41628966700.json),
  [source/cost report](../ts-lab-r17/NATIVE_OUTPUT_CPU_ATTRIBUTION_20261103.md).
  R03 VM vs CH comparison is additionally confounded by optimized VM vs O1
  ClickHouse; JSON is not the dominant CPU explanation for tiny output.

- [R18 three-codec full native #41630585794](https://github.com/moleus/ClickHouse/actions/runs/41630585794)
  PASS, eight ALP/ZSTD1/LZ4 A/B/B/A cases on same 400-node/3d SHA-verified
  fixture and stock O1 executable. Same RAW UUID, 3600s, 28,896 IDs,
  2,488,320 sample values, one active part/layer, same native results.
  Compressed SAMPLES ALP+ZSTD3=5,303,391B; ZSTD1=3,414,486B (−35.6%),
  LZ4=3,773,263B (−28.9%). But `pod_namespace` CPU
  ALP36.886→ZSTD1 40.558ms **+10.0%** with0.80% A/A CPU drift.
  Broad node CPU115.136→108.109ms (−6.1%, drift3.85%);
  other small effects inconclusive. One unpaired ingest/merge per codec
  has no steady-state write CPU validity. Original [R18 first #41630357537](https://github.com/moleus/ClickHouse/actions/runs/41630357537)
  was invalid string DDL check on quoted `samples.value` and `Delta(8)`,
  fixed and preserved in failure M42.
  [Evidence](../ts-lab/evidence/R18-samples-value-codec-41630585794.json).

- [R18 held-out smooth and entropy #41631747983](https://github.com/moleus/ClickHouse/actions/runs/41631747983)
  **both jobs PASS**, same source/binary/IDs/time labels with only Float64
  sample values changed, exact native results and complete ABBA. Compressed
  SAMPLES smooth ALP4,718,192B vs ZSTD1 3,937,064B (ZSTD1 −16.6%);
  entropy ALP**19,686,139B** vs ZSTD1 20,216,284B (**ALP −2.6%**).
  Thus the sign of codec storage advantage flips with value entropy.
  Entropy `pod_namespace` CPU ALP107.805→ZSTD1 99.317ms (−7.9%),
  A/A CPU drift1.60%, opposite to original repetitive gauge.
  [Immutable 2-job evidence](../ts-lab/evidence/R18-value-pattern-holdout-41631747983.json),
  [held-out report](../ts-lab-r18/HELDOUT_VALUE_DISTRIBUTIONS_20261103.md).
  R18/heldout runners are different AMD models than R17: **do not compare
  absolute CPU/wall across CI runners**. No global codec recommendation.

**Decisions:** R17 output formatter as primary cause **REJECT**.
R18 value codec **ACCEPT_NEXT_GATE_ONLY**; need real-like Float64 entropy,
late/counter/NaN values and paired natural merges/concurrent read on a
production-allocator/optimized binary. Original O1 jemalloc build
[#41607779848](https://github.com/moleus/ClickHouse/actions/runs/41607779848)
still *Compile one executable* at this checkpoint; do not cancel or
start another full compilation. No master/production or old branches changed.

