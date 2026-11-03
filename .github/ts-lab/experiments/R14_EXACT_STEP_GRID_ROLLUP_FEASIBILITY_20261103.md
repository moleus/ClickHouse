# R14 — Exact step-grid AVG rollups: algebra, cost bounds, admission gate

Status: **source-aware mathematical candidate and deterministic Python oracle only**. No native ClickHouse query speedup, disk saving, or output parity is claimed. Root owner: [R14](../claims/roots/R14.json). Claim: [R14-exact-step-grid-avg-rollup-v1](../claims/R14-exact-step-grid-avg-rollup-v1.json). No overlap with R11 generic unversioned QueryResultCache.

## Problem statement and pinned source

Baseline is source \`52529f0c711566d696917678bf15e7068772fdde\` TimeSeries v11. [StorageTimeSeries.cpp](https://github.com/moleus/ClickHouse/blob/52529f0c711566d696917678bf15e7068772fdde/src/Storages/StorageTimeSeries.cpp) shows a SAMPLES row is a bucket of samples for **one** series, with default \`ORDER BY(id,bucket)\`. [StorageTimeSeriesSelector.cpp](https://github.com/moleus/ClickHouse/blob/52529f0c711566d696917678bf15e7068772fdde/src/Storages/StorageTimeSeriesSelector.cpp) builds the normal ID-filtered samples path. The **new** candidate is a separate, specialized derived table containing only step-aligned scalar aggregation states. It is not a general substitute for native PromQL, Rate, or arbitrarily filtered queries.

PromQL references: [query range = separate instant evaluation per step; instant lookback and staleness; range is left-open/right-closed](https://prometheus.io/docs/prometheus/latest/querying/basics/); [avg_over_time computes mean within each individual series](https://prometheus.io/docs/prometheus/latest/querying/functions/). Native histogram handling and IEEE special-value details are explicitly **outside** the proved subset.

## Exact algebra for finite floats with a stated numeric tolerance

For each predeclared evaluation timestamp t, define v_i(t) as the **one scalar obtained after executing the full per-series function** (instant selector with lookback/stale semantics, or an avg_over_time per series). If i is absent, it contributes no element. Build partial states keyed by K=(stage,job,branch):

\`S[t,K] = sum_i v_i(t)\`, \`C[t,K] = number of present series i in K\`.

For a query with no extra label predicates except supported exact job/branch filters, select matching K; for each stage return:

\`AVG(stage,t) = sum_{K in selected stage} S[t,K] / sum_{K in selected stage} C[t,K]\`, when denominator > 0; otherwise omit the stage series.

This is an algebraic identity **over exact real arithmetic**: \`(sum,count)\` is a composable sufficient statistic. With floating point arithmetic, changing the reduction tree can change low bits. Require numerical tolerance and independent native comparison, **not byte-for-byte float identity**. It is *not* correct to average averages across branch/job buckets without weighting by counts.

**Important nested-range counterexample:** If one series has values [1,3] and another has [9], then \`avg(avg_over_time(per_series))=(2+9)/2=5.5\`. Flattening all samples gives \`(1+3+9)/3=4.3333\`: wrong. The oracle first computes per-series \`avg_over_time\`, then cross-series \`(sum,count)\`.

The safe admission key includes metric identity, exact source snapshot/revision, t, function/mode, horizon/lookback, evaluation step/phase, all labels permitted for filters, and PromQL feature flags. This Python prototype supports **only exact-literal job/branch filters** and finite float inputs; unknown build or regex filters, duplicate IDs/timestamps, nonfinite values, grid mismatch, source-version mismatch or unsupported syntax **must fall back to native**. A stale sample marker suppresses that series for an instant evaluation. Range endpoints use \`(t-window,t]\`.

## Potential cardinality advantage is NOT latency

Define N as the number of per-series values that the baseline must evaluate, G the number of precomputed group states read per eligible step, and f the fraction of total native cost reducible by a G/N factor. Then \`N/G\` is a **logical cardinality** reduction, not wall speed. Amdahl gives an optimistic ceiling:

\`speedup <= 1 / ((1-f) + f*(G/N))\`, assuming linear cost and zero new overhead; real gains will be lower.

**Illustration, NOT measured label NDV:** 95,000 input series/day, 38 stage labels, hypothetical 10 job labels and 14 eval steps; with branch=All a second cube grouped solely by (stage,job) has at most 38*10*14=5,320 states against 95,000*14=1,330,000 individual series evaluations, a **250x reduction in logical state count**. It requires all jobs/stages represented for 5,320 rows; if fewer groups, G is smaller. With 200 distinct branch values and filters retaining branch, the upper group bound rises to 38*10*200*14=1,064,000 and potential reduction can shrink to just **1.25x**. Group density and active-series counts must be measured, not inferred from total labels. An instant 5m selector may have far fewer active values than total stored series.

| Reducible fraction f (illustrative) | Optimistic speed ceiling with N/G=250 |
|---:|---:|
| 70% | 3.30x |
| 80% | 4.92x |
| 90% | 9.65x |
| 95% | 18.59x |
| 99% | 71.63x |

For Q repeated eligible reads, native cost \`Q*R_native\`; derived cost \`C_build+C_update+Q*R_rollup\`. The derived path is only cheaper when \`R_native>R_rollup\` and \`Q>(C_build+C_update)/(R_native-R_rollup)\`. There are no measured values for those costs yet. Write/merge amplification, storage, invalidation, replay and failed snapshot recovery must be included. Never use logical input shrink as evidence of a 250x user-visible win.

## Snapshot architecture and safety invariants

- An immutable, fully written version is published atomically with its provenance and the input snapshot hash/revision; a query is admitted only when that revision is still valid for the entire requested grid. A string supplied by the client is **not** sufficient verification: trusted storage metadata or an immutable sealed-part contract is needed.
- For active data, late writes, deletes, corrections or deduplications must invalidate/rebuild affected evaluation windows before serving the rollup. R11 proved optional generic QueryResultCache can return stale data after INSERT [#41619761382](https://github.com/moleus/ClickHouse/actions/runs/41619761382). This proposal is **not** a way to bypass that freshness requirement.
- Every allowed filter label must be retained in the group key or proven constant. A branch=All summary can be a second cube grouped by (stage,job); a branch-filtered query requires branch retention. Arbitrary build filters require original execution unless build is also preserved, which may erase any cardinality gain.
- The original native TimeSeries table remains authoritative. The derived table is an optional fast path only for a static admitted query family; no production write or schema migration is proposed.
- Reject if computed states cannot match current native source revision, error tolerance, timing/staleness or operator semantics. Distinguish no-data from numeric zero.

## First falsifiers, before any CI benchmark

1. Run existing **real panel query string** through a parser and classify its exact AST: \`avg by(stage)\` of an instant vector versus \`avg by(stage)(avg_over_time(...))\` versus another expression. Verify exact \`lookback_delta\`, step alignment, regex expansion, offsets and staleness.
2. On frozen synthetic/corpus evidence, measure per-evaluation active series \`N(t)\` and distinct allowed-label group counts \`G(t)\`. If \`N/G\` is near one, stop. For branch=All compute (stage,job); for literal branch filters compute (stage,job,branch).
3. Source-pinned stock O1 with existing fixture: exact native nonempty label/time/value oracle, snapshot builder proof, secure source-revision check, 1/3/7/14/40d, branch/job filters, late and duplicate samples, updated samples and time-grid mismatch.
4. Only if all correctness gates pass: matched same-binary ABBA with a real derived table using the actual query shape, CPU seconds, p50/p95/p99, read bytes, peak RSS, storage bytes, ingest/merge CPU, snapshot build cost and query count break-even. Preserve negative evidence. Do not build new C++.

## Executable oracle and checks

- [Oracle](r14_rollup_oracle.py), [9-unit-test suite](test_r14_rollup_oracle.py).
- Python 3 local run: \`python3 -m unittest discover -s .github/ts-lab/experiments -p 'test_r14_rollup_oracle.py' -v\` — 9/9 PASS. 64 additional seeded filtered/time/mode combinations are exercised inside one randomized test (4 modes * 4 times * 4 literal filter choices).
- The initial hand-written instant expected mean was **incorrect** (three live compile series yielded (3+9+5)/3, not 4.0); the test detected the oracle/test expectation mismatch. Corrected the expected value and reran. Keep this as a negative method note.
- [Path-scoped minimal GitHub CI](../../workflows/ts-lab-r14-rollup-math-20261103.yml) exists with 5-minute bound, sparse checkout and no C++ compiler. Its live run status must be checked separately; local PASS alone does not imply GitHub PASS.

**Decision now:** pursue the exact AST and cardinality gate; **do not claim or pursue native speedup yet**. If the real dashboard query cannot be admitted or subgroup counts are too high, reject R14 for that use case without a new build.

## Source-only check: do not reimplement existing native AVG fusion

The pinned C++ source already has a **native streaming aggregation stage**:
[applyOneArgumentAggregationOperator.cpp](https://github.com/moleus/ClickHouse/blob/52529f0c711566d696917678bf15e7068772fdde/src/Storages/TimeSeries/PrometheusQueryToSQL/applyOneArgumentAggregationOperator.cpp)
maps PromQL \`avg\` to SQL \`avgForEach(values)\` and emits \`GROUP BY new_group\`. This means R14 **cannot** claim that ClickHouse naively computes cross-series averages in application code or lacks efficient per-step grouping. Do not propose a new GROUP BY/AVG implementation as a supposed radical optimization: it already exists.

[fromSelector.cpp](https://github.com/moleus/ClickHouse/blob/52529f0c711566d696917678bf15e7068772fdde/src/Storages/TimeSeries/PrometheusQueryToSQL/fromSelector.cpp) converts instant selectors via \`last_over_time\` and transforms stale markers into absence; range selectors drop dedicated stale markers but retain ordinary NaNs. This supports the **strict finite-float and static-grid scope** above and explains why an unguarded numeric rollup is unsafe.

The only plausible **large** R14 gain is **persisting and reusing** already computed per-grid group states for repeated, revision-equivalent requests, thereby skipping selector/TAGS/TimeRanges/SAMPLES/aggregation on the subsequent reads. Adding an \`avgForEach\` in the same query would mostly duplicate the current work. CPU and storage gain must include actual repetition frequency and freshness cost.

## Independent CI verification

[Minimal GitHub Actions run #41622064271](https://github.com/moleus/ClickHouse/actions/runs/41622064271) finished **success**. Inspected decoded job logs: Python 3.12.3, all **9/9** named unittest cases OK (including seeded parity), sparse checkout and provenance SHA256 step succeeded. This verifies the mathematical oracle only. No native performance data.
