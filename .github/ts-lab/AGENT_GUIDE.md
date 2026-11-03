# Shared TimeSeries lab: instructions for every agent

This file is the operational source of truth for new work in `moleus/ClickHouse`. Read [INDEX.md](INDEX.md) and [METHODOLOGY_AND_FAILURES.md](METHODOLOGY_AND_FAILURES.md) first.

## One branch, one append-only history

1. **Write only** to `lab/ts-cbo-adaptive-20261103`. The older CPU/layout/perf branches have been merged and must not receive new commits. Never write to `master`, production, or upstream.
2. Fetch current branch HEAD before a write. Prefer new path per experiment, distinct from other agents' active files. For existing files, `fetch_file` and update with its exact blob SHA; on conflict refresh and reconcile instead of force-pushing. Do not rebase, amend, reset or force-push.
3. **Before creating any new question-specific experiment claim or expensive workflow, reserve its canonical question ID with a create-only `.github/ts-lab/claims/roots/RNN.json` file.** The root names exactly one primary claim and owner. If another root exists, do not create a different suffix as a workaround. An allowed support claim must be explicitly named in that root. This rule closes the R01 collision where two agents created different claim suffixes for overlapping Kubernetes fixtures. The root owner coordinates non-overlapping subtests; never overwrite the original root or claim.
4. Record a run in a new experiment-specific Markdown log, with pinned source SHA, workflow commit SHA, fixture SHA, binary SHA, query text, runner hardware, failure classification and CI link. Add a short index entry to [INDEX.md](INDEX.md), using fresh blob SHA.
5. Use a new workflow per expensive experiment. No shared environment, common mutable data volume, static query ID, or overlapping GitHub cache mutation. Limit runner matrix parallelism. No code changes during measurement phases.
6. Protect ongoing builds and cache artifacts. Never cancel them when pushing or rerunning a different experiment.

## Preserve repository symlinks

**Root AGENTS.md is a tracked symlink (Git mode `120000`) to `.claude/CLAUDE.md`**, not a regular Markdown file. The GitHub Contents API can dereference it when reading yet write a huge replacement symlink string when updating `AGENTS.md`. This caused all workflow checkouts to fail in run [41597417015](https://github.com/moleus/ClickHouse/actions/runs/41597417015). The original symlink is restored.

- Edit **`.claude/CLAUDE.md`**, never `AGENTS.md`, when changing global agent instructions.
- Before adding a lab workflow, verify `test "$(readlink AGENTS.md)" = .claude/CLAUDE.md` and `git ls-files -s AGENTS.md` mode `120000`.
- Use isolated `.github/ts-lab/` source files for guidance; do not mutate other upstream symlink paths through GitHub Contents API without inspecting their git mode.

## Safe GitHub Actions scheduling

Recommended isolated workflow:
```yaml
on:
  push:
    branches: [lab/ts-cbo-adaptive-20261103]
    paths: ['.github/workflows/ts-lab-UNIQUE-EXPERIMENT.yml']
  workflow_dispatch:
concurrency:
  group: ts-lab-${{ github.workflow }}-${{ github.run_id }}
  cancel-in-progress: false
jobs:
  compare:
    strategy:
      fail-fast: false
      max-parallel: 4
```

The run ID in the concurrency key means a new push **cannot cancel an older run**. `cancel-in-progress: false` also prevents cancellation within the group. A workflow whose concurrency group uses `github.ref` or just the branch will serialize all runs, and `cancel-in-progress: true` can cancel another agent's job. A shared group with `cancel-in-progress: false` can still cause pending runs to be replaced; use run-unique groups. Avoid schedules and unbounded `push` triggers on experimental workflows.

When promoting an older workflow to this branch, change its branch filter before running it and limit `paths` to itself. Do not bulk-enable hundreds of historical workflows; the old experiments stay available in Git history. Keep the manual `workflow_dispatch` path where appropriate.

## Profiling contract

Per query collect:
- `query_log`: `query_duration_ms`, `ProfileEvents.OSCPUVirtualTimeMicroseconds`, `read_rows`, `read_bytes`, `memory_usage`, `SelectedMarks`, `ReadCompressedBytes`.
- `processors_profile_log`: `plan_step_name`, operator name/instance, `elapsed_us`, `input_wait_elapsed_us`, `output_wait_elapsed_us`, input/output rows.
- `trace_log`: ALL CPU samples counted in disjoint categories; separate on-CPU and Memory alloc stacks. Sample counts are approximate CPU consumption, not latency.
- `/proc/PID/task/TID/schedstat`: changes in running CPU time and runnable runqueue wait. `/proc/PID/task/TID/status`: voluntary/involuntary context-switch counts; `wchan` and `stat` states. Optional privileged eBPF `sched_switch` or futex tracing can distinguish sleeping sites and timed waits.
- Server RSS peak and post-query retention separately from query-level memory tracker. Inspect working set, cache, allocator and background merges. Measure actual core utilization and **integral of simultaneously running threads**, not merely `max_threads`.

**Do not label a sleeping futex/condition-variable worker as a contended mutex without owner/wait evidence.** `input_wait_elapsed_us` and `output_wait_elapsed_us` are pipeline wait counters, not lock contention. Kernel runqueue time is not mutex wait. Sampling `wchan` is approximate and can miss short waits. Use direct lock wait probe where permitted, plus a no-probe overhead control.

## Comparison contract

Use A/B/B/A with 2+ warmups, multiple measured repetitions, randomize if shared hardware conditions warrant it, report baseline drift and reject high drift (default threshold 25%; stricter when effect is small). Assert exact nonempty label/time/output cardinality and numeric tolerance; confirm same stored physical data/hash, or explicitly record a schema/layout change. Stop background merges only when that is the declared test variable. Compare the same source revision and optimization level; separately measure write, merge, read, memory and p95/p99 under concurrency. Keep original C++ baseline and a cumulative candidate artifact, but **never merge unverified C++ patches simply because they were faster in a diagnostic SQL query**.

For tests requiring a built binary, use existing SHA-verified artifacts and ccache; never restart another agent's nearly completed compilation. On a failure, preserve the artifact and its precise reason in [METHODOLOGY_AND_FAILURES.md](METHODOLOGY_AND_FAILURES.md).

## Handoff

Write a compact **current measured result, negative outcomes, next experiment** to the shared index and relevant checkpoint. Prefer direct CI links and paths over copying long logs. The common branch makes new files visible to all agents; it cannot force other already-running agents to change their internal working branch. Ask other agents to switch when communicating outside this repository.
