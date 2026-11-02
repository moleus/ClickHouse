# Paired VictoriaMetrics vs ClickHouse benchmark, identical samples and limits

2026-11-02, predeclared before benchmark; laboratory CI only.

## Goal and truth conditions
Existing same-data run 41355281953 shows large selective query regression for ClickHouse versus VM 1.134.0: exact stage+branch 57.5ms vs 0.79ms and broad query 335.7ms vs 645.1ms. Dense 20k×120 rate sample run 41351948000: CH inner rate 926ms vs VM ~57ms. A sparse CI-specific v4 speedup vs CH baseline does NOT prove universal superiority vs VM.

## Dataset
Reuse `generate_dense_sparse_cost_ts.py` and its exact 40-day JSONEachRow fixture:
- Sparse metric `tslab_sparse_40d`: 1k new distinct series per day ×40, one sample/series = 40k series, 40k samples.
- Dense metric `tslab_dense_40d`: 1k permanent series, 24 samples/day ×40 = 960k samples.
- Stage, branch, project and build labels copied without changes; same exact Float64 values and UTC timestamp milliseconds.
- CH TimeSeries from public outer INSERT; VM from its public `/api/v1/import/prometheus` with format metric{labels} value timestamp_ms. Convert *the same fixture file*, hash both artifacts; verify CH series/sample counts and VM last-day sums.
- VictoriaMetrics pinned v1.134.0 binary, verified SHA256 from prior artifact run 41288854734. ClickHouse original binary and v4 same pinned code compiled O1 from 41447045583. VM retention >= 90d (the fixture is older than 4w); disable VM query cache explicitly. Both engines on one dedicated CI runner, no concurrent queries, vm maxUniqueTimeseries=1,000,000 to avoid unrelated enforced failures.

## Cases and timings
`avg by(stage)(avg_over_time(metric[1d]))` over E=3,14,40 on dense and sparse; selectors whole, exact stage, project=project-00 (18 cases). Test CH v4 off, CH v4 on, VM 1.134.0. For each case 2 warmups then 6 measured in rotated order (VM, CH_off, CH_on permutations). Verify exact output label/timestamp keys and float values within 1e-10/1e-8; no absent-series miscount. Report p50/p95 per engine, ratios to VM, full error details, query log read_rows/read_bytes/peak tracked CH memory, both servers' process RSS/peak sample and data-on-disk size. Do not compare CH tracked per-query memory directly to VM whole-process RSS as if equivalent.

## Falsification
- Hypothesis: CH v4 is competitive with VM on some broad sparse queries but VM remains much faster on exact-match selective queries, especially dense or few-ID cases.
- If VM results differ due MetricsQL semantics (staleness/window, rollup), isolate and report mismatches, not a performance win.
- If VM first-phase query timings drift markedly, present all raw trials, paired interleaving and cold/hot distinctions.
- A single runner, O1 ClickHouse binary, simplified data and single-node VM do not establish production readiness. Do not change global defaults, production config, fork master, upstream or auto schedules.
