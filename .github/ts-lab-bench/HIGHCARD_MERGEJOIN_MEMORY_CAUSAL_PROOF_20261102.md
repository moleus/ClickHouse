# 1.33m-series peak allocation attribution after MergeJoinTransform OOM

2026-11-02. Run 41469253120 faithfully generated 14×95,000 distinct one-sample Jenkins stage IDs, 1,330,000 total; fixture SHA256 `8f3a36846f80d6be5a8604d142e934399f2687094766c57bf105e43977f52858`, 258,336,218 JSON bytes; imported identical 178,536,218-byte VM Prometheus representation. VictoriaMetrics reported 95,000 last-day samples. First v4 full-metric E14 query at max server memory 5GB failed: `MEMORY_LIMIT_EXCEEDED`, current RSS4.66GiB, `MergeJoinTransform`. This is real resource evidence, not a compile/fixture bug.

## Distinct hypotheses
H1: The v11 TAGS↔TimeRanges full_sorting_merge `MergeJoinTransform` grows excessive state for 1.33m IDs. Pinned source `StorageTimeSeriesSelector.cpp` function `addTimeRangesTableJoin` constructs LEFT JOIN over (id, matched), and `applySettingsForSelectIDsQuery` forces full_sorting_merge, optimize_read_in_order. The source comment claims streaming; an OOM calls for an actual profile, not an assumption it is the join's own allocation.
H2: `ContextTimeSeriesTagsCollector` storing 1.33m labels and intermediate IDs dominates, with MergeJoinTransform only the *location of the final failed allocation*. H3: parallel aggregation duplicates ID/group states across threads; max_threads=1 should reduce memory substantially if so.

## Experiment
Replay exactly the same SHA-verified fixture in CH and VM, same O1 binary; same 4GB per query/5GB server max. First VM broad 14d query and its output, then run independent probes with dedicated query IDs, no concurrent queries:
- direct outer TimeSeries metadata, inner TAGS/TimeRanges/SAMPLES counts, exactly 1.33m logical series;
- `timeSeriesSelector` SELECT count(), SUM(length(samples)), distinct IDs, without stage aggregation;
- original PromQL full metric E3/E14 and v4 ON E3/E14, max_threads=1 and 4 separately;
- exact-stage and project selectors E14;
- EXPLAIN PIPELINE and query log for passing/failing cases, including memory_usage, ProfileEvents, memory profiler stack symbols if supported.
Do NOT silently reduce cardinality, raise cap, or treat failed SELECT as a successful benchmark. Log OOM code/text as a normal diagnostic outcome and continue cases. The same-data VM query must be executed independently before OOM to report its own p50.

## Decision
If selector-only fails, focus on tags/time-range join/collector and memory profile before changing group AVG; if selector-only succeeds but v4 fails, focus on per-(ID,day) hash state and arrayJoin. If threads=1 passes but 4 fails, investigate parallel partial-state duplication. A source patch that skips the TimeRanges join would require separate proof that it cannot bypass filters, late data, ACL or PromQL semantics; no such patch is approved without attribution.
