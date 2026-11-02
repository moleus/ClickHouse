# At-least-once retry falsification and ReplacingMergeTree FINAL alternative

2026-11-02. Evidence run 41466762409 shows fast event stage SQL on exactly one sample per logical build/stage/day, and 41467676887 shows faster weighted rollups. Neither data model is idempotent: a retry can double-count one series. This blocks production readiness even when query results match in a no-retry fixture.

## Proof
Consider two unique series at one day/stage: values10 and20. Correct per-ID mean is 15. If the first input is replayed as another event, raw event aggregation produces (10+10+20)/3=13.3333; original series semantics still have two logical series. A SummingMergeTree sum/count incremental rollup also double counts retry, and a materialized view sourced from ReplacingMergeTree does not retract an earlier update at merge time.

## Experiment
Reuse SHA verified 40,000 one-sample build events. Create an independent ReplacingMergeTree table `events_dedup`, with stable logical key `(metric_name,stage,project,branch,build,ts)`, same finite payload and one day per build. Insert all 40k events, then replay 2k IDENTICAL rows. Disable background merges to preserve the negative control. The raw no-FINAL path must differ from VM/TimeSeries for at least one affected stage; `FINAL` must keep logical40k unique keys and reproduce all 12 workload results of baseline VM, CH TimeSeries and raw event SQL.

Compare `SELECT ... FROM events_dedup FINAL GROUP BY stage,epoch` to VM1.134 and raw event stage, with randomized 2 warmups+6 reads. Track p50/p95, query memory and disk, showing the cost of correct retry handling. Reject if `FINAL` breaks output or cost is high. Do not build auto-updating aggregation MV from ReplacingMergeTree and falsely claim correction safety.

Scope limits: exact identical retries only; conflicting values at same key, timestamp changes across partitions, version-ordering, deletes, tenant ACL and cross-replica concurrency require independent design and proof. This is not an all-PromQL substitute or a production approval.
