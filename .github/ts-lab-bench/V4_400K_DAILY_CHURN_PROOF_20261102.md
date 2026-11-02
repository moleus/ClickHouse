# Scaling sparse one-sample-per-build TimeSeries past 165k selected IDs

2026-11-02. **Pre-run acceptance and cost proof.** Branch lab/timeseries-fastpath-ci only. Pinned source `52529f0c711566d696917678bf15e7068772fdde`. Use compiled v4 O1 artifact from run 41447045583, no rebuild.

## Why
Previous 90d experiment 41447201613 used 1,000 new one-sample IDs/day and selected 90k IDs across E=90. It demonstrated 34.2% latency reduction and 31% memory reduction, but is 1/95 of anticipated ~95k new Jenkins stage series/day and below VictoriaMetrics vmselect's 165k unique-series limit. This experiment tests the transition above 165k selected time series, where planning and hash-table/allocator costs may change discontinuously.

## Dataset and query contract
- 40 consecutive UTC days, 10,000 new unique IDs/day, one Float64 sample/ID; **400,000 unique series** in one v11 TimeSeries table, ~10k/day (approximately 1/9.5 of expected production daily churn).
- Each ID has unique build label; 38 stages; branch main or one of 19 feature names; 8 projects; values finite positive. Same format and exact timestamp scheme as previous 90d generator. Use only public TimeSeries outer INSERT and SHA256-check fixture.
- E=14 and E=40, both daily 1d `avg by(stage)(avg_over_time(metric[1d]))`; selectors exact metric and branch regex. At E14 expected ~140k sampled IDs, E40 ~400k. `count` in outer TimeSeries confirms 400k.
- Original pinned baseline / v4 opt-in / v4 opt-out / original: 2 warmups + 5 measured queries per case, exact labels/time/Float64 within 1e-10 rel/1e-8 abs. Run with max_threads4, max_memory_usage3GB, no parallel competing load. Capture client p50/p95, system.query_log rows, bytes, query duration and p95 memory.
- One runner only, no prod/fork-master writes, no automatic schedules, no cancellation.

## Falsification/acceptance
If native baseline or v4 exceeds memory/query timeout, log raw error and treat as a real resource result; do not hide, retry indefinitely or silently lower N. If v4 correctness differs or its memory exceeds baseline, reject v4 at this size. A gain at 400k selected IDs is not a proof for 3.8–8.55M production IDs. O1 runner performance is not O2 production performance. Case isolation and A/B/C/A protect against some drift but do not prove confidence intervals.

## Cost-model prediction
For short-lived one-sample IDs `S=P≈400000`, `E=40`, original emits up to `S*E=16,000,000` per-series grid slots while v4 uses roughly one one-point per ID. The absolute win depends on selected-ID tag lookup and onepoint grouping overhead. A 400k series benchmark can expose hash-table threshold/memory discontinuities absent at N90k.
