# v3 guarded C++ native one-point AVG — proof and experiment gate

Date 2026-11-02. Laboratory-only C++ query lowering; this is NOT a production change.

## What the last two tests established

- Run 41436423717: v1 per-ID ordinary SQL `avg(value)` is **not equivalent** to native compensated `timeSeriesAvgToGrid`; three valid cancellation series gave wrong values. Reject v1/v2 for general multi-sample data.
- Run 41437063380: SQL translation to a one-point `timeSeriesAvgToGrid` reproduced all four original outputs, including three cancellation cases.
- Run 41437945766: explicit SQL `GROUP BY series_id,timestamp` / `argMax` can be removed without changing six tested outputs, including conflicting duplicate timestamps across physical inserts; native `AggregateFunctionTimeseriesSamples` already handles same-timestamp max.
- Run 41438173934: original / with SQL dedup / native-only dedup / original, exact original outputs for N=4200 and 67200, E=3/14/40. For N=67200, native-only-dedup p50 103.288, 106.479, 99.158ms; original repeat p50 160.919, 153.625, 205.857ms. The last baseline drifted from 169.239ms initial, so do not claim an exact universal 51.8% speedup; still substantially faster than both baselines. Source read rows/bytes matched.

## Algebraic and implementation claims

For disjoint evaluation windows (T_i-L,T_i] with step>=L, translate each selected original sample (t,v) to (t-T_i,v) and call exactly the original native `timeSeriesAvgToGrid` aggregate with one evaluation at 0 and the same window length L. This preserves numerical compensated accumulation and duplicate-timestamp policies in the native aggregator, as long as:
1. all samples at equal timestamp for an ID are allocated to the same i, and timestamp bounds (open left, closed right) are checked;
2. sample value bits are not changed, with stale payload filtered exactly as native `fromSelector` does before range operations;
3. native state/bucket semantics are invariant under the constant time shift (needs broader testing for part boundaries, NaN, Inf, negative values, noncanonical IDs);
4. stage-level average is taken over the resulting **per-series** values, never over raw samples.

The original query contains output count E per series and emits nullable grids. The transformed path emits only Q nonempty series/window results. Model:
`T_original=C_read + a*N*E + C_groups`
`T_v3=C_read' + b*P + c*Q + C_groups' + H`
where N=selected series, E=grid steps, P=selected sample count, Q<=min(P,N*E). No general speedup is proved; in particular small N and dense P>>N can regress.

## Lab v3 C++ eligibility and safety

Copy only v1's strict AST guard into an isolated CI patch: v11, Float64, UTC, DateTime64(3), `avg by(stage)(avg_over_time(exact_metric[1d]))`, 1d disjoint steps, at least two output timestamps, no other label matchers, binary expressions, offsets, or subqueries. Use native `timeSeriesSelector` and its query-context tags collector; no direct owned target references or physical schema changes.

**Do not introduce a table-wide cardinality guard**: run 41433029087 falsified it. **Do not introduce synchronous count/uniqExact estimate**: run 41437764684 shows >0.8–0.9ms budget for small queries, and all-time count may overstate selected time-window count. Thus v3 C++ is initially enabled for eligible queries in the experiment binary only; it remains research-only because small N can regress. Future production planning needs correct cheap selected-N information or execution-stage adaptive dispatch.

## Test and rejection plan

1. Compile exact upstream pinned source 52529f0c... with only the script-applied C++ modifications using existing LLVM/ccache. No upstream writes, no new master workflow schedules, no cancellations; `cancel-in-progress: false`.
2. Run original/v3/original on numerical cancellation fixture (old v1 failure), duplicate timestamp inserts, and small N. Fail candidate on any changed tags/timestamps/values; report all 13 PromQL smoke cases in build workflow.
3. Run same-run original/v3/original on 282036 series Jenkins (3d), normal 20k x120 dense rate and selected N/E curve. Capture p50/p95, memory, rows/bytes and baseline drift. Keep all unsupported shapes on original plan.
4. Adversarial backlog: inf/NaNs/stale bits, negative epoch, shifted window, late 14–90d, subqueries and offsets, per-stage mixed compensated summation, access and query-context ownership, full O2/O3 workloads and multiple physical part layouts.
5. No production promotion or PR based on a compile pass or only one successful benchmark.
