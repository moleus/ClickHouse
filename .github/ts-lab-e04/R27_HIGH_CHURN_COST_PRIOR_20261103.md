# R27 — high-churn 14-day VM global-vs-per-day index search: preregistered cost prior

State at start: **source/math hypothesis only**, no R27 speedup established. Owner [R27 root](../ts-lab/claims/roots/R27.json); [claim](../ts-lab/claims/R27-vm-perday-global-jenkins14d-v1.json), [source-pinned benchmark](r27_vm_jenkins_index_path.py) and [fixture generator](r27_generate_jenkins_fixture.py). GitHub issue tracker disabled, all writes stay in `lab/ts-cbo-adaptive-20261103`.

## Source and input identity

- VM v1.134.0 source commit `eaf7a68c92c8069a67ae02e601e81453a5e002de`, archived release SHA `cd6e109bae85ff7c138d838b32be9a7b3a387a7cf9234f22991da10`.
- Canonical 95,000 new series per UTC day for 14d, 1,330,000 total, exactly one sample per ID, metric `jenkins_ci_95k_daily`, source SHA `8f3a36846f80d6be5a8604d142e934399f2687094766c57bf105e43977f52858`. Uses *same row generation logic* as [existing source](../ts-lab-bench/generate_95k_daily_churn_ts.py), with the ClickHouse write side effect omitted. The second CI pilot [41672930806](https://github.com/moleus/ClickHouse/actions/runs/41672930806) confirmed the exact hash and **all 14 days visible** after VM import.
- The failed first pilot [41672882701](https://github.com/moleus/ClickHouse/actions/runs/41672882701) used an incorrect workflow shell SHA string despite the generator having the correct pinned digest. It is an **INVALID_METHOD preflight**, not a scientific performance rejection.
- The failed second pilot [41672930806](https://github.com/moleus/ClickHouse/actions/runs/41672930806) exposed a mistaken *independent test oracle* group count for `project=project-00`: because `i%8==0`, `i%38` visits **19** stage residues, not 38 (`gcd(8,38)=2`). Corrected without changing source fixture or intervention. Also **INVALID_METHOD preflight**.
- Follow-up [41673646832](https://github.com/moleus/ClickHouse/actions/runs/41673646832) runs the corrected analytic oracle; its conclusion must be checked before citing any results.

## Cost prior — measurable and falsifiable

Global index postings cover `N = 14 * 95000 = 1,330,000` unique series for the full metric, regardless of a requested 1-day lookbehind. VM's per-day index scans postings for the eligible days, and unions the sets.

For a `d`-day window with one sample/day-specific ID, the **ideal candidate-count comparison** is:

`N_global = 95,000 * 14`, `N_day(d) = 95,000 * d`.

| Window | Approx candidate IDs global | Approx candidate IDs per-day | Ideal candidate-count ratio |
|---|---:|---:|---:|
| 1 day | 1,330,000 | 95,000 | 14× |
| 3 days | 1,330,000 | 285,000 | 4.67× |
| 7 days | 1,330,000 | 665,000 | 2× |
| 14 days | 1,330,000 | 1,330,000 | 1× |

This table is a **model, not actual decoded posting counts**: VM considers inclusive date boundaries, metric grouping, indexDB partitions and cache states, and the global path may use additional filters. In particular, using **one evaluation tick with a `[d]` lookbehind** makes the query's native samples read semantics clear, but it does not force the index to seek exactly d complete calendar days. Trace and actual measurements decide.

Expected cost terms:

```
Global = single global posting search + candidate filtering + TSID read + sample/rollup
Day(d)= d date posting searches + union of daily candidates + TSID read + sample/rollup
Break-even when the saved candidate/ID work exceeds date seeks and union overhead.
```

VM [`adjustTimeRange`](https://github.com/VictoriaMetrics/VictoriaMetrics/blob/v1.134.0/lib/storage/storage.go#L1623-L1651) uses global for an entire indexDB or a range with max/min date difference over40, not a cardinality-based formula. That is a useful source-level baseline for E03, but its 40-day rule is not automatically appropriate to ClickHouse.

## Correctness and memory contract

- All four phases GLOBAL/DAY/DAY/GLOBAL read cloned copies of the same cleanly stopped VM import. Only `-disablePerDayIndex` changes.
- Disable result response cache and fix tag-filter index cache at 1 byte in both modes.
- VM `-search.maxUniqueTimeseries=2000000` is an intentional lab-only override: broad14d matches >165k and would hit the real `165000` policy limit. This experiment **cannot** claim the production default would accept the query.
- Evaluate exact Float64 expected group means directly from the generator without trusting either query engine. Since each series has one sample, `avg by(stage)(avg_over_time(series[d]))` equals the mean of per-series values in range. The independent oracle uses integer milliunits and checks exact stage-group cardinality.
- Keep finite memory budget, A/B/B/A drift bound ≤25%, source/VM/fixture SHA verification and per-query `trace=1` path proof *after* measured requests.
- A speedup at 1d that disappears at 14d would support time-aware access selection. Negative/crossover results are equally valid if controls pass. No change to CH production or any E03/R07 owner artifacts.

## Decision requested from R27

Report one accepted per-window global/day ratio for **broad 1d/3d/7d/14d**, plus valid/invalid selective cases and CPU per query. Do not multiply measured R26 VM-only gains by R15 or R07 CH changes. Handoff to E03 should specify cost terms and the need to expose native CH TAGS text-index cardinalities to the CBO.
