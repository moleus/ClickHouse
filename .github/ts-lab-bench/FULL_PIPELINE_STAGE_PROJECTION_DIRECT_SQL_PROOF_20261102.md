# Full-query stage projection without C++ build: controlled direct-inner laboratory test

2026-11-02. Previous 1.33m series collector run 41476956981: full label registration 1,248ms and 1,035MiB process peak; stage-only 371ms and 475MiB, same 1.33m ID inputs. This proves registration overhead only, not full PromQL.

## Hypothesis
For the one-day avg by(stage) query already recognized by v4, substituting stage-only tag registration into the *otherwise unchanged* per-series `timeSeriesAvgToGrid` SQL query will cut latency and memory. Preserve exact full label matchers on TAGS, the TimeRanges LEFT JOIN, sample time conditions, the ID set, per-ID grouping and numeric aggregation.

## Exact comparison
Run same 1.33m-source fixture SHA `8f3a36846f80d6be5a8604d142e934399f2687094766c57bf105e43977f52858` and pinned VM v1.134.0 in same runner. For E3/E14 on whole metric and exact stage: (A) native `prometheusQueryRange` with opt-in v4; (B) manually reconstructed SQL using original full tags registration; (C) same SQL with only stage registration; (D) VM. Require exact label/timestamp set and values within tight tolerance against A and D. Record query log read_rows, CPU, memory, latency with randomized order. Restart server before each first variant to remove retained RSS confound.

## Limits and reject rules
Direct inner SQL is NOT a valid production API and does not prove native selector access control. If direct query's plan/semantics differ from the native path, label results diagnostic only. If the stage projection loses a label that is needed for output or final aggregation, reject it. Keep build in fork; do not touch upstream.
