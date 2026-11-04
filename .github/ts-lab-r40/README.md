# R40 — cumulative native metric-name optimizer

**Status: built and native-semantic validated research integration.** This is not a new measured speedup and is not a production release.

## Combined source patch

- [Exact build 41797918745](https://github.com/moleus/ClickHouse/actions/runs/41797918745): ClickHouse source `52529f0c711566d696917678bf15e7068772fdde`, LLVM 22.1.8, Release `-O2 -DNDEBUG -g0`, jemalloc. Binary SHA256 `522bbe374eb73727142bcd6239a7beb62a983c2f5a29a7f8b3456446c5536653`.
- [155 native compatibility cases and per-shape physical plans PASS 41798916172](https://github.com/moleus/ClickHouse/actions/runs/41798916172); R35, R37, R38 and R40 binaries used the same test fixture. A previous run [41798625174](https://github.com/moleus/ClickHouse/actions/runs/41798625174) failed because the test harness omitted the `wide_without` plan capture; fixed with no C++ changes.
- [Cumulative source-pinned helper](apply_cumulative.py). Apply upstream source, then [R35](../ts-lab-r35/apply_metric_drop.py), then [R37](../ts-lab-r37/apply_injective_projection.py), finally [R40](apply_cumulative.py) to enable R38's verified `without` parent route. The last helper refuses a mismatched R37 source blob. All modifications touch only `applyFunctionOverRange.cpp` and no storage/index or collector code.
- [Primary claim](../ts-lab/claims/R40-composable-proven-native-metric-drop-v1.json), [root](../ts-lab/claims/roots/R40.json), [semantic suite](guard_suite.py) and [CI build](../workflows/ts-lab-r40-cumulative-compiler-o2-20261104.yml).

## Decision tree

`avg by(stage)(avg_over_time(ci_duration[1d]))`: R35 defers metric-name removal to the immediate parent. Full 1.33m/14d R35 vs optimized stock already achieved −35% wall, −35.6% CPU, −38.6% tracked memory, with independent full-output oracle.

`avg without(build,project,branch,job)(avg_over_time(ci_duration[1d]))`: R38 applies the same safe deferral through `without(...)`; its independent native ABBA against R35 showed −39.2% wall, −39.5% CPU and −38.6% tracked memory, and two-client full-memory test passed 16/16 versus 6/16 under the same fixed cap.

Root exact-metric range functions or other parents for which the safe deferral is impossible: R37 removes the duplicate-check GROUP BY, but retains the required per-series metric-name group projection.

Any selector with possible multiple metrics, regex (without fixed exact name), offsets and subqueries retains the original defensive duplicate-check path. The 155-case suite verifies complete output equality and the correct physical-plan selection.

**Do not add R36 unconditionally**: its selective stage query regressed 9.4% under a stable control. **Do not add R39**: its native ~3% observation is too close to drift. R34 is independently measured but not yet tested in this cumulative stack. Do not multiply isolated percentages.

## Remaining gates

The integration result is not a p95/p99, mixed-ingest or optimized-VictoriaMetrics comparison. Next work: R41 explores removing the final root-only projected group registration; R40 is its exact source-matched control. Separately test sustained read/write, merges, retention, tenant access, and distributed query semantics. No master, upstream or production modifications.
