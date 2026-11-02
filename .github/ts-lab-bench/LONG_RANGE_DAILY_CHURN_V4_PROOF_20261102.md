# Long-range daily-churn TimeSeries performance and correctness — before execution

2026-11-02. Pinned upstream ClickHouse source 52529f0c711566d696917678bf15e7068772fdde. Lab-only, no production writes.

## Gap in prior evidence

Prior E=40 synthetic selected-cardinality benchmark held input samples constant over **3 days** while increasing output steps to 40. This made 37 extra days empty. It is valid to isolate dense-grid materialization cost but does not model a CI dashboard where new build+stage time series appear **every day** over 14–40 or 90 days.

## New fixture

- One v11 Float64 TimeSeries table with 90 consecutive days.
- 1000 unique series per day, one sample per series, total 90,000 selected unique series; stage spread across 38 values, build ID unique, branch `main` or `feature-*`, project labels. Timestamps in middle of each UTC day, evaluation at subsequent midnight. This is a 1/95 scaled version of ~95k new CI stage series/day, not a full production load.
- Query E=14,40,90 consecutive daily evaluation points ending on the final day, so selected cardinality approximately N=14k,40k,90k. The original query and fast path read identical rows/bytes. Include a branch regexp to evaluate actual Grafana-like label filters.
- Physical data/period fixed for all A/B/A phases. Ingest in batches via the official TimeSeries outer INSERT; verify 90,000 distinct series in outer TimeSeries and 90 output days.
- Run baseline original, v4 binary opt-in ON, same v4 binary opt-in OFF, baseline original (A/B/C/A). 2 warmups+5 measured queries per phase/case. Compare all labels/time/value with 1e-10 rel / 1e-8 absolute; record query-log p50 and memory p95.
- The filtered v4 setting MUST default off; a query with opt-in disabled must follow the original converter, without revealing project labels or bypassing selector.
- Use pinned O1 binaries and the same runner; no C++ compile in benchmark. No upstream write.

## Questions and rejection

1. Does native one-point still win when selected series increase with E, not only when most extra days are empty?
2. Does branch regular-expression selection preserve exact results and avoid extra scans?
3. How does memory scale with actual daily churn and E at constant series/day?
4. Does v4 opt-out match original path numerically and with similar resources?

Reject the C++ fastpath for this workload if it mismatches labels/values/timestamps or significantly increases memory; treat 1/95-scale results as evidence but not a production SLO. Strong observed speedups do not establish a universal threshold; long-range resource pressure, concurrent queries, stale values, physical part compaction and access controls remain untested. Full 95k/day x90d would be 8.55M series and is intentionally not loaded on this 7GB CI runner.
