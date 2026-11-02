# Second iteration: sufficient statistics for one-sample Jenkins-like daily AVG

2026-11-02, proof before new experiment. The first restricted event SQL approach beat VM v1.134.0 on whole metric (E40 14.93 vs 83.01ms), project E40 (8.92 vs 12.66ms), but lost exact stage E40 (8.44 vs 4.02ms) and E3 (7.13 vs1.266ms) in run 41466762409.

## Mathematical identity
Let G be a union of groups of samples (for one day/stage, optionally project and branch). For each subgroup g store finite `s_g = Σ x_i` and exact integer `n_g = count(x_i)`. Then `(Σ_g s_g)/(Σ_g n_g)` is the exact real arithmetic average of raw samples. Under the previously proved **exactly one sample per distinct series per daily window** condition, it equals `avg by(stage)(avg_over_time(...[1d]))`. Unlike `avg(avg_g)`, weighted sum/count must be used because groups have different sizes.

This is not universally equivalent under IEEE Float64 due aggregation order, infinities, NaNs and stale marker semantics. This is not a general PromQL rewrite and not an exact state for arbitrary subqueries. Require strict per-case data-finite and one-sample contract and compare outputs to VM and original TimeSeries before reporting speed.

## Hypothesis and data
Using the same hashed 40k sparse events already inserted in run 41466762409, build two additional ordinary SummingMergeTree tables:
- `rollup_stage`: `(metric_name,stage,epoch,sum_v UInt? (Float64),n UInt64)`, ORDER BY (metric_name,stage,epoch); ~1,520 logical rows for 40d.
- `rollup_facets`: `(metric_name,stage,project,branch,epoch,sum_v,n)`, ORDER BY (metric_name,stage,project,branch,epoch); expected fewer than 40k rows (depends on correlations).
Persist sum and count. The setup uses bulk `INSERT SELECT GROUP BY` from the same immutable `events_stage` table. The benchmark must report the extra disk space and ingestion work; an offline bulk build is NOT proof of streaming correctness.

For E3/14/40 and whole, exact stage, exact project and branch regex: compare VM1.134.0, CH TimeSeries v4, raw event stage or project table, rollup_stage (only whole/stage), rollup_facets (all), 2 warmups+6 measured, randomized order, same runner, exact label/time and numeric tolerance 1e-10/1e-8. Record p50, p95, read_rows, disk, query memory and CPU.

Reject if rollup increases latency for most selective queries, does not preserve output, or demands disproportionate memory/disk. A possible fast result is still an exact-data-contract specialization and cannot be called production-ready until incremental ingestion, late data/retries, dedup, auth/ACL and HA are proved.
