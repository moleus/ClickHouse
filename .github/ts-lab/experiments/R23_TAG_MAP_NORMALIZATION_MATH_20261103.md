# R23 — label-map input order and unskippable TimeSeries tag normalization

**Owner:** `R23-tag-map-key-order-normalization-cost-v1`. Source `52529f0c711566d696917678bf15e7068772fdde`, unmodified stock-O1 binary SHA256 `f0a2fa1f814c58853f43423705cac10ab9df3bfe5887e27ebe9620ba7cb9660e`. This is neither R21 row-order/cache-capacity nor R22 cache expiry, R19 old-metadata retention, R07 covering index or R20 query aggregation.

## Source proof before benchmark

- `src/Storages/TimeSeries/TimeSeriesSink.cpp`, `fillTagsColumns` builds one `std::vector<pair<string_view,string_view>> sorted_tags` **for each input row**. It copies all key/value pairs from the original `Map`, then appends the `__name__` key when `metric_name` is provided, then calls `sortTagsAndRemoveDuplicates`.
- `sortTagsAndRemoveDuplicates` executes `std::sort(tags.begin(),tags.end())`, `std::unique`, removes empty-valued labels, rejects conflicting duplicate label names, and requires a metric name. It does not inspect the TAGS cache first.
- `TimeSeriesSink::consumeTagsAndSamples` creates `tags_block = makeTagsBlock(...)`, calculating stable `id`, **before** it invokes `tags_deduplication_cache->filterOutWrittenRows`. Thus a TAGS-cache hit cannot avoid per-row canonicalization or ID hashing. A size-correct cache only prevents the TAGS inner-table row from being written again.
- **Important constraint:** Sorting user `tags` keys at the client does not make the internal vector necessarily sorted. If `metric_name` is nonempty, ClickHouse appends `__name__` after those keys. Lexically, `__name__` sorts before the common `lab_XX`, `project`, `rack`, `series` keys. Consequently both source variants still execute the C++ sort. Do not claim this A/B directly measures the cost of removing `std::sort`, `sipHash128`, or JSON parsing.

## Fixed control and mathematical bounds

- `S=4096` identical active IDs, `B=24` chronological 1-minute INSERTs, one sample per ID per insert. `S×B=98,304` samples/phase, `19` supplied tag pairs per row and **20 normalized pairs** after `__name__` injection. Each phase presents `4096×24×19=1,867,776` supplied label pairs. The sorting work is on `4096×24×20=1,966,080` normalized pairs across all input rows.
- `tags_deduplication_cache_size_bytes=1,048,576` and `expiration_seconds=3600`. Pinned C++ entry accounting from `TimeSeriesDeduplicationCache.h` is about **144 bytes/entry**, capacity `floor(1048576/144)=7281 >4096`. Expected global misses `4096` and hits `4096×23=94,208` per phase; raw physical TAGS rows `4096` when merges are stopped. These are **predeclared falsifiers**, not measured results.
- Exact same per-(series,batch) value/timestamp and row ordering. The only intervention is the serialized order of key/value pairs in the `tags` Map: keys in ascending order (A) versus deterministic seeded shuffle on each row (B).
- Independently reproduced fixture SHA256: canonical logical rows `826f8d12e7051d8b8c0ebc2f79d201c205fdd2ee9d4ae58e69534375963cf458`, sorted wire `ec128e6c74402d095c59c79f8f352ef4a0f48e0839dce730baa0f6ed7796ff64`, shuffled wire `6c73ffea74efc9d92ea78996a7075e514b83c98b5f2411d7ed9839939480af49`. Exact equal byte count **39,980,232 per phase**; the two wire hashes MUST differ while the canonical checksum MUST match. All **98,304** rows have a different Map key order in the shuffled fixture.
- Four independent clean localhost server phases on **one** GitHub runner: A0 sorted, B1 shuffled, B2 shuffled, A3 sorted. SHA-verified unmodified binary and no code change during the measurements. Use the validated R21 phase harness; pin its git blob SHA `fd3673d47fcdf575033bfebfaaf56ef2e2affc60` and retain every helper's SHA256. STOP MERGES only as a declared controlled variable, equal across all four.
- Measure watchdog+worker `/proc` process CPU and write_bytes over inserts, global `system.events` TAGS cache hits/misses, QueryFinish CPU/read_rows/bytes, exact `TAGS(id,tags)` fingerprint ordered by stable ID and full nonempty native `avg by(project)(avg_over_time(gauge[30m]))` labels/timestamp/numerical values.

## Decision rules

1. Any changed canonical SHA, physical ID+Map hash, source/binary/DDL, unequal wire byte lengths, physical sample count, cache hit/miss counts, or native output => **INVALID_METHOD_OR_CORRECTNESS_FAIL**. Do not reclassify as speedup.
2. Any sorted A/A ingest CPU or native p50 drift above **25%** => **INVALID_METHOD**. Never relax the threshold after viewing results.
3. A shuffled-vs-sorted total process ingest CPU delta must exceed **both 5% and the local control drift** to warrant an independent-runner replication. No CPU difference or a smaller difference => `NO_ROBUST_CPU_ORDER_EFFECT`. A putative CPU difference must not be called C++ sort cost without exclusive CPU profiling to separate JSON parsing and ID hashing.
4. A read latency difference is not a natural expectation if the physical ID+Map hashes and storage byte counts are identical; it is a negative control. If read divergence is material, inspect cache warmth and pipeline drift rather than attributing it to transmitted key ordering.

**Workflow:** `.github/workflows/ts-lab-r23-tags-map-order-20261103.yml`. No new build, production change or scheduled workflow. R21/R22 independent owners remain separate.
