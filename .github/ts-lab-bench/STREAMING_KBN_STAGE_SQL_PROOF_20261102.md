# Grouped KBN streaming in TimeSeries SQL: next proof after successful standalone state tests

2026-11-02, written before experiment. Research-only.

## Facts

- Native `timeSeriesAvgToGrid` per-series arithmetic is correct on tested finite cancellation and duplicate cases.
- `avg by(stage)` compiles to `avgForEach` (ordinary floating point numerator) in the original converter. That original is numerically unstable on adversarial cross-ID cancellation.
- SQL-only synthetic-timestamp stage KBN in 41444399417 matched high-precision oracle on all seven finite cross-ID groups. This was `groupArray` memory-heavy.
- A named streaming C++ function `avgKBNLab(Float64)` preserving `timeSeriesAvgToGrid` Kahan-Babuska-Neumaier Summary add/merge/count exists in independent lab binary. Run 41446233352 matched math.fsum across seven groups with 1,4,8 threads and explicit partial State/Merge, 15 repeats each.
- Two 1e308 values overflow the new KBN sum and yield Inf, while mathematical mean is finite. This is a known defect for general Prometheus grouped avg; Prometheus switches to incremental mean near overflow.

## New SQL experiment

Keep the existing `timeSeriesSelector`, 1d disjoint windows, per-series native `timeSeriesAvgToGrid`, time indexing and all label projection. Replace **only** final stage `avg(series_avg)` with `avgKBNLab(series_avg)`. No synthetic timestamps, no `groupArray`.

Test with:
1. The same seven cross-ID cancellation groups (one sample per unique build series and shared stage). Compare to math.fsum / len of exact input, not only to unstable native original; 15 repeats and max_threads=1/4/8.
2. Six per-series precision/duplicate fixtures, must remain mathematically correct and match native where native values are stable.
3. Selected N4200 and 67200, E3 and E40 on mixed Jenkins data with 2 warmups+5 repeats; compare normal translated native one-point SQL vs streamed KBN stage on same unchanged ClickHouse binary and data, record p50/p95 and memory and exact output (normal finite dataset should match).
4. NaN, Inf, stale markers, and group count==0 in separate tests. Do not enable generally without overflow logic and proper Prometheus semantics.

Prediction: streaming KBN improves accuracy for cancellation-heavy grouped means without O(group_size) groupArray materialization. May increase CPU modestly for large groups. The built-in `sumKahan` is not a valid drop-in substitute (41440060987).

Accept only if all finite examples match oracle and no unexpected selector/tag group changes. Keep C++ `avgKBNLab` separate from built-in avg. Do NOT change the general PromQL converter or production.
