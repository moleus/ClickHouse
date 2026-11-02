# A conservative active-window upper bound from existing TimeSeries time ranges

2026-11-02. Source-level mathematical proposal, NOT an implemented planner or measured speed improvement.

## Data source
Pinned ClickHouse source `src/Storages/StorageTimeSeries.cpp` defines v11 inner `TimeRanges` as `AggregatingMergeTree ORDER BY id` with `min_time SimpleAggregateFunction(min,DateTime64(3))` and `max_time SimpleAggregateFunction(max,DateTime64(3))`. `StorageTimeSeriesSelector.cpp::addTimeRangesTableJoin` already reads this table to restrict candidates on time intervals, though the current join projects an ID and matched flag, not a full per-ID bound in the user-facing result.

## Proof
Take one selected TimeSeries ID with sample timestamps `t_1...t_k`, with stored exact bounds `a=min(t_i)` and `b=max(t_i)`. For daily evaluation points `T_j=T_0+jW`, window `(T_j−W,T_j]`, and nonoverlap `W=86400000ms`, the unique grid index for any retained sample timestamp is `j(t)=ceil((t−T_0)/W)`.

The ceil function is monotone, so all active sample indices lie inside the integer interval
`[ceil((a−T0)/W),ceil((b−T0)/W)]`.

Let `lo=max(0,ceil((a−T0)/W))`, `hi=min(E−1,ceil((b−T0)/W))` and `U_ID=max(0,hi−lo+1)`. Then `P_ID≤U_ID`, where `P_ID` is the actual number of (ID,j) pairs containing samples in the query. Summing over selected IDs gives
`P≤U=Σ_ID U_ID≤S⋅E`.

This is **only an upper bound**: it can be very loose for bursty long-lived series with gaps. If the stored bounds include data older/newer than the query, the clipped bound can still be used safely (may increase U). For one-sample IDs (`a=b`), `U_ID≤1`. For 40d dense gauges (`a≈T0−W,b≈T_(E−1)`), `U_ID≈E`. It directly distinguishes the two measured 41453548480 workloads.

## Implementation conditions
1. Validate that persisted `min_time` and `max_time` correctly combine multiple physical parts, including late inserts, unmerged states, TTL and potentially duplicated rows. If data is missing, do **not** produce a too-small U. Treat absence or invalid bounds as U=E (safe overestimate), not zero.
2. Only consider IDs surviving ALL label selectors, user row policies, and the exact time range. Filtering after a global metric count would break selective-query performance/authorization.
3. Existing `timeSeriesSelector` may be able to attach per-ID metadata while already selecting IDs, but actual current `TimeRanges` join only projects `matched`. This would require C++ changes and a measured cost; querying TAGS+TimeRanges separately was already found too expensive for small requests (41437764684).
4. Even with a tight U, the cost inequality depends on per-group overhead, sample count M and stable coefficients, not just U/S/E. A safe cost decision needs a conservative crossover condition and must reject (fallback to native) when uncertain.
5. No threshold is being guessed here. Workload allowlisting (explicit default-off setting) is the only already measured deployment-level policy.

## Falsification experiments before C++ changes
- SQL offline instrument the 40d dense/sparse fixture: compute per-ID [a,b] from the actual inner TimeRanges target via a distinct ID aggregation (no direct write), derive U and measure actual P from selector outputs. Require P≤U for every tested metric and query E3/E14/E40, compare analytic expectation: one-sample IDs U≈S and dense IDs U≈S E.
- Add late inserts and gaps, verify bound remains conservative. Record extra query latency and memory separately. Do not put SQL probe into per-request critical path before it is proven economical.
- Only after this, consider modifying `StorageTimeSeriesSelector` to expose the bound from an existing read with per-ID filtered metadata.

The mathematical bound does not establish timing gain, physical IO reduction or security correctness. Existing v4 dense-case regression and narrow-match regression remain independent rejection conditions.
