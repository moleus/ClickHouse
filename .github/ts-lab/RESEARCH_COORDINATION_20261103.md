# TimeSeries research coordination and next experiments — 2026-11-03

Status: research-only. Repository: `moleus/ClickHouse`. Single write branch: `lab/ts-cbo-adaptive-20261103`. Do not modify `master`, production, or upstream. Do not cancel live jobs, rebase, amend, or force-push. This is a decision and coordination protocol, not evidence that any proposal works.

Read in order: [INDEX](INDEX.md), [AGENT_GUIDE](AGENT_GUIDE.md), [METHODOLOGY_AND_FAILURES](METHODOLOGY_AND_FAILURES.md), [OPTIMIZATION_STACK](OPTIMIZATION_STACK.md), then this document. Refresh the shared branch and the complete GitHub Actions in-progress and queued lists before every costly experiment. Existing agents might still be running from older branch checkouts; this file cannot change their state automatically.

## 1. Problem with the previous workflow

The lab has a shared history and good A/B/B/A rules, but a report is not a work queue. Several agents can select the same next question, run different baselines, consume runners, and publish incompatible summaries. The repository has roughly 300 workflow files, many built for one experiment. Workflows are executable history, not an experiment registry. Results have several local checkpoint formats. An agent can miss a negative result and rerun a rejected idea.

Corrective action: use **one canonical question ID, an atomic create-only claim, one immutable evidence record per run, and a promotion decision**. The index links outcomes; it is not the lock. Do not create a new build merely because a different branch or agent has not yet answered a message.

## 2. Evidence that must guide the next decisions

| Established or rejected | Evidence and consequence |
|---|---|
| High-churn v11 ID type | [Stock O1, four native A/B/B/A comparisons](https://github.com/moleus/ClickHouse/actions/runs/41596921882): explicit `Tuple(UInt64,UUID)` outperforms the default LC UUID by 1.14-1.43x on the 1.33m high-churn fixture. Keep as opt-in schema result; do not repeat it as a new candidate or call it a universal win. Dense/sparse counterexamples exist. |
| Collector prehash / lock | [Exact lock counters](https://github.com/moleus/ClickHouse/actions/runs/41596955580) reduced some wait and hold measurements but increased end-to-end latency on the paired project workload. Reject this implementation, not the broader allocation question. |
| TimeRanges removal | [Raw-ID ON/OFF factorial](https://github.com/moleus/ClickHouse/actions/runs/41601352971): beneficial for some selective long windows; much slower or OOM for broad windows. No global removal. Query-time choice requires a memory bound and a fresh comparison. |
| Projection/order | [Native factorial](https://github.com/moleus/ClickHouse/actions/runs/41589798782) showed fewer rows with a time projection but extra sorting and slower native PromQL. Optimizer estimates must include downstream ordering. |
| Parallelism | [Raw-ID scheduler sweep](https://github.com/moleus/ClickHouse/actions/runs/41602036007): four to eight query threads gave no robust latency gain on stage/project; two threads can be more CPU-efficient on some broad cases. This is not concurrent-client throughput evidence. |
| RSS accumulation | [12 consecutive native queries](https://github.com/moleus/ClickHouse/actions/runs/41589590350) raised server RSS despite smaller per-query tracked peaks, ending in broad OOM. Does not yet distinguish allocator retention from live objects/caches. |
| Invalid methods | [Failure ledger](METHODOLOGY_AND_FAILURES.md): O1-vs-O2 comparison, observer-polluted `/proc` sampling, unflushed QueryFinish, cross-runner speed claims, missing profiler leaves, and AGENTS symlink replacement. Do not rerun invalid methods without fixing the cause. |

At this snapshot, the following existing CI runs already cover the glibc allocator question: [2x2 fresh-process](https://github.com/moleus/ClickHouse/actions/runs/41604969692), [same-runner trim A/B/B/A](https://github.com/moleus/ClickHouse/actions/runs/41605644182), [same-runner arena A/B/B/A](https://github.com/moleus/ClickHouse/actions/runs/41605807154). The [LC decoder build](https://github.com/moleus/ClickHouse/actions/runs/41591617794) is also in progress on an older branch. These status statements refer to the 2026-11-03 review and must be refreshed. **Do not launch a fourth allocator experiment, a duplicate UUID comparison, or a second LC decoder build before inspecting the existing outputs.**

## 3. Prioritized new questions (research hypotheses, not measured improvements)

Use these IDs to prevent multiple agents from working on the same hypothesis. A variant-specific claim can have a suffix such as `R02-K8S-C1`.

| ID / priority | Research question and source of uncertainty | Decisive next test and required result |
|---|---|---|
| R01 / P0 | Does the winner survive the real-shaped Kubernetes workload? Existing 95k/day Jenkins churn and 10k dense synthetic series do not model 400 nodes, about three-hour node lifetimes, pod/namespace/instance churn, scrape intervals, or high/low-cardinality mixed selectors together. | Build one deterministic synthetic workload from declared distributions, no private data. Report distinct IDs/part, samples/ID, part sizes, query matcher selectivity, 15s/60s cadence, stable/churn mix. Reuse stock and raw-ID binaries. Measure `query_range` for 1/3/7/14d, exact/regex/broad, plus 1/4/8 clients. |
| R02 / P0 | What is the **steady-state** cost of RAW_ID and each physical layout? Most reported gains are read tests after bulk load; the insertion sink writes TAGS, TimeRanges, SAMPLES and optional recent samples, followed by merges. | For one controlled workload, run continuous small remote-write batches + reads for long enough to include actual merges. Record ingest samples/s, CPU/s, allocated bytes/s, disk bytes/sample, merge CPU/bytes, parts, WAL/temporary bytes if present, p95/p99 query latency and server RSS. Keep a read-only control and initial/final data hashes. |
| R03 / P0 | Is native end-to-end ClickHouse competitive with VictoriaMetrics under **concurrent** selectors and a finite memory budget? One-query `max_threads` sweeps do not answer QPS or tenant interference. | Same fixture, query set, timestamps and numeric oracle, pinned VM and ClickHouse binaries, same host quota and cold/warm cache conditions. Compare p50/p95/p99, completed QPS, CPU-second/query, server peak RSS, bytes read and errors at 1/4/8 concurrent clients. Check the existing `bench_native_multiclient_stock_20261103.py` before creating another runner. |
| R04 / P1 | Can the native recent-samples target reduce hot-window reads without unacceptable double writes, TTL boundary errors or merge cost? `StorageTimeSeriesSelector::readImpl` chooses RecentSamples when the full window is within TTL; `TimeSeriesSink::consumeTagsAndSamples` writes main before recent. The existing canonical lab often disables recent-samples TTL. | One native ON/OFF factorial with identical stream. Window fully inside, straddling and outside TTL; fresh/expired/late samples; process restart; exact output parity against main-table path; read CPU, RSS, write/merge amplification, compressed bytes and storage growth. |
| R05 / P1 | Can the selector run candidate-TAGS first, then use sparse time/ID lookups without a full generic metadata JOIN or large materialized ID set? Existing TAGS direct search is much cheaper than end-to-end stage selection; earlier direct SQL rewrites and fastpaths are not evidence of a safe native replacement. | With RAW_ID fixed, instrument candidate count, TimeRanges right-side rows/parts, ID-set allocation, output order, SAMPLES selected marks, per-operator CPU. First prove equivalent SQL on stage/project/branch/whole and 1/3/7/14d; test a cheap sorted-ID or batched-lookup oracle. Only patch C++ if wall and memory upper bounds make a significant win plausible. |
| R06 / P1 | Can the planner select time pruning and projection using **estimated selectivity, time coverage, read order and peak memory**? TimeRanges ON/OFF and time-projection results cross over by workload. | Build a cost table from existing valid runs, then a held-out A/B/B/A matrix. Features: candidate IDs, active fraction, query range, part count, required join order, estimate of sort/hash bytes. Require native correctness, no broad OOM and a proven improvement outside the training cases. Do not turn off TimeRanges globally. |
| R07 / P1 | Can stable logical IDs coexist with an opt-in label-local physical access path without reducing hash entropy or changing migration semantics? Packed-ID tests improved selective cases but truncated hash128 and had fastpath-dependent broad OOM. | Build a write-preserving sidecar/postings/projection **prototype** and a no-extra-index control; compare stage/project/namespace selectors and whole metric. Require full ID identity, cross-tenant filters, merge/rebuild correctness, storage and write amplification, exact native output. Do not repeat the packed-short-ID success as production proof. |
| R08 / P0 gate | Are all speed paths semantically safe for PromQL, repeated updates and access control? Existing numeric checks focus on specific positive finite fixtures. | Differential oracle cases for empty sets, absent vs empty labels, regex/negative matchers, stale NaN, NaN/Inf, counter reset, range boundaries, duplicate/late/out-of-order samples, cross-part merges, nondeterministic label order, read-only/tenant restrictions and multi-selector expressions. Run this as a quick gate before larger performance builds. |
| R09 / P2 | Is a later upstream master revision already better than the pinned 52529f0 source? Optimizing a fork-only path that upstream has changed can waste a week. | Review source diffs for selector, bucket aggregation, recent samples and tag collector. Run the same artifact-pinned fixture and semantic suite on new source only after a compatible matched build exists; treat revision changes as a separate variable, not as the same-source A/B test. |

P0 means ready or required as a gate. P1 means start after inspecting the active overlaps and cheap proofs. P2 means preserve for a later campaign. S3/HA/multi-region come after single-node read, ingest, memory and correctness meet a target; do not spend the current CI budget on them.

## 4. Atomic claims: one hypothesis, no parallel duplicates

Do not use the mutable index or a variant suffix as a task lock. **Before any new question variant, reserve the canonical root question ID** by creating `.github/ts-lab/claims/roots/RNN.json` using a create-only GitHub operation. It records `question_id`, `primary_claim_id`, `owner`, `scope`, and `allowed_support_claims`. If root RNN exists, do not invent a new suffix to get around it. The already-existing R01 fixture claim and mixed-cadence benchmark claim were separately created by two agents; R01's root now gives the mixed-cadence experiment ownership and permits the small lifecycle fixture as a noncompeting support test. The CI contract validates root ownership and rejects unregistered duplicate claims.

Once the root reservation succeeds, create a new file on the shared branch:

`.github/ts-lab/claims/R01-k8s-mixed-c1.json`

Use a **create-file operation** (not update) with a deterministic path based on the question, fixture family and intervention. GitHub rejects creation if the path already exists. If it exists, read the active owner's planned work and perform another independent task or add an evidence review. Do not overwrite or remove the claim.

Minimum JSON claim fields:

`{
  "id": "R01-k8s-mixed-c1",
  "question": "Will RAW_ID retain its gain with three-hour Kubernetes node churn?",
  "owner": "agent-or-session-label",
  "state": "claimed",
  "source_sha": "40-character pinned Git commit",
  "baseline": "stock, binary SHA and build workflow URL",
  "candidate": "RAW_ID; no C++ patch",
  "fixture": "generator commit + seed + fixture SHA",
  "single_variable": "internal UUID encoding",
  "math_bound": "which CPU/bytes term can shrink, which cannot",
  "falsifier": "native parity fails, drift gate fails, or CPU+RSS regress",
  "test_matrix": "selectors, windows, density, clients, cache state",
  "runner_budget": "job minutes and max parallel jobs",
  "planned_workflow": "new unique path; no schedule",
  "dependencies": ["R08"]
}`

The owner label identifies work, not a person. First create the claim; then push a new workflow scoped to its own path. Never turn an abandoned claim into permission to launch duplicate jobs: append an explicit handoff evidence file explaining that no live run remains, then claim a *new variant suffix*. Check GitHub runs, not just the claim status.

## 5. Immutable evidence and decision records

After each run create `.github/ts-lab/evidence/<experiment-id>/<run-id>.json` (or an equally named Markdown file if that is all a connector supports). Do not mutate older records. Put a *brief* link and the decision in [INDEX.md](INDEX.md) using a fresh blob SHA. The primary output is **one machine-readable record** plus raw artifacts, not another long prose checkpoint.

Required contents:

- Question ID, baseline and candidate source SHA, actual executable SHA, LLVM/flags/build options, fixture generator/seed/hash, schema, query text/hash and workload shape.
- URL of the GitHub workflow, job and artifact; GitHub runner CPU/memory and cgroup cap; partition count, active merges and cache state; explicit control and experimental order.
- Native output cardinality, labels, timestamps and value equality; separate NaN/staleness/duplicates/ACL checks.
- p50/p95/p99 wall latency, CPU microseconds/query, server peak and retained RSS, per-query memory, read rows/bytes/marks, ingestion/merge costs when relevant.
- Baseline A/A drift and variance; effect size and direction; profiler overhead measured against a no-probe control.
- Exactly one scientific decision: `ACCEPT_FOR_NEXT_GATE`, `REJECT`, `NO_EFFECT`, `INCONCLUSIVE`, `INVALID_METHOD`, `CORRECTNESS_FAIL`. GitHub job success is *not* a scientific accept decision. Infra failures get a separate `INFRA_FAIL`.
- The explicit stop or promotion condition and **one** next dependent experiment. Preserve negative evidence and the original result; append corrections with references rather than editing history.

Use the existing `campaign_aggregate.py`, `analyze.py`, workflow audit and checkpoint scripts where possible. Build a common result adapter around them instead of adding a new independent benchmark framework for each agent.

## 6. Decision and experiment loop

1. **Refresh**: read shared INDEX, failures, open claims, recent commits and *all* in-progress/queued runs. Search existing scripts/workflows and artifact evidence by the hypothesis ID, not only by experiment name. If a run answers the question, analyze it instead of launching another.
2. **Model**: write a source-backed hypothesis with at least one cost formula or Amdahl upper bound. Example: for uniformly mixed stage selectivity `p` and granule size `g`, a granule-hit fraction `1-(1-p)^g` near 1 means a sample skip index cannot prune much without physical locality. Distinguish CPU-work reduction from wall latency reduction.
3. **Claim**: create the unique claim file. Ensure the runner budget, control artifact, fixture and expected result are pinned. Only **one meaningful variable** per experiment, except a preregistered factorial.
4. **Prove cheaply**: static source proof, SQL/EXPLAIN, property tests, tiny smoke and exact parity. Estimate largest possible wall/CPU gain before a new C++ compile. Reuse an existing verified binary if the question can be answered by SQL, schema or settings.
5. **Measure correctly**: same host matched A/B/B/A with warmups, replicated observations, control drift gate (usually 25%, tighter for small effects), correct native PromQL outputs and memory caps. Cross-runner comparisons are discovery only. No profiler-instrumented latency vs unprofiled control.
6. **Continue independent work while CI runs**: inspect unrelated source-level hypotheses, prepare deterministic fixtures, interpret earlier artifacts, write the next cheap correctness test. Do not launch duplicate runs just to keep agents busy.
7. **Reduce and decide**: classify scientific result and separately CI infrastructure status. Stop ideas with no plausible critical-path gain or unsafe memory/write cost. Keep the successful, negative and invalid reports.
8. **Publish and hand off**: immutable evidence first; update shared index with concise result and next falsifier; other agents read it before starting.

Promotion requires a *Pareto decision*, not a single speedup: compare wall p95/p99, CPU-second/query, process peak RSS, ingest samples/s, merge CPU, compressed bytes/sample, and correctness. No candidate with an unresolved broad-query OOM enters a cumulative stack. Keep original stock and candidate O1/O2 controls separate.

## 7. Runner allocation and reuse

- Default maximum: one expensive compile lane per unique source patch family; one or two independent measurement lanes with already built SHA-verified binaries; cheap document/source analysis is unlimited. Treat this as a planning limit, not a global GitHub quota.
- Query all live runs before allocating a lane. Never cancel or replace a running job or a nearly finished compile. Use workflow/run-unique concurrency with `cancel-in-progress: false` and fail-fast off.
- Cache by source patch hash + toolchain + flags. Never reuse a binary merely because a build workflow succeeded; verify archive and executable SHA. Never compare O1 patched with O2 stock as proof of a source speedup.
- Prefer one **parameterized benchmark runner and reducer** with controlled fixture definitions to hundreds of almost-identical YAML files. Keep a distinct lightweight path-specific trigger when needed for isolation; reuse shared Python experiment libraries.
- Rank queued ideas by expected information gain / incremental runner minutes. A result that rejects a high-risk physical layout in five minutes is valuable; a long build for an unproven 1% optimization is not.

## 8. First actions after this review

**Lane A (no competing CI):** R01 deterministic Kubernetes/churn fixture + its distribution audit and parity oracle. Do not start with a C++ patch.

**Lane B (reuse binaries):** R02 steady-state ingest/merge/write-vs-read matrix, starting with stock and high-churn RAW_ID only. This tests whether read wins survive writes.

**Lane C (read-only until active CI completes):** inspect 41604969692, 41605644182 and 41605807154 together. Record whether glibc helps RSS without hidden latency/CPU regressions; only then decide if another allocator test is justified.

**Lane D (method/tooling):** consolidate existing benchmark artifact fields, implement a validator for claim/evidence metadata, and run R08 correctness cases without waiting for heavy CI. Reuse `bench_native_multiclient_stock_20261103.py` for R03 rather than creating a new multi-client benchmark.

Every handoff must give: (1) the new fact with run/artifact link; (2) what is *not* proven or is invalid; (3) the independent next action with exact ID, control, and expected output. Do not repeat full logs in chat or in INDEX.
