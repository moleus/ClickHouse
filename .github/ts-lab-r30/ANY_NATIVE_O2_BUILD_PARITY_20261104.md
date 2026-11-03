# R30 native ANY O2/jemalloc — exactly one source delta vs R29 stock

Date: 2026-11-04. Source `52529f0c711566d696917678bf15e7068772fdde`. **Binary build PASS; native end-to-end performance not yet concluded**.

## Verified pair

| Control | Stock R29 | Lab candidate R30 |
|---|---|---|
| Workflow | [41685467438](https://github.com/moleus/ClickHouse/actions/runs/41685467438) | [41723209512](https://github.com/moleus/ClickHouse/actions/runs/41723209512) |
| Artifact | `13270474797` | `13273174943` |
| Binary SHA256 | `5d629ddfda91919402fdf8475ec3da9fe718d48ef386bc51be677264df6dc9ef` | `aa857ccff473837a37772c04825d8adcd0fbf1dc48d16d1841e2baafab14a59c` |
| Source base | same SHA above | same SHA above |
| Compiler | LLVM 22.1.8 | LLVM 22.1.8 |
| CMake build type | Release | Release |
| C++ flags | `-O2 -DNDEBUG -g0` | `-O2 -DNDEBUG -g0` |
| Allocator setting | `ENABLE_JEMALLOC=ON` | `ENABLE_JEMALLOC=ON` |
| Source patch | none | one-site TimeRanges strictness below |

The binary ZIP files preserve `BUILD_INFO.txt`, archive/executable hashes, CMake option dump, and compile timing. R30 ZIP SHA256 `7deaf286b3b04ce065a03a80ada2886f7be7c11196ab39275443f38c22dff4ad`. R29 ZIP SHA256 `fa047fd0833461f00f1615be39a30fdeb8e36a40fce19ca45f00c36420d8325f`.

The R30 workflow uses a detached checkout of the exact source SHA, restores original submodule paths, and applies `apply_any_join_cpp.py` only to `src/Storages/StorageTimeSeriesSelector.cpp`. It verifies `git diff --name-only` identifies only this file before and after compilation, verifies the in-memory diff and checksum do not change during the build, and fails on mismatches. The source patch helper also fails closed if the function and `matched` membership context changed.

The sole intended source change in `StorageTimeSeriesSelector::addTimeRangesTableJoin` around line 526:

```diff
-            table_join->strictness = JoinStrictness::All;
+            table_join->strictness = JoinStrictness::Any;
```

No changed schema, serializer, query function, planner setting or optimizer hint; all future performance comparisons must use the same frozen physical data and entire native PromQL outputs. This source-level parity does not prove runtime semantic parity.

## Matched native gate

[CI full native stock/ANY ABBA workflow](../workflows/ts-lab-r30-native-o2-matched-20261104.yml), [initial real run 41724842961](https://github.com/moleus/ClickHouse/actions/runs/41724842961). It uses the verified artifacts above and a copy of one frozen TimeSeries v11 database template for each stock/ANY/ANY/stock phase. It tests exact labels/timestamps and numeric tolerances on stage/project/whole/regex, QueryFinish wall/CPU/rows/bytes/query memory, sampled process RSS/HWM and all active inner-table part names, rows, marks and disk bytes. Stop each source process before cloning its data; stop TimeRanges merges before queries. Reject control drift above 25%, output mismatch, physical merges, or missing logs. Only promote after E06/R08 and larger 1.33m/14d native full-query and concurrency gates.

## Invalid workflow parser pilots, not compilation failures

[Three invalid YAML events](../ts-lab/evidence/R30-invalid-yaml-41722930194.json) had no jobs; resolved by [commit ad43b2be](https://github.com/moleus/ClickHouse/commit/ad43b2be52816530b212e350e14c654cba8e2243). They are not benchmark results. First R30 actual build [41723209512](https://github.com/moleus/ClickHouse/actions/runs/41723209512) succeeded. Master/production and the stock R29 artifact were not modified.
