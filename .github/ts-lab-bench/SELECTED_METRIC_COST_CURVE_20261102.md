# Selected-metric cardinality cost curve: proof before implementation

Date: 2026-11-02. Scope: fork lab only; no production change.

## Source and hypothesis

Pinned unmodified ClickHouse binary from Actions 41301314345 and v1 selector-scalar C++ binary from Actions 41420384277. The guarded C++ patch is `.github/ts-lab-pending/apply_selector_scalar_grouped_avg_cpp.py`. Its exact, no-extra-matcher shape is `avg by(stage)(avg_over_time(metric[1d]))` with disjoint 1d windows on TimeSeries v11 Float64.

The v2 patch checks `getTargetTable(ViewTarget::Samples, context)->totalRows(context)`. That number is independent of the selected metric: run 41433029087 measured 210 selected series in a table of 282246 and 15.8% regression. A fixed threshold on total table rows is rejected.

For **selected** cardinality N, E evaluation points, physical selected sample count P and nonempty (series, step) pairs Q, use the approximate work model from COST_MODEL_AND_PROOF_OBLIGATIONS_20261102.md:

`T_native = C_read(N,M) + (c_grid+c_agg)*N*E + C_group + H_native`

`T_scalar = C_read'(N,M) + c_sample*P + c_hash*Q + C_group' + H_scalar`

Here M is all rows of the mixed table; selector and plan effects are *not* assumed equal. In the controlled synthetic fixture, there is exactly one nonstale sample per series and P=Q=N; each series falls into exactly one daily window, so the variable coefficient of the candidate is approximately `c_sample+c_hash`. Its break-even, when the coefficient difference is positive, is:

`N > (H_scalar-H_native + C_read'-C_read + C_group'-C_group) / ((c_grid+c_agg)*E-c_sample-c_hash)`.

This is a *hypothesis*, not a computed production threshold. It cannot be translated into a safe guard until cheap selected-N evidence exists and effects of M, group counts, label selectivity, sample density and E are measured.

## Semantic proof obligation

Let V(s,t) be the valid samples in the window (t-1d,t]. For each nonempty series s, native `avg_over_time` returns `mean(V(s,t))`. Grouped `avg by(stage)` returns the mean of these per-series means, not a flattened mean across all samples. The v1 scalar lowerer uses the same two aggregation levels, which is algebraically equivalent only when the selected series/labels, window boundaries, duplicate, stale-NaN and tie rules agree. Floating-point results need tolerance. Adversarial runs 41420683218 passed ten cases, but additional cases remain.

## Controlled falsification protocol

1. One mixed TimeSeries table with 282036 original Jenkins series plus distinct metric names with selected counts 210, 1050, 4200, 16800 and 67200. All use 3 days, one sample per series, exact label values from the deterministic generator; stage labels remain present. Keep all other data identical between binary phases.
2. Read every metric using the same exact-match PromQL shape, 1d window and 1d step, without label matchers. Count true per-metric series during setup (not in request path).
3. Same GitHub runner, same on-disk data, original/C++ v1/original, explicit warmup and repeated timings. Check exact sets of label/timestamp keys and numeric results against first original, including empty/absent cases where applicable. Capture p50/p95 latency, query memory, read_rows/read_bytes and full repetitions.
4. Independently measure a `SELECT count() FROM outer TimeSeries WHERE metric_name=...` query to falsify the idea of doing an extra synchronous cardinality probe. It counts outer rows for this *one-sample synthetic fixture*; do not interpret it as a general selected-series estimator.
5. Reject a threshold if either baseline-before/after drifts substantially, if there is a correctness difference, or if small-N/regression evidence does not support it. Derive confidence bounds only if enough stable observations exist. Do not use a fitted intercept as a performance guarantee.

## Constraints

- No new C++ compile, no source-format changes, no upstream commits.
- Existing workflow schedule removal on fork master is unaffected.
- A/B/A experiment creates no live-production configuration. `cancel-in-progress: false`.
- A v1 speedup, if observed, does not prove its current implementation is safe for other labels, duplicate part tie-breaking, staleness or tenant access policy.
- No automatic promotion from a crossover plot to a production cost guard.

## Subsequent independent hypothesis

Inspect whether the original `StorageTimeSeriesSelector` and existing query collector expose a metric-specific *already-computed* selected ID count before query lowering. If no such field is available at C++ converter time, a correct dispatch may require moving selection to an execution-stage adaptive operator or reusing planner cardinality estimates. Avoid doing a second label lookup merely to select the fastpath.

## Measured result from the declared A/B/A protocol

Run `41435248918` SUCCESS. Full output comparisons passed. Seven measured repetitions per phase after two warmups, original/v1/original on a single mixed table and five metrics. Original-after vs candidate p50 in ms:
`N=210:18.167/18.613; N=1050:18.344/20.709; N=4200:30.816/30.498; N=16800:46.823/37.904; N=67200:134.206/102.744`.
Memory p95 at N=16800 native 38.51MB vs v1 30.29MB; at N=67200 native 114.71MB vs v1 101.43MB. Original-before/after drift is visible at small N. A threshold on the *true selected N* is still not directly executable at converter stage; count-probe latency not yet measured.

**Important later finding:** run `41436423717` proved v1 numerical differences for valid multi-sample data; series count alone is NOT a correct promotion condition. See `NUMERIC_PRECISION_PROOF_20261102.md`.
