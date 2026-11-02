# Sparse one-sample stage-duration exact SQL rewrite: theorem and negative control

2026-11-02. Restricted-purpose, source-guided storage experiment; NOT a general PromQL rewrite.

## Theorem
For any stage group G at daily evaluation T_j, suppose every matched TimeSeries ID contributes **exactly one finite sample** in the half-open window (T_j-W,T_j], W=1 day. Then `avg_over_time(samples_of_ID[W])` equals that single sample, and
`avg by(stage)(avg_over_time(metric[W])) = avg(samples in G with T_j-W<t≤T_j)`
under exact real arithmetic. Identical series labels and stage/time placement are required. An event-native MergeTree can compute the right side directly without a TimeSeries ID->tags index join, per-ID grid or arrayJoin.

This is NOT generally valid when IDs have unequal numbers of samples. Counterexample: ID A has [100], ID B has [0,0] in the same day. Correct PromQL grouped average=(100+0)/2=50, while raw-sample average=(100+0+0)/3=33.3333. Therefore a generic optimizer MUST NOT use the shortcut for arbitrary dense/rate/histogram workloads. A trusted data contract or runtime proof of 1 sample/(ID,window) is required. Duplicate timestamps, stale NaN, late sample reassignment and partial series must retain original semantics.

## Experiment
Replay the already SHA-verified 40,000 one-sample-per-ID sparse fixture from run 41463921958 into three alternative MergeTree event tables:
1. `events_time` ORDER BY (metric_name, ts, stage, project), for broad day-window scans.
2. `events_stage` ORDER BY (metric_name, stage, ts, project), for exact stage.
3. `events_project` ORDER BY (metric_name, project, ts, stage), for exact project.
All have index_granularity=256. Input and output exactness against **both** original `prometheusQueryRange` and VM v1.134.0. Use 3/14/40-day whole/stage/project/branch-regex cases; only the sparse metric. Use identical time bounds and values, 2 warmups+6 measured interleaved order. Track p50/p95, physical read rows/bytes, peak tracked query memory and disk size of all copies. Fail immediately if label/time/value differs.

## Acceptance and production constraints
If at least one event-table layout is faster than VM for the broad query, report it as a potential specialized query service; it is **not** a drop-in TimeSeries replacement. A production-grade dispatch needs:
- an ingest-side assertion of exactly one sample per logical series/window, stable under retries/out-of-order writes;
- a source-of-truth mapping from the TimeSeries selector and access policy to the event query, with no tenant bypass;
- fallback to native PromQL for any query with overlapping windows, subqueries, absent labels, stale markers, nonfinite values, duplicates or unsupported query AST;
- ingress, dedup, TTL, replication, S3 and cost/HA proofs.
No claim of universal VM superiority, no prod/master changes and no CI cancellation.
