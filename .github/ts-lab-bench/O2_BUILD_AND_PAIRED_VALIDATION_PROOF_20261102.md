# Optimized native binary validation — O2 build and exact recheck

2026-11-02, preregistered, no experimental result yet.

All 18 queries in run 41463921958 returned exact same data in ClickHouse and VM, but VM v1.134.0 beat ClickHouse O1 binary in every query, from 1.62x on sparse broad to about 30x on dense exact stage. Pinned ClickHouse build from 41447045583 is `-O1 -DNDEBUG -g0`; this is a serious performance confound.

## Hypothesis and test
Build **the same C++ changes** on the same upstream base SHA 52529f0c711566d696917678bf15e7068772fdde with LLVM22.1.8 and `-O2 -DNDEBUG -g0`. No algorithm changes and no unrelated workflows or tests. Keep 2.5GB ccache state, do not cancel existing jobs, and isolate CI to the lab fork and branch. Run compile smoke + existing PromQL suites.

Do NOT compare an O2 binary to O1 timings from a different runner as if it were a controlled speed gain. Re-use one physical dataset and one runner, original baseline, v4 O1, and v4 O2, warmups+6 interleaved queries for broad sparse and dense plus exact stage. Check exact labels/timestamp/Float64 and query log memory read_rows and CPU. Run VM v1.134.0 on same runner for the final paired decision.

Expected O2 may reduce CPU-heavy math/hash operations; does not change asymptotic number of selected mark reads, number of ID sets or query plan; unlikely to solve 10–30x selective latency gap alone. If full rebuild takes too long, preserve caches/artifacts; **do not interpret a running build as a successful proof**.

For production readiness still require O2 correctness, CLI/API ACL, retries, HA, S3, data churn 95k new per day and same-hardware memory/latency throughput curves, and robust VM comparisons. This build is only one quality gate.
