# Stage-level compensated SUM: negative-control test before C++ implementation

Date 2026-11-02.

Context: the original ClickHouse TimeSeries PromQL converter uses `avgForEach` (ordinary `avg` numerator) at the grouping stage. Prometheus `promql/engine.go` instead calls `kahansum.Inc` on cross-ID float values and falls back to incremental mean near overflow. Run 41439318904 demonstrated repeated original `avg by(stage)` values can differ greatly from exact `math.fsum(values)/N` on adversarial multi-ID cancellation.

Hypothesis: replacing the final SQL `avg(series_avg)` in the **SQL-only translated onepoint plan** with `sumKahan(series_avg)/count()` may reduce error relative to the mathematical oracle without touching the per-ID `timeSeriesAvgToGrid`. It is NOT guaranteed equivalent to Prometheus; the existing `sumKahan` has already failed the per-ID cancellation test in run 41436423717, and division/overflow/NaNs need separate tests. There is no v3 C++ modification in this experiment.

Proof/cost bound: number of series in group G; original stage ordinary AVG uses O(G) additions; candidate uses O(G) compensated additions and one final division. It may consume a larger per-group state but keeps asymptotic O(G). On cancellation `[1e18,1,-1e18]` true result 1/3; error of both variants must be measured under 15 repeated queries with identical input, not inferred. Group order affects both. Report baseline, normal native-onepoint, Kahan native-onepoint and baseline recheck, p50 query duration, per-stage min/max/unique outputs and high-precision `math.fsum` reference.

Accept only a clear reduction in error for all relevant adversarial cases and no major performance regression. If Kahan still fails, reject this SQL replacement; next engine-level option is a Kahan-Babuška-Neumaier compensated *stage-level* aggregate with explicit merge semantics and differential Prometheus validation. Source `AggregateFunctionTimeseriesCompensatedSum.h` is a possible reusable arithmetic reference but cannot be applied directly across IDs with equal timestamps because native TimeSeries sample aggregation deduplicates equal timestamps.

No upstream/production writes, no C++ build, no scheduled runs.

## Tested result: reject sumKahan/count as a grouped AVG remedy

Run `41440060987` SUCCESS as benchmark execution; 15 measured executions per phase after 2 warmups, identical fixture, same pinned O1 baseline binary. `onepoint_stage_sumkahan` still had large numerical errors and instability:
- `group_many_small` expected 127/129≈0.984496; Kahan median **-0.100775**, observed range **-0.186047 to 0.705426**, while plain onepoint native-dedup median 0. This replacement does not reliably improve accuracy.
- `group_many_interleaved` expected 0.5; stage sumKahan median **-0.0125**, range -0.016667..-0.004167; normal onepoint median 0.
- `group_big_split` expected ~0.502488; stage sumKahan median 0 (some negative -0.014925). Plain grouped version also returned 0.
- `group_three` expected 1/3; sumKahan median **-1/3** with observed [-1/3,+1/3], versus original median 0 on same dataset. It can be worse.
- Query p50 normal native-dedup **21.199ms**, sumKahan stage **21.456ms**, original recheck **20.284ms**, but this is a small test and numerical correctness is dominant.

**REJECT** replacing `avg(series_avg)` with ClickHouse built-in `sumKahan(series_avg)/count()` as a general semantic fix. The source of error is compensation implementation and/or partial-state merge order; confirm in source before implementing another aggregate. Prometheus `kahansum.Inc` uses Neumaier compensation and special overflow switching; ClickHouse `sumKahan` is not shown equivalent by these tests. Keep original `avgForEach` behavior outside separately reviewed fidelity work.
