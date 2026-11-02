# Query cost attribution: profile first, change second (2026-11-02)

## Confirmed prior evidence
- Same-data sparse Jenkins (run 41355281953): whole metric ClickHouse experimental inner path 335.7ms vs VM 645.1ms, but exact stage+branch ClickHouse 57.5ms vs VM 0.792ms. Universal claims of superiority are false under this fixture.
- Dense rate (run 41351948000): inner ClickHouse rate ~926ms vs VM ~57ms, while scanning rows ~5.8ms, decoding length ~6.7ms, hash IDs ~47ms. These are separately timed operators, not an additive query profile. Grouping + timeSeriesRateToGrid must be instrumented.
- v4 sparse 400k series run 41455684534: E40 v4 1.622s vs pinned original final 2.295s and memory 306.8 vs 467.4MB. Dense 40d v4 run 41453548480: 28% worse latency, 91% worse peak memory; all values correct.

## Predeclared causal hypotheses
H1 (selector): For an exact label matcher, TimeSeries selector's tags/ID discovery and sample lookup still touches too many parts/rows and builds an ID set instead of using narrow postings. A large fraction of the total query latency should remain when output stage AVG is removed.
H2 (grid allocation): For sparse 1-sample-per-series data, E-wide finalization has cost ~S*E, whereas v4 has ~P active (id,day) pairs. The advantage should rise with E and sparse density P/(S*E).
H3 (dense aggregation): For dense long-lived gauges P≈S*E. V4 creates P groups and must process M raw samples via arrayJoin. Peak hash aggregation and pipeline CPU should increase relative to original.
H4 (rate): Dense rate's main expensive stage is timeSeriesRateToGrid over 2.4m samples, not raw scan; optional thread scaling and pipeline processor times should expose serial bottleneck.
H5 (fixed cost): For 1–100 matching IDs, query startup, internal nested subqueries and set preparation dominate. Narrow queries can be slower than VM even if read bytes are tiny.

## Planned falsification; no code optimization before evidence
1. Run original and v4-on from *same compiled v4 binary and same physical dataset*, interleaved alternating order per case. Use sparse and dense metrics, broad/exact-stage/project selectors, E14/E40. 2 warmup + 6 measured per option, then reverse order. Keep max_threads=4 and no competing queries.
2. Query independent layers: inner tags exact/whole lookups, physical sample/TimeRanges reads, timeSeriesSelector count and sample-length, arrayJoin raw values, per-(id,day) aggregation, full PromQL. This yields non-additive component timings only; do **not** claim layer times sum to total.
3. For every timed query save QueryFinish `ProfileEvents`, read_rows/read_bytes, elapsed, tracked peak memory. For representative instrumented queries save `EXPLAIN PIPELINE`, `EXPLAIN indexes`, processor profile log and CPU sample trace when available. Profiling overhead must be measured separately from ordinary timing.
4. Summarize top CPU events and processor chains, compare broad vs narrow. A/B pairs with randomized order avoid systematic cache advantage. Extra queries are diagnostic and not query-path replacements.
5. If selector dominates narrow queries, improve a genuine native label-select path and prove policy/regex/fallback/scale, not another stage AVG. If dense aggregation dominates, change per-ID bucketing or reusable native grid state, with exact PromQL validation. A changed C++ path stays default-OFF until security, O2 optimized build, resource curves and VM 1.134 same-hardware comparison pass.
6. A later cross-engine paired benchmark must import identical samples into CH and VM, match query semantics, warm caches equally, and measure both process RSS and per-query peaks. VM query limit 165k should be reported separately from unlimited reference, not treated as an artificial speed failure.

## Failure conditions
Reject performance claims on unmatched datasets, ignored query errors, unsupported query semantics, changing hardware or cold/hot phase bias. A single fixture cannot establish "faster on all queries"; report each query family separately. Never merge into upstream or production by default. Keep CI limited to the fork and no automatic schedules or cancellations.
