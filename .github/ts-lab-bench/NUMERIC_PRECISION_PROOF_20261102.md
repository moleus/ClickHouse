# Numerical proof obligation: scalar grouped AVG vs compensated TimeSeries AVG

Date 2026-11-02. This file is written BEFORE the adversarial execution. No production changes.

## Source facts (pinned upstream `52529f0c711566d696917678bf15e7068772fdde`)

- `src/AggregateFunctions/TimeSeries/AggregateFunctionTimeseriesCompensatedSum.h`, Summary::addCompensated: native `timeSeriesAvgToGrid` keeps a Kahan-Babuska-Neumaier `sum + compensation`, then divides by count. Its comment explicitly states that plain sum would swallow small samples beside large values.
- `src/AggregateFunctions/AggregateFunctionAvg.h`: SQL `avg()` maintains a numerator and denominator; the numerator uses `AggregateFunctionSumData` with normal floating-point addition, and `mergeImpl` directly adds the numerators. It is NOT the native per-series compensated accumulation.
- The v1 patch `.github/ts-lab-pending/apply_selector_scalar_grouped_avg_cpp.py` lowers a matched PromQL query to `per_series GROUP BY series_id,gi,avg(value)` followed by `per_stage ... avg(series_avg)`. There is no semantic guard for very large cancellation or multiple samples per ID/window.
- `src/AggregateFunctions/TimeSeries/timeseriesMaxValueForDuplicateTimestamp.h` uses the maximum non-NaN value across duplicate timestamps; the v1 `argMax(value, (not isNaN(value), if(isNaN(value),0,value)))` intends to preserve that rule, which is a *separate* obligation.

## Mathematical counterexample (hypothesis to verify in the engine)

One time series has five samples in one evaluation day, with values in timestamp order

`[10^18, 2, 3, 5, -10^18]`.

The exact mathematical sum is 10 and mathematical average is 2. In IEEE binary64 naive left-to-right accumulation, adding 2,3,5 to 10^18 rounds back to 10^18; then subtracting 10^18 gives zero, so naive avg is 0. The native compensated summary keeps the 10 lost units and gives 2. If ClickHouse's execution order follows this accumulation, the original and v1 path differ by 2, far exceeding existing pointwise numerical tolerances.

This is a source-level counterexample for **replacing the native per-ID average with ordinary SQL avg()**; it is NOT proof the runtime produces different answers until tested. Native grouped stage `avg` of one series cannot mask that difference.

## Test and acceptance

- Construct a small v11 Float64 TimeSeries table; import all samples through its public outer `INSERT`. Use 1h buckets (default), daily aligned evaluation and exact metric name, matching the v1 C++ AST guard. Include a simple finite control series and at least two cancellation patterns.
- A/B/A: original `41301314345`, v1 `41420384277`, original again, same immutable on-disk data and query. Save raw serialized values, stage labels and timestamps. Compare original before/after strictly and report any v1 value differences without silently failing the evidence upload.
- Also run SQL arithmetic probes of `avg`, `sumKahan/count` and `sum` as diagnostics, not as proof that sumKahan matches the native Babuška-Neumaier algorithm in every order or for NaN/Inf.
- If a discrepancy is observed, the v1 path is **incorrect for general multi-sample series**, regardless of its speed on one-sample Jenkins data. Do NOT enable v1 globally. Restricting only by query shape or estimated N cannot fix this semantic issue. The future lowering must preserve the native per-ID summary operation or prove a strict one-sample-per-window invariant.
- No C++ rebuild is required for this falsification. Do not cancel other jobs or modify upstream.
