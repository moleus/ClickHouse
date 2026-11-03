# Eight-phase glibc allocator frontier — independent artifact audit

Date: 2026-11-03. Study owner: existing CBO allocator campaign; **read-only analysis of its completed artifact**, no new run or build. [CI #41607288907](https://github.com/moleus/ClickHouse/actions/runs/41607288907), artifact ID `13217618469`. Same one GitHub runner; eight fresh ClickHouse server processes and O1 source SHA `52529f0c711566d696917678bf15e7068772fdde`. Original Jenkins fixture SHA256 `8f3a36846f80d6be5a8604d142e934399f2687094766c57bf105e43977f52858` (1.33m series).

## Exact execution and scientific success

Expected sequence: default, arena4, arena8, trim, trim, arena8, arena4, default. A successful *phase* contains twelve 7-day full native PromQL queries plus one 14-day probe; each 7-day query yields 266 nonempty points, each successful 14-day probe 532 points.

| Phase | glibc mode | Successful 7-day queries | 14-day probe | RSS after twelve 7-day, MiB |
|---|---|---:|---|---:|
| 0 | default | **9/12**, tenth hit Code 241 OOM | Not attempted | Not available |
| 1 | MALLOC_ARENA_MAX=4 | 12/12 | PASS | 1765.5 |
| 2 | MALLOC_ARENA_MAX=8 | 12/12 | PASS | 1773.4 |
| 3 | MALLOC_TRIM_THRESHOLD_=131072 | 12/12 | PASS | 2294.5 |
| 4 | trim | 12/12 | PASS | 1924.5 |
| 5 | arena8 | 12/12 | PASS | 2744.8 |
| 6 | arena4 | 12/12 | PASS | 1894.6 |
| 7 | default | 12/12 | **Code 241 OOM** | 4157.7 |

All recorded **101** query IDs are unique and phase-qualified. There are 101 rather than 104 records because phase0 stops after the first OOM. Both failures are actual ClickHouse memory-limit errors close to 4.66 GiB, not GitHub CI infrastructure errors. The eight-phase GitHub workflow is SUCCESS because it collected expected diagnostic failures; that does not mean all 104 queries passed.

**Important reducer error:** [`reduce_glibc_frontier.py`](reduce_glibc_frontier.py) currently requires `len(steps)==13` for **every** phase and exactly 104 unique query IDs. It therefore rejects the validly observed early-default-OOM path (phase0 contains 10 entries). A safe reader must classify the phase as *expected right-censored OOM*, retain its exact failure index, verify there are no subsequent requests, and exclude it from full-phase performance medians. It must not impute three omitted requests or fabricate a matching default A/A pair.

## Candidate-to-candidate comparisons — same runner, two phases per mode

For each **fully completed** phase, calculate the median `system.query_log.query_duration_ms` and `ProfileEvents.OSCPUVirtualTimeMicroseconds` for 7-day queries 3–12 (exclude first two warmups), and extract `VmRSS` after query 12. Then average the two phase medians for each mode. The drift column is the difference between the two paired phase values divided by their mean.

| Mode | 7d wall median, averaged across phases | 7d CPU/query, averaged | RSS after 12 ×7d, averaged | Pair drift: wall / CPU / RSS | 14d PASS |
|---|---:|---:|---:|---|---:|
| arena4 | 5,500 ms | 11,439 ms | 1,830 MiB | 6.85% / 7.75% / 7.05% | **2/2** |
| arena8 | 5,745 ms | 11,019 ms | 2,259 MiB | 9.30% / 1.18% / **43.0%** | **2/2** |
| trim | 5,467 ms | 10,662 ms | 2,110 MiB | 5.98% / 0.85% / 17.54% | **2/2** |

This table is **exploratory**: modes were run in a symmetric sequence on the same host, but there is no valid full default/default performance control because phase0 aborted. Do **not** interpret candidate-vs-default latency or CPU percentages as causal. The candidates still have useful paired cross-phase evidence: all six candidate phases survive the full 14-day query, whereas default fails at least once under both seven- and fourteen-day stress.

With wall/CPU/retained RSS as three minimization dimensions, phase-average candidates `arena4` and `trim` are the conditional Pareto frontier; `trim` is lower than `arena8` on all three phase-mean dimensions. However `arena8` RSS control drift is **43%**, so its average RSS cannot be treated as a stable estimate. `arena4` has the lowest, most reproducible RSS but higher CPU than trim. Neither is proved optimal for the intended jemalloc production allocator.

Additional caveat: arena8 phase2 had ~212 ClickHouse threads after queries, while phase5 had ~416, and its RSS was ~971 MiB greater. That may contribute to the RSS difference; this source does not establish why thread counts diverged. `MALLOC_ARENA_MAX` does not directly constrain application thread count. No claim of mutex contention, leak, or allocator root cause follows from that observation alone.

## Next actions without duplicating CBO work

1. Correct/revise the frontier reducer to accept a **terminal expected OOM** as censored diagnostic evidence, preserve all recorded IDs, check exact `Code 241` and absence of subsequent requests, and mark default comparative performance **INCONCLUSIVE**.
2. Keep arena4/trim as candidates for allocator-specific lab mitigation, **not** global ClickHouse settings. Validate against an explicitly verified `USE_JEMALLOC` build and identical 1.33m native PromQL fixture when existing [jemalloc build #41607779848](https://github.com/moleus/ClickHouse/actions/runs/41607779848) finishes. Do not cancel or duplicate that build.
3. Repeat under fixed thread pool size only if enough evidence shows the arena8 RSS variance matters for production. Include post-query allocator retained vs live pages and p95/p99 under concurrent clients.

Existing [glibc allocation study](GLIBC_MEMORY_EVIDENCE_20261103.md) remains authoritative for prior 4-arm and paired A/B/B/A trials. This audit neither modifies its results nor changes production or master.
