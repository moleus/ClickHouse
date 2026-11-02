# Dense rate() CPU attribution: predeclared falsification

Dataset is 20,000 fixed series x 120 samples each, same bytes imported into ClickHouse 26.11.1.1 TimeSeries and VictoriaMetrics v1.134.0. Previous run 41351948000 measured ClickHouse TimeSeries rate inner grid ~926ms versus VM 57.3ms, while raw scan was 5.8ms and ID grouping 34.1ms.

## Hypothesis
Rate calculation in ClickHouse spends most CPU in the per-sample TimeSeries aggregate (e.g. repeated window/sliding calculations, hash/merge or array passes), not raw MergeTree disk reads. The expectation is a large number of CPU trace samples in `timeSeriesRateToGrid` / `AggregateFunctionTimeseries...` rather than read/decompression. This is a hypothesis, not an observed flame graph.

## Experiment
Reuse prepared workflow `ts-lab-rate-cpu-profile-20261102.yml` on the unchanged pinned O1 binary and SHA-verified VictoriaMetrics. Run 2 warmups+24 measured samples for sum(rate), grouped rate by team and a single exact host, query_profiler_cpu_time_period_ns=1,000,000. Capture `system.trace_log` CPU stack symbols using `allow_introspection_functions=1` in SELECT, not globally. Preserve timing and trace counts. If no trace samples, do not infer a hot function. Query profile evidence must be followed by a source-level change and paired A/B test with numerical equivalence before promoting.

A stack-sample fraction is not equal to exact attribution of wall time; CPU-time sampling can overcount parallel threads and symbolization may be partial with stripped binaries. Reject universal speedup claims without VM O2/O3 exact same hardware and data.
