# Exact-stage TAGS / TimeRanges / SAMPLES isolation: pre-registered experiment

2026-11-02, pinned ClickHouse 52529f0c711566d696917678bf15e7068772fdde. All tests use isolated CI admin SQL, never a production tenant path. No C++ change.

## Known data and cost hypothesis

14 days x95k new unique sample IDs/day=1.33m IDs. Exact stage-0007 appears 2500 per day, hence exactly 35000 selected IDs and samples. Full query after stage projection: CH 484ms median, VM77.5ms, run 41486787371. Direct TAGS exact stage count about23ms from 41489795764. Source fixture SHA256 8f3a36846f80d6be5a8604d142e934399f2687094766c57bf105e43977f52858.

Let T = selected TAGS IDs (35000), R = IDs whose TimeRanges intersect the range, and S = selected physical SAMPLES. An exact matching sample must have ID in T intersect R and sample timestamp within requested range. A direct SQL query is a diagnostic proxy; it is not a correct substitute for native timeSeriesSelector or any access-control boundary.

Falsifiable hypotheses:
- H1: TAGS exact stage ID extraction and ID-set construction alone take less than 100ms.
- H2: TimeRanges full-sorted merge join over 1.33m IDs dominates even though 35000 matching IDs are output.
- H3: SAMPLES index pruning for id IN builds/reads many granules and dominates CPU.
- H4: repeated zero-init/memset and memory representation dominates index work. Use sampled CPU stack plus counters and actual pipeline.
- Null: No isolated operation explains native critical path; combined operation or scheduling overlap dominates. Do not add isolated durations as if a disjoint latency decomposition.

## Predefined controls

(A) matching TAGS uniqExact(id), expected 35000.
(B) time range count across the exact TimeRanges target UUID.
(C) matching TAGS INNER JOIN exact TimeRanges target by id, expected 35000 (diagnostic; native joins can differ).
(D) SAMPLES sum(length(samples)) with id IN matching TAGS IDs, expected 35000.
(E) same SAMPLES scan with bucket/min_time/max_time bounds, expected 35000.
(F) same samples scan with ids from tags JOIN timeranges, expected 35000.
(G) native prometheusQueryRange 14d exact stage, 14 returned points, checked numerically against frozen reference in subsequent full-query checks.

Discover each inner target by the same generated UUID suffix from system.columns, assert schema and counts. Each independent query stores EXPLAIN indexes, query_log, processor profile, CPU leaf samples, disk free/inodes, merges, physical I/O and query outputs. Only compare independent operation times as diagnostic bounds, not causal speedups. Record failures and partial results. Avoid new binaries/builds and preserve running O2 jobs. Full production optimization also needs ACL/IEEE/dense/duplicate/VM parity, not proven by this experiment.

## Mathematical observation

T/N = 35000/1330000 = 2.6316%. A truly selective ID-dependent algorithm could scale closer to T, while a merge join or sparse primary-key index can still perform O(N) work. This is an opportunity bound, not a measured speedup. No patch justified until hardware/software CPU stacks and full-query A/B/A agree.
