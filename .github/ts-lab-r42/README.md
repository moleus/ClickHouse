# R42 — direct native output tags for root exact-name range queries

**Status: 160-case native semantic PASS, measured NO_PROVEN_GAIN / DO_NOT_PROMOTE.**

## Completed native experiment — 2026-11-04

- [Immutable A/B/B/A experiment](../ts-lab/evidence/R42-native-root-35k-local-20261104.json), [successful R42 O2/jemalloc build 41801868345](https://github.com/moleus/ClickHouse/actions/runs/41801868345), binary SHA256 `5563d0d37e12d8fbc8270923284804b6c9b5522a177618ade26534c7050b0cff`, [160-case native semantic and plan PASS 41803649288](https://github.com/moleus/ClickHouse/actions/runs/41803649288).
- Same 1.33m / 14d RAW_UUID physical parts. Exact native `avg_over_time(ci_duration{stage="stage-1"}[1d])` returns 35,000 fixture-derived output points, all checked on *each* warmup and measured query, same 3,989,152 read rows and 337,293,196 read bytes.
- R41/R42/R42/R41 fresh-server A/B/B/A, 2 warmups+6 measured requests per phase, 4 CPU quota, 4 GiB cgroup, ClickHouse 2.85 GB server cap, max_threads=2. Mean of within-phase medians.

| Metric | R41 | R42 | Observed change | R41 control drift |
|---|---:|---:|---:|---:|
| Wall | 638.0ms | 642.5ms | **+0.71%** | 1.25% |
| Query CPU | 568.15ms | 575.68ms | **+1.33%** | 2.70% |
| Tracked query memory | 72,238,661B | 72,220,053B | −0.026% | approximately 0 |
| Sampled request RSS peaks | 431,377,408B | 411,090,944B | −4.7% | 7.17% |

The differences in wall, CPU and sampled RSS are smaller than reference drift. R42's direct serializer passes correctness but **does not provide a reproducible native speed or RSS gain** here. Mark `NO_PROVEN_GAIN / DO_NOT_PROMOTE`, not a cumulative win. No storage/collector or production change.

## Source mechanism

[R41](../ts-lab-r41/README.md) made root exact-metric queries faster by retaining original group IDs through range aggregation and removing the constant `__name__` only in `finalizeSQL.cpp`. It removes projected group registration. In its final step, however, R41 evaluates:

```sql
arrayFilter(tag_pair -> tag_pair.1 != '__name__', timeSeriesGroupToTags(group))
```

This builds a full output tag array and a filtered tag array for each result row. R42 adds `timeSeriesGroupToTagsWithoutName(group)` to the existing native TimeSeries function registration, using the same per-query `ContextTimeSeriesTagsCollector` as `timeSeriesGroupToTags`. The serializer creates `Array(Tuple(String, String))` directly and skips `__name__` while filling `ColumnString` and `ColumnArray`; no intermediate full tag-array materialization, no temporary projected groups and no persistent cache.

The caller is **only** R41's late-root exact-metric proof: a direct range selector with a nonempty exact `__name__`. Negative cases (mixed metrics, regex, offsets, subqueries, nested binary, and name-preserving first/last range functions) remain on the R40/R37 paths. No selector, storage, range accumulator, data layout or aggregation arithmetic is changed.

### Exact source and experiment

- [Canonical primary claim](../ts-lab/claims/R42-direct-root-tags-serialization-v1.json) and [root](../ts-lab/claims/roots/R42.json).
- [Source-pinned R42 helper](apply_direct_root_tags.py), applied after source-pin checked R35 → R37 → R40 → R41. Only changes `src/Functions/TimeSeries/timeSeriesGroupToTags.cpp` and `src/Storages/TimeSeries/PrometheusQueryToSQL/finalizeSQL.cpp` relative to R41.
- [R42 O2/jemalloc isolated build workflow](../workflows/ts-lab-r42-direct-root-tags-o2-20261104.yml); pinned source `52529f0c711566d696917678bf15e7068772fdde`, LLVM 22.1.8, Release `-O2`, jemalloc. Baseline R41 binary SHA256 `1fbf8d81f3d4f730f87578e4fb4f16de593f6fee30370d0afd2fd7c67f8281a2` [run 41799488038](https://github.com/moleus/ClickHouse/actions/runs/41799488038).
- [160-case native oracle](guard_suite.py) against R41; exact labels, timestamps, values and error codes, plus all Aggregating-plan counts.

### Acceptance gate

Primary: 1.33m RAW_UUID source series over 14 days, native root `avg_over_time(ci_duration{stage="stage-1"}[1d])`, 35,000 distinct output label sets, one point each. R41/R42/R42/R41 fresh-process A/B/B/A under 4CPU/4GiB cgroup, 2.85GB ClickHouse server cap and `max_threads=2`. Two warmups plus six measured native requests each phase. Validate every complete response against an independent fixture generator, including absent `__name__`, all labels, timestamp and value. Verify identical physical parts, read rows/bytes and QueryFinish counters. Report wall, CPU, query tracker, process sampled RSS and baseline drift. Include root `rate`, `last_over_time` negative, empty labels, NaN/Inf/stale and mixed-metric regex.

R41 local pilot [evidence](../ts-lab/evidence/R41-native-root-35k-local-20261104.json): R40→R41 observed −19.0% wall, −18.9% CPU, −4.5% tracked memory, **+8.3% sampled RSS** with R40 control drift 11.8%; this is not p95/p99 or production evidence. R42 should be rejected if its direct serializer changes semantics or makes native CPU/RSS worse. Do not multiply independent isolated gains.

The R42 change is separate from R36 (regressed narrow selector), R39 (inconclusive), and R34 (not tested cumulatively). Master/upstream/production remain unchanged.
