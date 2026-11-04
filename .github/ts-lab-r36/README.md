# R36: bounded interning for native one-label aggregation

**Status: UNMEASURED C++ candidate, build initiated. No speed or memory gain is claimed.**

- [Canonical R36 root](../ts-lab/claims/roots/R36.json) and [primary claim](../ts-lab/claims/R36-bounded-single-label-intern-v1.json).
- [R35 compiler baseline](../ts-lab-r35/README.md), compiled candidate build [41787693315](https://github.com/moleus/ClickHouse/actions/runs/41787693315).
- [Source-pinned R36 patch helper](apply_single_label_intern.py); [path-triggered, isolated build workflow](../workflows/ts-lab-r36-single-label-o2-20261104.yml).
- Pinned unmodified ClickHouse source: `52529f0c711566d696917678bf15e7068772fdde`.
- Match LLVM 22.1.8, Release -O2 and jemalloc. Baseline for R36 **must be R35**, not unmodified R29 stock.

## Source mechanism

In native `avg by(stage)(avg_over_time(ci_duration[1d]))`, R35 avoids an intermediate duplicate-check GROUP BY after dropping a constant metric name. The remaining final aggregation still applies `timeSeriesRemoveAllTagsExcept(group, ['stage'])` to the original full-label groups.

The collector already deduplicates **equal input group IDs** in `transformTags()`. That helps only when full labels repeat. For the high-churn CI fixture, full groups differ by build and other labels, while all 1,330,000 groups project onto just 38 stage values.

R36's single-label path looks up original group tags, obtains the *value* of the retained label, interns it into a bounded lookup table, and creates only one projected tag set per distinct output value. It maps every original row to the projected group via compact `UInt16` indices. The source labels remain owned by shared pointers during all string-view lookups. The tag collector's existing `getGroupForTags()` registers final groups; no new persistent data structure is added.

Conditions: exactly one retained label, at least 128 input groups, and no more than `min(4096, N/8)` projected tag values, including one missing-label value. If the bound is reached, the function falls back to the existing `transformTags()` **before registering any projected group**.

This is not SQL preaggregation, selector pruning, R34 bucket-state tuning, or a replacement of per-series range calculations.

## Expected work and limits

Let `N` be the number of distinct full input label sets in the vector and `D` the number of distinct output values (plus the missing-label class). With `D << N`, source `transformTags` can construct up to `N` projected temporary tag sets and perform `N` projected-tag hashes. R36 constructs at most `D` tag sets and hashes the retained value for each input. The projected-group vector still needs `N` entries. It also retains a temporary source-tags pointer vector. The reduction in *end-to-end* CPU and memory is unknown until measured.

No win is assumed for high-cardinality values, complex tag manipulations, multiple retained labels, source ID maps, metadata JOINs or SAMPLES reads.

## Correctness gates

1. Native original R35 and R36 output parity for complete labels, every timestamp and value, NaN/Inf/stale payload, missing versus empty tags and mixed-metric errors. The 147 native R35 compatibility cases remain applicable.
2. Single retained label only; unchanged fallback for multiple labels and for `without`; high-cardinality cardinality cap must fall back cleanly without collector changes.
3. Verify the existing group identity and tag group 0, including absent labels, and ensure no premature `std::string_view` destruction. Run ASAN/TSAN or memory instrumentation where feasible.
4. Plan and total read rows/bytes remain unchanged; negative plans outside R35's proof remain unchanged.
5. Every query must match an independent generator-derived oracle, not merely the other binary.

## Full native performance gate

Once the R36 binary artifact passes source and smoke verification, perform a same-host, fresh-server `R35 / R36 / R36 / R35` comparison using the verified R35 `native.py`, `guard_suite.py` and `run_phase.py` harness, with an R36 binary variant added to the runner. Preserve original input parts. Use 2+ warmups, 4+ measured repetitions per phase, control drift, completed QueryFinish logs and the full output oracle.

Primary: 1.33m series, 14 days, 38 stages, `avg by(stage)(avg_over_time(ci_duration[1d]))`. Controls: filtered stage/project, 8192 dense counters with 256 samples, high-cardinality single-label with cap exceeded, missing/empty stage and concurrent requests at 4CPU/4GiB/2.85GB server budget. Report wall, query CPU, memory tracker, RSS, distinct source/output groups, registered group counts and native read rows/bytes. Do not infer p95/p99 from a short pilot or multiply percentages from R34/R35.

**Decision rule:** no numeric outcome until the compiler build succeeds and full native tests pass. Even with a native gain, sustained read/write, retention, tenant/ACL and distributed gates remain open. Master/upstream/production remain untouched.
