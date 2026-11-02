# Predict the full-query effect of stage-only tag registration before seeing paired results

Recorded 2026-11-02, before run 41483980771 finishes. All code under the lab branch; no production changes.

## Source and fixed data
Pinned upstream base 52529f0c711566d696917678bf15e7068772fdde. In `StorageTimeSeriesSelector.cpp`, `makeSelectQueryFromTagsTable` invokes stateful `timeSeriesStoreTags(id,tags,...)` after all tag-match and TimeRanges filtering. `ContextTimeSeriesTagsCollector.cpp` then stores a group per distinct full tag set and an ID -> group map. In this fixture, unique `build` gives S = 1,330,000 full groups; stage-only projection reduces unique group keys to G=38, keeping S ID map entries. The patch uses an internal return-column discriminator `samples_lab_stage` only under explicit `promql_enable_native_one_point_lab`.

Observed isolated registration 41476956981, median over three cold-restart repetitions:
- Full: 1,247.612 ms, peak process 1,059,616 KiB, 1,330,000 groups.
- Stage-only: 370.949 ms, peak process 486,192 KiB, 38 groups.
- Difference 876.663 ms and 573,424 KiB. Per-ID empirical cost difference ~0.659 microseconds and ~441 bytes (memory includes process and allocator overhead; not an intrinsic type size). These measurements are **not** the full PromQL plan.

Full v4 14-day broad query cold-process run 41475145589: 2,996.664 ms wall, 1,355.7 MiB sampled RSS, 923.5 MiB tracked peak; full original 4,712.209 ms /1,803.1 MiB. VM 1.134.0 warm p50~3,170.922 ms in separate high-card diagnostic 41470644132. Run-level CPU p50 and cache state differ. On the four-thread v4 pipeline, `ExpressionTransform` aggregated active elapsed ~4.265 seconds, while `MergeJoinTransform` ~0.684 seconds, `AdaptiveBucketMergingSource` ~0.895 seconds; processor activity can overlap and does not sum to wall time. The largest remaining expression work is a plausible tag-processing target.

## Cost hypothesis and falsification
At fixed selected IDs S, processed sample cells M, grid points E, and same read_rows, the projection changes the tag-group registry:
`C_full = C_scan + C_filter + C_timeRange + C_ID_map(S) + C_fullTags(S,L) + C_grid(M,E) + C_group + C_fixed`.
`C_projected = C_scan + C_filter + C_timeRange + C_ID_map(S) + C_stageTags(S,G) + C_grid(M,E) + C_group + C_fixed`.
Expected benefit is from `C_fullTags - C_stageTags`, not from fewer ID postings or sample rows. The isolated 876.663 ms savings is a **directional estimate only**, not a proven subtractive bound on full-query wall time due overlapping workers, caches, different expression arrangements and prefetch. Do not claim the patched full query beats VM before verifying.

Paired run 41483980771, using artifacts from 41447045583 and 41477923294 with the same SHA256 dataset and four test variants (original/projected × off/on), is the decisive test. Predeclared requirements:
1. VM must return all 14 days × exactly 95,000 samples/day and stable per-selector output point counts; no zero/partial index reads.
2. Same labels/timestamps/value tolerance versus VM in every case before interpreting speed.
3. Relative CPU/RSS effects separated from OOM/cache drift. Exact stage/regex/project, 3-day and 14-day cases, not just broad queries.
4. Default flag OFF must preserve original public behavior. A failed query or OOM is a failed comparison, never a speedup.

## Semantics and security limitations
The projection is safe for *this guarded one-group-label output only if* no later operation needs the discarded labels; original WHERE matchers must execute before tag registration; missing stage must not become stage=""; and a duplicate ID with conflicting full labels must not silently change error behavior. The C++ patch exposes an explicit opt-in public table-function return token; this is not a production-grade internal authorization boundary. Physical SAMPLES SELECT can bypass inner TAGS policies when granted (41458721287); the new plan needs an independent tenant authorization test and least-privilege model. Dense/multi-sample, rate, NaN/Inf and subquery workloads remain separate gates.

Status: pending paired benchmark, not approved for production.
