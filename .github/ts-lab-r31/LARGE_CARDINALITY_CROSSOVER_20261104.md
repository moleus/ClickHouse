# R31: global IN membership loses to ordered JOIN at 1.33 million series

Date 2026-11-04. **Decision: REJECT unconditional IN-set native rewrite.** This is a validated direct internal SQL comparison, not a patched native PromQL measurement.

Evidence: [CI 41721273167](https://github.com/moleus/ClickHouse/actions/runs/41721273167), [immutable JSON](../ts-lab/evidence/R31-1330k-in-memory-41721273167.json). ZIP SHA256: 2dff6a2bd853d298db69e0bf18e0a5db205d32568dc682bce7199b46684a530a, artifact ID 13272992202.

## Pinned input and method

- Source 52529f0c711566d696917678bf15e7068772fdde. Unmodified O1 lab JOIN-override binary SHA256 3990e373b43396d0faafd8feffe5dd6514bd57c520ec12e89b8e5796d64e6221; original C++ still emits ALL JOIN.
- Canonical 95k new Jenkins series/day for 14 days, 1,330,000 series, fixture SHA256 8f3a36846f80d6be5a8604d142e934399f2687094766c57bf105e43977f52858, 258,336,218 input bytes.
- Default v11 LowCardinality(UUID) ID schema. TAGS, TimeRanges and SAMPLES each have one frozen physical part and 1,330,000 rows. No duplicate right-hand TimeRanges rows.
- For each query, compare direct generated ID SQL: source ALL, R30 ANY, and R31 IN-set. Same physical data, join settings and binary.
- Balanced ALL / ANY / IN / IN / ANY / ALL; 3 warmups and 4 measured requests per phase. Per-mode A/A control drift at most 1.01%. max_threads=4, query memory limit 1.8GB, server memory 4.8GB.
- Original native PromQL was nonempty (14, 266, and 19 points). Direct SQL output parity: equal exact distinct-ID counts, two independent aggregate hash fingerprints, and complete SAMPLES values for the first 64 sorted IDs. The complete large-ID set was NOT independently checked element by element.

## Measured direct ID-stage SQL

| Selector | Mode | Wall ms | CPU ms | Query tracked peak MB |
|---|---|---:|---:|---:|
| stage 14d | ALL | 366.25 | 894.50 | 112.15 |
| | ANY | **347.50** | **858.20** | 113.58 |
| | IN | 998.00 | 1594.27 | 158.00 |
| project 14d | ALL | 794.75 | 1658.49 | 148.62 |
| | ANY | **745.25** | **1583.24** | 148.29 |
| | IN | 1376.00 | 2272.57 | 260.44 |
| project 1d | ALL | 329.50 | 808.84 | 116.11 |
| | ANY | **306.25** | **779.71** | 110.98 |
| | IN | 319.50 | 821.86 | **29.84** |

At 14d, IN is **2.87 times slower** than ANY on stage and **1.85 times slower** on project. The 14-day TimeRanges set can have all 1,330,000 distinct IDs; IN builds a set before scanning TAGS. For the 1-day project query, the right time filter matches about 95,000 IDs. IN has about **73% lower tracked query memory** but ~4.3% higher latency and ~5.4% more CPU than ANY. These right-side counts are inferred from the canonical daily fixture and frozen physical table, not separately instrumented per query.

All modes read 2,660,000 rows and about 264.2MB in the large benchmark. No material read reduction was observed for IN. The small R31 k16 fixture showed a 54.5% IN wall reduction, but that cannot be extrapolated to high cardinality. R30 ANY has a smaller and repeatable SQL-stage benefit on this frozen 1.33m baseline, but its C++ native impact remains unmeasured.

## Main physical finding and next plan

The pinned StorageTimeSeriesSelector source explicitly uses an ID-ordered streaming merge to avoid creating a right-hand set. R31 IN generates a CreatingSets operator and pushes the ID predicate into TAGS PREWHERE on the small fixture. At 14 days, the cost of building the very large set dominates the benefit of filtering TAGS.

Do not enable IN unconditionally or fit a single universal threshold. An adaptive choice needs the actual active ID bound for the time window, distinct candidate TAGS ID count, set-building memory, ordering requirements, and concurrent process RSS. ClickHouse EXPLAIN estimated rows are not a hard upper bound (R06 already showed false-low cardinality estimates).

Next gates: compare on verified R29 stock O2/jemalloc, test candidate-restricted TimeRanges sets for selective matchers, keep full E06/R08 native correctness tests, and measure 1/4/8 clients before any opt-in C++ implementation. The R31 source-only AST patch is not yet compiled or promoted. No master, production, or upstream changes.
