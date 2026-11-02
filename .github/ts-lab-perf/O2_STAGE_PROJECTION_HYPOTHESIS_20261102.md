# O2 compiled projected-stage native collector: proof-first comparison

2026-11-02 fork lab, pinned ClickHouse source 52529f0c711566d696917678bf15e7068772fdde. No upstream/production change. Opt-in new behavior only.

## Existing hard evidence
- Fresh-process O1 v4 full-tags vs O1 stage-only query 41486787371: whole14 2868.11ms to 1589.869ms, ~1.80x, reduced RSS 1.32GiB to .81GiB, strict VM parity. Exact stage stage-only only ~5% faster, VM still six times faster.
- Fresh-process same patch blobs (fastpath, selector, KBN) O1 vs O2 in 41498812663: whole14 O1 6063.116ms vs O2 3247.697ms; exact stage 1108.656 vs537.314ms; O2 effect ~1.87-2.16x across selectors. Different CI fixture part layouts make absolute O1 times from different runs incomparable.
- O2 allocator comparison 41499622283: jemalloc gives 0-6% wall gains but 6-15% more process peak RSS in this fixture. Candidate O2 stage-projected compile uses normal system allocator first.
- Native UInt64 ID registry repeats 41499234636 passed 60/60. Smaller input block size8192 had lower wall time and memory than65536 at 1-100% new IDs, but these are synthetic native SQL component measurements, not semantic broad PromQL.

## Mathematical claim and falsifier

Let M=metadata and sample reads, T=tag registration/lookup memory state, A=per-ID and group arithmetic, H=allocator/cache overhead. Whole-query time is critical_path(M,T,A,H), not the sum of inclusive processor times. The O1 speedup with projection and O2 speedup from compiler optimization are NOT multiplicative by mathematical necessity. Compiler O2 can change which stage is dominant, interaction with allocator, vectorized execution and cache locality. Regression is possible.

H1: O2 + stage projection preserves full VM numerical output and reduces 1.33m churned one-sample whole14 wall time and process RSS by >=10% relative to same O2 full-tags variant in fresh-process A/B/B/A on identical immutable physical data. H0: effect <= noise or correctness difference.

Mandatory gate:
1. Confirm same ClickHouse source SHA, same three C++ patches plus one stage-projection patch, same LLVM22.1.8 and O2 flags. Preserve git diff and binary SHA; no ccache miss interpreted as speed.
2. A/B/B/A each query fresh server, same dataset, 4 threads, same 5GB/4GB limits and VM1.134.0, two VM readiness probes, exact labels/timestamps and compensated Float64 output.
3. Controls: whole14, exact stage, project and regex; confidence needs more than four samples for production. Report wall, CPU, query tracked peak, process RSS, read_rows, part count, merges and disk free before/after.
4. Do not enable broad production path until dense multi-sample timeseries, stale/NaN/Inf, duplicates/late arrivals, missing/empty stage and direct inner-table ACL tests are proven. Known dense regression of v4 and potential ACL bypass remain open.
5. If O2 projected is no faster than O2 full-tags on whole14, retain raw failure and stop its promotion. Do not optimize next microscopic leaf without showing CPU critical path and mark/index profile.

Build is one-off with restored ccache from completed O2 no-jemalloc compile (41467151457); -O2 and otherwise identical settings, no tests/linters and no cron. Preserve any long running builds, do not cancel in-progress actions.
