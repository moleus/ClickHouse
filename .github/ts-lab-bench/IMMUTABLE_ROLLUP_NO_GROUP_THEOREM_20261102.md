# Unique immutable stage snapshot: remove final aggregation safely

2026-11-02. Source run 41467676887 has weighted rollups exactly equal to VM under the one-sample-per-series/day contract; full E40 stage-only group query 8.12ms vs VM83ms, but exact stage E3 5.307ms vs VM1.14ms. Production readiness remains blocked by unsafe duplicate/retry handling.

## Hypothesis
For a **static snapshot** of the rollup table with exactly one row per (metric_name,stage,epoch), the final `GROUP BY stage,epoch` and `sum(sum_v)/sum(n)` may be replaced by direct `SELECT stage,epoch,sum_v/n`, which avoids aggregation-state creation and merging. This should reduce fixed cost, especially exact stage; small VM request is about 1–4ms.

## Proof and scope
If the stored table is known to contain EXACTLY one row for each key, the unique row's `sum_v/n` equals `sum(sum_v)/sum(n)` over that key. Under the existing one-sample-per-series/day assumption, this is the desired PromQL grouped avg. The uniqueness condition is **not enforced** by SummingMergeTree for concurrent writes; before measuring, assert
`count()=uniqExact(tuple(metric_name,stage,epoch))`
and `count()=1520`. Also verify same input SHA as previous run.

Any update, retry, or late sample may add an extra part with the same group key. Then direct SELECT is incorrect until merge or atomic rebuild, so it MUST NOT be enabled in production for a mutable rollup without an immutable snapshot+generation switch. Positive timing is a bound for a hypothetical immutable daily snapshot, not a ready streaming plan.

## Experiment
Reuse same VM v1.134 and identical samples; compare original TimeSeries, VM, normal GROUP BY stage rollup and unique-row SELECT for whole and exact stage with E3/14/40, 2 warmups+6 interleaved runs. Require full key/timestamp/value parity; record querylog time, read_rows, peak memory and CPU. No engine code, no production/fork-master changes, no scheduled jobs.
