# v4 special Float64 payloads, duplicate timestamps, and window endpoints: proof before execution

2026-11-02. Source pinned 52529f0c711566d696917678bf15e7068772fdde. No production use, no fork master changes.

## Semantics and risk

The v4 guarded one-point SQL uses `timeSeriesSelector`, then `WHERE reinterpretAsUInt64(sample_v) != 9218868437227405314` to skip **only** the dedicated Prometheus stale NaN bit pattern `0x7ff0000000000002`; `fromSelector.cpp` performs the same check in the original converter. Ordinary quiet NaN is legitimate sample data and must not be removed. Native `timeSeriesAvgToGrid` handles duplicates with max-non-NaN at each timestamp. The transformer remaps time `t` to `t-T_i`; for disjoint 1d steps, the original bounds are `(T_i-86400000,T_i]` with open left and closed right.

The C++ optimization has been verified on six finite precision/duplicates cases, 20 filtered queries, and 90-day churn; it has NOT been verified for special IEEE payloads and boundary times. A fallback to the original algorithm is mandatory for unsupported query shapes.

## Cases

Create an isolated v11 Float64 TimeSeries through public outer INSERT with SQL constructors (avoid lossy JSON serialization of non-finite values). Use **independent build tags** per stage for each case. Insert:
1. ordinary quiet NaN `0x7ff8000000000000` alongside finite 5, expected `NaN` where semantics include ordinary NaN;
2. stale NaN `0x7ff0000000000002` alone (no sample output) and alongside finite 7 (must not affect finite average);
3. positive infinity `0x7ff0000000000000` with 1 (expected Inf);
4. negative infinity `0xfff0000000000000` with -1 (expected -Inf);
5. both infinities same series (expected NaN);
6. conflicting valid finite and ordinary NaN at same timestamp in *separate* INSERTs, test max non-NaN dedup selection;
7. exact right-boundary T0 sample, exact open-left-boundary T0-1d sample, T0-1d+1ms sample, T0+1ms sample, and samples on the second evaluation's right boundary; compare output time placement.
8. positive zero and negative zero duplicate timestamps (bit-level comparison of zero sign where output serialization preserves it).

**Comparison:** original baseline / v4 ON / v4 OFF / original baseline, unchanged data; exact label/timestamp keys and Float64 classification for NaN/Inf/finite, sign for Inf, finite values with 1e-10 relative and 1e-8 absolute. Include raw JSON, not only normalized floats. If same series is omitted due stale only, omission must match. Opt-out must preserve original. Two warmups plus 5 measured repetitions per phase.

This test verifies correctness only for isolated data and selected window boundaries. It does not prove overflow fallback, arbitrary sub-millisecond scale, offsets, @, subqueries, Access Control/ACL, later physical merges or production concurrency. Do not conflate KBN grouped stage (still optional) with v4's ordinary stage avg.

## Failure policy

Every mismatch is a correctness blocker even if the faster path wins on Jenkins. Do not auto-promote. Save the exact SQL, payload bits, raw output and query log. No writes outside lab branch.
