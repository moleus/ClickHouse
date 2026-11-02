# Finite nonnegative Jenkins durations: bound on group AVG rounding error

2026-11-02, mathematical proof. This is a **conditional** guarantee, not verification of production data values. Source pinned upstream ClickHouse 52529f0c711566d696917678bf15e7068772fdde.

## Why the distinction matters
The adversarial groups `[1e18,1,-1e18]` proved the original TimeSeries `avgForEach` is unstable/inaccurate under extreme cancellation (run 41439318904). But the actual Jenkins metric is a stage **duration**: if every sample is finite and nonnegative, this particular catastrophic cancellation is impossible. KBN parity for all IEEE Float64 remains important for general PromQL correctness but is not necessarily a blocking requirement for a metric-specific opt-in experiment.

## Conditional floating-point error proof

Let `x_i≥0` denote per-ID durations at a specific stage and evaluation timestamp, with n>0 contributing distinct series. Assume IEEE754 Float64 round-to-nearest, no overflow, no underflow and no nonfinite values. Every floating-point addition has a relative error bounded by the unit roundoff `u=2^-53≈1.1102230246251565e-16`.

For any sequential order of summing these nonnegative values, the standard forward-error bound gives
`|fl(sum x_i)−sum x_i| ≤ γ_(n-1) * sum x_i`, where `γ_k = k*u / (1-k*u)`, provided `k*u<1`.

A parallel reduction over a tree of additions has a path length at most n−1; this conservative bound still covers any merging order of finite nonnegative partial sums if no other nonstandard arithmetic changes the values. The final Float64 division by n adds at most one relative rounding factor `1+u`. Therefore the relative stage-average error is bounded conservatively by `(1+γ_(n-1))(1+u)−1` for nonzero mean.

Numerical examples:
- n=1000: <1.2×10^-13
- n=100000: <1.2×10^-11
- n=400000: <4.5×10^-11
- n=3800000: <4.3×10^-10

These are worst-case mathematical bounds under the stated assumptions, NOT measured errors or bitwise equality between v4 and native ClickHouse. Actual n is the number of **distinct contributing series in one stage at one evaluation time**, not the total series count across 40 days. For a 40-day dataset of one-sample-per-build series, 400k across the entire window may correspond to ~10k per day across 38 stages; these limits can be far more conservative than needed.

## Critical exceptions

1. A negative duration, subtraction-like value, nonfinite NaN/Inf, subnormal values, or overflow invalidates the stated proof. The adversarial KBN tests show exactly how cancellation can break it.
2. Intermediate per-ID timeSeries averages introduce separate errors when a series has multiple samples. The simple n-term bound applies *only* to the final stage aggregation of already computed per-ID values, and per-ID correctness is an independent obligation.
3. ClickHouse AVG's actual implementation and partial merges must be checked against the assumed summation model; do not assert exact `γ_n` for implementation-specific algorithms outside this model.
4. Real production samples have not been scanned for negative/nonfinite values; **validate** using a non-destructive targeted query before relying on this bound.
5. Rounding error compared to an ideal mean is not the same as bitwise parity between two algorithms.

## Engineering decision
For an explicitly allowlisted short-lived Jenkins stage-duration metric whose per-sample values are verified finite, nonnegative and bounded, prioritize workload-specific v4 latency/memory and access-control tests. Treat fully general grouped KBN fidelity and overflow as a separate project. Keep the opt-in setting disabled by default; do not change standard PromQL `avg()`.
