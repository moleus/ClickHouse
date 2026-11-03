# TimeSeries tag collector locking: source audit and hypotheses

Pinned source: `52529f0c711566d696917678bf15e7068772fdde`. Baseline native query is `prometheusQueryRange` over v11 TimeSeries. This is source inspection, not proof of contended lock wait.

## Sources and critical sections

| Method and pinned location | Lock | Work held inside | Potential issue |
|---|---|---|---|
| `ContextTimeSeriesTagsCollector::storeTagsTyped` (approx. lines 1196–1285) | `SharedLockGuard` | O(N) per-row ID-map find and comparisons | Holds a reader lock over the whole batch, delays pending writers |
| `ContextTimeSeriesTagsCollector::storeTagsTyped` (same) | `std::lock_guard`, exclusive | O(unresolved IDs) `TagsKey` hash using CityHash, `groups_for_tags.try_emplace`, `id_map.emplace`, allocations | All concurrent workers of one query serialize on a critical section that can span thousands of IDs |
| `ContextTimeSeriesTagsCollector::getGroupForTags(vector)` (approx. lines 950–990) | Shared then exclusive | Full-vector find then full unresolved-vector insert | Long lock; substantial hash/alloc work |
| `ContextTimeSeriesTagsCollector::getGroupByIDTyped` (approx. lines 1390–1440) | `SharedLockGuard` | Entire ID array, hash find, output writes | Long read section; blocks a simultaneous writer |
| `ContextTimeSeriesTagsCollector::extractTag(vector, ...)` (approx. lines 1096–1128) | `SharedLockGuard` | String allocation and `ColumnString::insertData` for each group | Avoidable memory allocation under shared lock |
| `getTagsByIDTyped` | `SharedLockGuard` | ID lookup and `shared_ptr` writes per result | Long read lock; repeated shared-pointer refcount traffic |

Sources: [collector implementation](https://github.com/moleus/ClickHouse/blob/52529f0c711566d696917678bf15e7068772fdde/src/Interpreters/ContextTimeSeriesTagsCollector.cpp), [collector fields](https://github.com/moleus/ClickHouse/blob/52529f0c711566d696917678bf15e7068772fdde/src/Interpreters/ContextTimeSeriesTagsCollector.h), [stateful function](https://github.com/moleus/ClickHouse/blob/52529f0c711566d696917678bf15e7068772fdde/src/Functions/TimeSeries/timeSeriesStoreTags.cpp).

**Scope:** collector is per **query context**. Independent client queries have separate collectors. Running 8 independent queries can reveal CPU saturation or global locks, but does **not** directly prove contention on this particular collector's mutex. To test that mutex, vary execution threads **inside one query** and instrument the actual lock acquisition/wait/hold.

## Pending CPU hypothesis

On the 1.33m high-churn fixture, new series are rarely repeated within a registration block. Each unresolved ID must register a new tag set. The exclusive lock does `TagsKey` construction, which iterates tag name/value strings and runs `CityHash64WithSeed` for each pair, then updates two maps.

Let `H` be the time spent hashing pending tags, `L` current total exclusive lock work, and `W` total parallel query work. Moving `H` outside the exclusive section could reduce serialized time from `L` toward `L-H`. With `P` workers, a lower bound on completion time is roughly `max(W/P, L)`. This model only predicts **possible** improvement; real throughput depends on lock implementation, worker scheduling, allocation and pipeline dependencies.

## Staged intervention

- [`apply_tagcollector_precompute_cpp.py`](../ts-lab-cpu/apply_tagcollector_precompute_cpp.py) builds a `std::vector<std::pair<size_t,TagsKey>>` for only unresolved rows *before* acquiring exclusive `mutex`. It retains their original order and performs map publication under the original exclusive lock.
- [C++ build](https://github.com/moleus/ClickHouse/actions/workflows/ts-lab-tagcollector-prehash-cxx-20261103.yml) compiles O1 pinned source with the same earlier JOIN-override lab patch. The change is **experimental**, not validated.
- Need a same-source baseline vs patched native PromQL ABBA comparison on **identical stored data**, with 1/2/4/8 query threads; stages, projects, branch regex, whole metric, dense/sparse and concurrent loads; exact output and memory assertions.
- Need **direct** `ProfileEvents` instrumentation of collector shared/exclusive wait and hold microseconds, pending count and tag hash work; do not infer these from `futex` counts alone.
- Inspect scheduler runqueue wait and query worker `wchan` separately. `QueryPipelineEx` sleeping in futex may reflect intentional queue idle rather than lock contention. `QueryThreadLog` can correlate OS TIDs to the query ID.
- Staged next test: remove long-lived shared locks by copying stable `shared_ptr<const TagNamesAndValues>` references under lock, then formatting outside, only after a measured reader-versus-writer wait problem.

## Instrumentation without C++ patches

ClickHouse already exposes `GlobalThreadPoolLockWaitMicroseconds`, `LocalThreadPoolLockWaitMicroseconds`, `ContextLockWaitMicroseconds`, `PartsLockWaitMicroseconds`, `SharedPartsLockWaitMicroseconds` and corresponding hold counters in `ProfileEvents`. Read `query_log.ProfileEvents` for the actual query IDs and reconcile with `system.query_thread_log`.

Off-CPU probe [v1](https://github.com/moleus/ClickHouse/actions/runs/41594014184) used full `/proc` thread snapshots every 25ms; at least one wave spent **81% of the sampling window collecting observations**, so any mutex interpretation is INVALID. [v2](../ts-lab-cpu/probe_offcpu_parallelism_v2.py) reduces reads, uses a 100ms sampling interval, links TIDs to `query_thread_log`, and rejects sampling overhead above 20%. Its `schedstat` counters measure runnable queue delay, not a mutex lock wait.
