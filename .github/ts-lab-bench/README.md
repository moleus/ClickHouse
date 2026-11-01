# ClickHouse TimeSeries laboratory measurements

Repository scope: `moleus/ClickHouse`, branch `lab/timeseries-fastpath-ci`. All numbers below are for **unpatched** ClickHouse 26.11.1.974 unless stated otherwise. The C++ fast path must be built and measured separately.

## Data and environment

- Synthetic monotonic counters: 50,000 series, 120 samples each (6,000,000 samples).
- 60 evaluation steps, 60 seconds apart, 5-minute rate window.
- Same generated input imported into ClickHouse TimeSeries and VictoriaMetrics 1.134.0.
- 4-vCPU, 4-GiB shared runtime. Requests were sequential. HTTP latency includes response encoding.
- The test is not a representative production workload.

## Confirmed results

- Baseline `sum(rate(bench_metric[5m]))`: ClickHouse p50 **1069.707 ms**, VictoriaMetrics p50 **101.994 ms** (20 requests after warmup).
- SQL variant with two aggregation stages: p50 **796.810 ms** (20 requests). It gives the same numerical result as native ClickHouse on this dataset. This is **not** a measurement of the C++ patch.
- Stage-cost experiment (8 requests after warmup): selector count p50 **177.279 ms**; selector sample-length sum p50 **166.957 ms**; two-stage `timeSeriesRateToGrid` grouped by tags p50 **793.304 ms**.
- Grouping rate by the full `id` tuple gave **823.165 ms** p50, versus **793.304 ms** for `timeSeriesIdToGroup(id)`. The numeric outputs agree on this dataset (maximum absolute difference `4.09e-11`). This change does not improve latency.
- Changing `max_threads` from 1 to 8 gave p50 values of **817.26, 805.70, 807.36, 819.50 ms**. No meaningful scaling was measured.
- Disabling `optimize_read_in_order`: **850.878 ms** versus **841.851 ms** p50 with it enabled. No measurable benefit in this run.
- ClickHouse and VictoriaMetrics outputs have identical timestamps, but different rate values: maximum absolute difference **0.0125000006** and maximum relative difference **3.0e-6** across the 60 output steps. Do not describe the engines as semantically identical.

## Reproduce

1. Generate data with `generate.py`, then import into both engines. Review `benchmark_http.py` for the HTTP method and measured expressions.
2. Run the native baseline with `benchmark_http.py`. Keep the same endpoints and input data.
3. Run `bench_rate_stages.py --manifest <path>/manifest.json --ch http://127.0.0.1:18191 --table bench_ts_50k --output result.json`.
4. Run `validate_promql_extra.py /path/to/clickhouse` for eight more correctness cases (baseline confirmed on ClickHouse 26.11.1.974).
5. After the patched CI build produces a binary, download it with `download_lab_binary.sh`. The helper checks archive and binary SHA-256 before running the executable.

## Source and limitations

- Data: `results/2026-11-01-ch-unpatched-vm-50k.json`.
- Stage timings: `results/2026-11-01-ch-rate-stage-costs-50k.json`.
- Parallelism: `results/2026-11-01-ch-rate-parallelism-50k.json`.
- Numeric differences: `results/2026-11-01-ch-vm-rate-value-diff-50k.json`.
- No C++ patched binary timings are available yet. Compile, run regressions, and verify an artifact and prerelease before claiming a patch speedup.
- The unpatched ClickHouse binary and CI build may use different compiler flags (`-O1` in CI). Compare patched and unpatched builds with identical flags for an isolated speedup result.
