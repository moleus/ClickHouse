# Grouped-average floating-point associativity: independent adversarial test

2026-11-02. Plan written before test.

Native `timeSeriesAvgToGrid` has compensated **per-series** arithmetic, but `avg by(stage)` uses `avgForEach` and merges per-series values into per-stage sums. The proposed native-one-point SQL also uses the ordinary stage-level `avg(series_avg)`. Both are mathematically the same mean; floating-point aggregate state order is not associative. The new SQL plan may visit series in a different order, so values can differ for cancellation-heavy **different series within the same stage** even when each individual series result is exact.

Counterexample with three distinct IDs, same stage, one sample per ID: `[1e18, 1, -1e18]`. Exact arithmetic average is `1/3`; left-to-right ordinary Float64 summation returns 0. Another ordering returns `1/3`. Add stages with interleaved +1e18,-1e18,1 and with 50+ real small values between large values; vary input insertion order and original label-to-ID hash layout. Both plans must return the same labels/time/value to original at pointwise tolerance, or the candidate is rejected for the general shape. This is not about raw sample count; each series has one sample, so it exposes post-series grouping order.

Test: baseline `prometheusQueryRange`, translated native onepoint with explicit SQL dedup, translated native onepoint with native-only dedup, baseline recheck on immutable v11 dataset. Original after must match original before. Collect exact raw values and mismatches. No new C++ code required; do not infer correctness from the existing per-ID cancellation test alone.

A pass will only cover the tested stage distribution. A failure is a semantic risk even if its absolute error looks small compared with 1e18 raw samples, because the output can be small and query clients rely on it.
