# R39: bounded projected-tag interning for native without(...)

**Status: UNMEASURED C++ candidate. No native performance improvement is claimed until measured against R38.**

## Baseline and intervention

- Baseline: R38 [verified source-matched build 41791235811](https://github.com/moleus/ClickHouse/actions/runs/41791235811), exact source `52529f0c711566d696917678bf15e7068772fdde`, LLVM 22.1.8, Release O2 and jemalloc.
- R38 compiler already uses R35's safe parent deferral for `without(...)`. R39 keeps **both** R35 and R38 compiler patches unchanged and modifies only `ContextTimeSeriesTagsCollector.cpp`, the `removeTags(vector, removed_labels)` call site.
- [Canonical root](../ts-lab/claims/roots/R39.json), [primary claim](../ts-lab/claims/R39-without-projected-tags-intern-v1.json), [source-pinned C++ helper](apply_without_intern.py), [isolated build](../workflows/ts-lab-r39-without-intern-o2-20261104.yml), [R38/R39 full native guard](guard_suite.py).
- This is separate from R36, which optimized **one retained label via removeAllTagsExcept**, and from R37 which optimized a different compiler path. Do not combine isolated gains.

## Source-level cost mechanism

Pinned `ContextTimeSeriesTagsCollector::transformTags` already deduplicates **identical original group IDs**. Its `RemoveTagsTransformFunc` creates a new `TagNamesAndValues` shared object for every distinct original full tag set with any removed tag, even when 1,330,000 groups later collapse to 38 projected stage groups. Then `getGroupForTags` hashes every projected object before returning the group IDs.

R39 directly hashes each original group's **retained** tags in their original sorted order using the exact `TagsKey` hash sequence. An `unordered_multimap<Hash, compact-index>` finds candidate projected groups. A hash match is never sufficient: the algorithm compares the entire original retained name/value sequence to the candidate projected tag set. It constructs a shared projected object only for a **new distinct result**, and registers the small final vector with the existing collector.

For N input groups, K tag pairs and D distinct results after removal, the old implementation can allocate O(N) projected vectors; R39 only O(D), while it still scans O(N K) original name/value pairs and emits N output group indexes. A performance gain is a hypothesis, not a consequence of the asymptotic bound alone.

## Safety gates and fallback

- Only try this path when `N>=16384`, `tags_to_remove` nonempty, and no more than `min(4096,N/8)` distinct projected tag groups are needed.
- If the distinct-value cap is reached, return through the original `transformTags` **before registering any projected group**. Temporary allocations are released automatically.
- Preserve group #0 (no labels); exact absent/empty labels; duplicate source groups; full tag-name/value collision checks; sorting and original group identity. No persistent cache.
- Compare R38 and R39 native outputs for 157 compatibility cases: 155 inherited cases plus a high-churn `wide_big` fixture with 32,768 unique full groups, 38 stages, and a high-cardinality negative control preserving each unique instance.
- Recheck mixed metrics, stale NaN, NaN/Inf, counter resets, regex, offsets, subqueries and all physical native plans.

## Performance gate

Use exactly the original 1.33m/14d RAW_UUID physical parts, 4CPU/4GiB cgroup, 2.85GB server cap. Query:

```promql
avg without(build,project,branch,job)(avg_over_time(ci_duration[1d]))
```

R38/R39/R39/R38 fresh-process A/B/B/A, two warmups plus four measured requests each. Exact 532 output points per request, independent generator-derived oracle, complete QueryFinish metrics and read-row parity. Report wall, CPU, query memory and sampled process RSS; verify control drift and code activation. Include narrow, high-cardinality and dense controls. If no reliable native speedup or any semantic/regression gate fails, classify `NO_EFFECT` or `REJECT`, not production promotion.
