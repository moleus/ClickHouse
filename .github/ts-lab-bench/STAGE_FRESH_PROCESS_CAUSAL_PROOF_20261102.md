# Fresh-process falsification of stage-only registration at 1.33m series

2026-11-02, fork laboratory only. Source `52529f0c711566d696917678bf15e7068772fdde`.

## Observed before proposing the experiment

Run 41483980771, immutable SHA-256 fixture `8f3a36846f80d6be5a8604d142e934399f2687094766c57bf105e43977f52858`, 14 days * 95,000 fresh series/day, 38 stages, fixed 5,000,000,000-byte server cap. VM successfully passed the new two-consecutive-full-14d readiness checks. Four opt-in/off variants were checked against VM; the two broad original-off variants hit real ClickHouse memory errors, not VM errors. The opt-in original and stage-projected C++ variants produced equal VM outputs on all six controls.

For the whole-14d opt-in run the five **sequential in-process** query times were:
- original full registry: 5214.986, 6077.234, 14533.470, 14512.871, 13801.046 ms
- projected stage-only registry: 4324.251, 5156.258, 5178.650, 4778.765, 4662.575 ms.

The published 14156.959 vs 4967.512 ms median, 2.85x speedup, is real for *that sequential test*, but cannot yet be attributed exclusively to cheaper per-query tag registration: the original becomes ~2.7x slower after repeated queries and RSS accumulates across trials. First trials differ by only 17.1% (890.7 ms). Median observed RSS p95 3090144.8 versus 1511263.2 KiB is a *process* statistic, not per-query allocation.

Also `system.query_log.read_rows=3,990,000` for all 14d controls, including exact stage and project filters. A query scans multiple physical inner tables, therefore this counter is not itself proof of absent index selectivity or absent predicate pushdown.

## Semantic proof for projected registry (conditional)

Native fastpath is gated to the exact `avg by(stage)(avg_over_time(metric{matchers}[1d]))` AST, disjoint daily windows, verified v11 Float64/UTC types and an explicit default-OFF laboratory setting. All full-label matchers must be evaluated on the TAGS table **before** registering a subset of tags. If the only output label required by the subsequent `timeSeriesIdToGroup` and `timeSeriesGroupToTags` is `stage`, and the same per-ID samples and arithmetic are used, then replacing the collector's full label set by the stage label only does not change the output. This proof does NOT apply if any operation later reads a discarded label, if projection changes the set of selected IDs, or if it collapses absent stage and empty stage. Test these independently; no production promotion.

## Cost hypothesis and counterexample

Let `Q = R + T + A + H`: R is time-range and sample/index reads, T is tag registration, A is arithmetic/grouping and H is query-dependent allocator/cache overhead. The standalone same-table experiment 41476956981 measured T_full=1247.612ms and T_stage=370.949ms, a ~876.7ms saving. Thus a cold-query latency saving on the order of ~0.9s, rather than the full 9.19s observed between warmed medians, is plausible; it is not guaranteed because T overlaps with other operators and memory effects are nonlinear. Absolute savings can be zero for one-stage narrow matchers, or negative if projection adds overhead. No unconditional fastpath follows.

Null hypothesis H0: after a fresh ClickHouse process before **every** query, the projected variant's latency advantage on `whole_e14` vanishes or is small compared with timing variation, and its large sequential-test advantage was mostly allocator/cache state.

Alternative H1: fresh-process A/B/B/A runs preserve a reproducible positive latency and RSS advantage; then report observed fraction and compare its size to standalone T independently. Do not infer causation from a single ratio.

## Predeclared test and acceptance

- Reuse SHA-verified artifacts original v4 run 41447045583 and stage-projected run 41477923294, no recompilation.
- Re-import exactly the same fixture and VM 1.134.0 with the same readiness gate; never treat VM HTTP import 204 as query-ready.
- Each measured ClickHouse query starts a new process on the **same on-disk ClickHouse data**, with 5GB process and 4GB query limits, 4 threads. Alternate A/B/B/A for four trials per case, using `whole_e14`, `stage_e14`, and `regex_e14`; VM executes two warmups and four timings per case.
- Assert every output key, timestamp and Float64 result against VM within the existing explicit tolerance. Keep response errors and OOM in evidence; do not count failed query as a fast result.
- Record process RSS at start, process peak, process RSS after the query, CPU from query_log, tracked query memory, read_rows and timings; flush query_log **before** restarting.
- Present per-run timings and median, not just a single speedup. Treat observed memory differences as process-level, not user-query allocations. VM process RSS is contextual only because its lifetime, ingestion and allocator are different.
- **Acceptance for additional research only**: all correctness checks pass, no OOM, no reversal in fresh-process measurements, and at least 10% whole_e14 median improvement with smaller process RSS. Even acceptance does not prove dense-series safety, other CPUs, O2 build performance, multi-tenancy or general PromQL correctness.
- On failure, retain raw data and the failure; neither edit production nor generalize projected collector to the public selector.

## Independent next bottleneck

For exact stage only, CH in 41483980771 was ~1224ms versus VM 76.4ms; for 3d stage ~476ms versus VM 13.9ms. All corresponding 14d queries logged 3,990,000 read_rows regardless of label filter, while 3d logged 3,032,000. Hypothesis: this is primarily metadata TAGS/TimeRanges reads and sample primary-key granules, not stage aggregation. Need a separate `EXPLAIN indexes=1`, per-inner-table read and processor-profile experiment, with correct physical table definitions and access controls. Do NOT propose a blind new tag index until the current index and merge-join behavior are measured.

Runs 41476488794 (O2 with jemalloc) and 41467151457 (O2 without jemalloc) must remain undisturbed. All changes fork lab branch only. Scheduled fork workflows previously removed from fork master.
