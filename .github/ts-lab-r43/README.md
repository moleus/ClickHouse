# R43 — verified native compiler plus R34 sparse bucket-map grower

**Status: source-matched optimized build and 160-case native semantic gate PASS; full 1.33m same-host A/B/B/A is in progress. No performance gain is claimed until controls complete.**

## Composition

R40 contains the verified native range-function compiler changes R35 + R37 + R38. R43 retains these source-verified changes and **adds only R34's initial TimeSeries hash-map capacity change** in `AggregateFunctionTimeseriesBase.h`. R34 uses 2 initial cells with one empty slot and 3/4 occupancy for 4+ cells, in place of 16 initial cells. No data layout, tag collector, series selector or PromQL semantics change.

- [Canonical root](../ts-lab/claims/roots/R43.json), [primary claim](../ts-lab/claims/R43-cumulative-small-bucket-map-v1.json).
- [R34 exact source-pinned helper](../ts-lab-r34/apply_sparse_map.py), [R40 cumulative compiler helper](../ts-lab-r40/apply_cumulative.py).
- [R43 LLVM22.1.8 -O2 jemalloc optimized build 41804461799](https://github.com/moleus/ClickHouse/actions/runs/41804461799), executable SHA256 `5bac2b7722bc43b5a48cafbfeff2598997b0b38ac244ff977245986a3bf05c72`.
- [R43 native 160-case semantic/plan gate 41805684591](https://github.com/moleus/ClickHouse/actions/runs/41805684591) PASS against R40, including root selectors and aggregations, regex, offsets, mixed names, NaN/Inf/stale controls.
- [Exact R43 build workflow](../workflows/ts-lab-r43-cumulative-smallbucket-o2-20261104.yml), [native guard suite](guard_suite.py).

## Full native acceptance

Same frozen RAW_UUID source: 1,330,000 series over 14d, 2500 builds/day × 38 stages, 14 daily steps. Check four native queries through `prometheusQueryRange`:

```promql
avg by(stage)(avg_over_time(ci_duration[1d]))
avg without(build,project,branch,job)(avg_over_time(ci_duration[1d]))
avg by(stage)(avg_over_time(ci_duration{stage="stage-1"}[1d]))
avg by(stage)(avg_over_time(ci_duration{project="project-1"}[1d]))
```

Run R40/R43/R43/R40 with a fresh ClickHouse process per phase, 2 warmups+4 measured native requests per query, 4CPU/4GiB cgroup, 2.85GB server memory, max_threads=2; identical physical part census. Verify complete output against an independent fixture-derived oracle for **every request**, including all tags, timestamps, values and errors. Record QueryFinish wall, CPU, memory, reads and sampled request RSS; separate absolute changes and baseline drift. Dense 8192×256 counters must retain correct rate output and not regress significantly. Never multiply R34 isolated aggregate-state gain with R35/R38 independently.

R43 is not production-ready until mixed reads/writes, merge, retention, tenant access and distributed query gates are passed. Master, upstream and production are unchanged.
