# R07 — Time-day covering metadata: read/write/space bound, stable full ID

Status: **mathematical and semantic oracle only**. This design is NOT implemented in native ClickHouse or validated for production. Canonical [R07 root](../claims/roots/R07.json), [primary claim](../claims/R07-time-day-covering-metadata-v1.json), owner \`chatgpt-r07-day-covering-20261103\`.

## Why a new data structure, not another SAMPLES index

The [R15 source-pinned native experiment #41625086352](https://github.com/moleus/ClickHouse/actions/runs/41625086352) changed **only** SAMPLES month/day partitioning. Broad1d direct SAMPLES read fell 70k→5k and selected marks 273→20, but full native read_rows only 210k→145k and p50 only 67.98→55.45ms (1.226x). The remaining 140k scanned rows are **consistent with TAGS and TimeRanges**, each with 70k rows. This motivates a new path that prunes metadata itself, without fabricating native CPU attribution.

Pinned [StorageTimeSeries.cpp](https://github.com/moleus/ClickHouse/blob/52529f0c711566d696917678bf15e7068772fdde/src/Storages/StorageTimeSeries.cpp) documents v11 TAGS \`ORDER BY(metric_name,id)\`, TimeRanges \`ORDER BY id\`, SAMPLES \`ORDER BY(id,bucket)\`. Pinned [StorageTimeSeriesSelector.cpp](https://github.com/moleus/ClickHouse/blob/52529f0c711566d696917678bf15e7068772fdde/src/Storages/StorageTimeSeriesSelector.cpp) constructs per-query ID filter from TAGS/TimeRanges, then applies bucket/time filters to SAMPLES. A day partition on SAMPLES cannot by itself remove metadata history.

## Candidate

**New optional covering sidecar**, restricted to high-churn metric families and known allowed filter/group labels:

- Logical key \`(UTC_sample_day, metric_name, FULL original id)\`. Retain id exactly as ClickHouse generates it (including original full hash128 component). The sidecar never computes a packed/truncated replacement ID.
- Value: an **explicit set of necessary label columns** such as stage/job/branch/project, with a possible whole-tag \`Map\` variant whose write cost must be measured. Unknown filters, offsets, ACL scopes, and functions fall back to original native plan.
- One logical entry per **distinct series ID per UTC day containing at least one sample**, not one entry per every sample. Repeated writes to the same day may need dedup/aggregate state; \`ReplacingMergeTree\` is eventual and does not by itself guarantee query-time uniqueness.
- Time predicates select a **conservative superset** of day partitions for each evaluation/lookback. Every candidate ID must be deduplicated **before** per-series scalar evaluation and cross-series aggregation. The original SAMPLES table remains the source for actual time boundaries and values.
- A trusted atomic source revision or sealed-part contract is required. If late samples, stale markers, deletes or corrections occur before the sidecar is updated, queries must use the original path. A client-provided string or ordinary query cache is not a revision proof. R11 documented stale optional query cache after INSERT.

This is different from R05 SAMPLES sparse-ID mark pruning, R10 sample-bucket duration, R14 value rollup, and the previously rejected packed-UUID prefix (the latter dropped 16 bits of original hash128 and had broad-query OOM without its fastpath). It has a **write and storage cost** which may outweigh the read benefit.

## Mathematical bounds, explicitly conditional

Let \`N\` be the number of original distinct series across \`D\` UTC days. For an active lifetime \`L\` seconds and **uniform start phases** under a dense continuous interval approximation, expected days touched is:

\`E[copies per series] ~ 1 + L/86400\`.

For a three-hour lifetime, this approaches 1.125. If samples arrive every \`delta\` seconds, there are edge cases: the first sample may arrive only after a day boundary. For **uniform minute phases**, 60s cadence and samples at \`start+60,...,start+10800\`, the EXACT average is \`1+179/1440=1.1243055556\`, not 1.125. The initial unit test incorrectly required 1.125 and **failed** in [CI #41626607817](https://github.com/moleus/ClickHouse/actions/runs/41626607817); after correcting the expected discrete-sample boundary, [CI #41626770898](https://github.com/moleus/ClickHouse/actions/runs/41626770898) passed all 10 tests. This is a model correction, not an engine failure.

If a query's lookback conservatively requires \`W\` UTC-day partitions and series-day entries are near uniformly distributed, the approximate covering rows read are \`N * E[copies] * W / D\`, **before** label-index selectivity and dedup. Original broad TAGS+TimeRanges scans may cost ~\`2N\` logical rows. On a 14d high-churn corpus, 3h lifetime and a conservative two-day lookback:

- Cover entries ~\`2*1.125/14*N=0.160714*N\`, versus original ~\`2N\`: **12.44x fewer hypothetical metadata rows**.
- Combine with day-partitioned SAMPLES at ~\`N/14\` rows if native pruning is effective: baseline monthly \`3N\` rows versus ~\`0.23214N\` total metadata+sample rows, an optimistic **12.92x row ratio**, **not** a latency or CPU ratio.
- These expressions do not model per-series joins, CPU caches, allocator use, disk bytes, extra sidecar merges, concurrent clients or label cardinality. R15's 13.65x physical SAMPLES mark saving produced only 1.226x whole-query latency; repeat the exact caution here.

A sidecar with \`1.125N\` rows is **112.5% additional metadata-row writes relative to existing TAGS**, if existing TAGS is kept. The 12.5% figure means **duplicates within the sidecar beyond one copy per series**, not the total write amplification. For 30-day stable series the dense approximation is ~31 copies/ID, making this full-label sidecar unattractive unless opted out or stored sparsely. Estimate costs in **actual compressed bytes, insert CPU, merge CPU, and API backfill write rate**, not row count alone. For original active lifespan ≈3h only, the duplication bound can be favorable.

## Exact source-derived R01 400-slot/3-day sample-day copy audit

[R01 fixture generator](../../ts-lab-r01/generate_k8s_churn_fixture.py) pinned to the shared branch generates node60, two pod300, and burst15 on slots divisible by 100, \`LIFE=10800\`, \`24\` lifetimes ×\`400\` slots, 28,896 distinct series, 2,488,320 points. Aligned starts use \`life*LIFE\`; shifted starts add \`(slot*317)%10800\`. Each tier samples at \`start+(i+1)*cadence-1\`, so first/last timestamps are exact. [R07 oracle](r07_day_cover_oracle.py) replays just first/last timestamps (without regenerating 97MB of sample data):

| Source fixture | Distinct series | Day-cover entries | Entries/series | Series crossing UTC midnight |
|---|---:|---:|---:|---:|
| Aligned R01 | 28,896 | 28,896 | 1.000000 | 0 |
| Staggered R01 | 28,896 | **32,424** | **1.122093** | 3,528 |

For the staggered fixture, entries by UTC day (indexed from fixture start) are **9,608, 10,808, 10,808, 1,200**; the last 1,200 are beyond the nominal three-day fixture end because node starts are shifted. This boundary effect was already a caveat in R10 and must remain visible. These are mathematically replayed **series-day index counts**, not an inserted ClickHouse sidecar. It would add about 32,424 metadata-cover rows **on top of** original tags if both are retained.

## Semantic failure gates

The [pure Python oracle](r07_day_cover_oracle.py) and [tests](test_r07_day_cover_oracle.py) implement a deliberately limited \`avg by(stage)(avg_over_time(metric[window]))\` subset for finite floats, exact literal labels, original full IDs, and \`(t-window,t]\` time bounds. Stale markers within range are ignored; arbitrary PromQL functions, ordinary NaN behavior, native histograms, complex regex and ACL are not proven and require fallback.

- **Duplicate across days:** one ID appears in two day partitions; summing two covering rows as if they were different series changes the mean. Test proves 32.5 correct versus naive 26.6667. Mandatory \`DISTINCT original full id\` before per-series aggregation.
- **Missing/late sample:** the sidecar revision no longer matches source. Fail closed to native; do not silently serve stale data. The oracle hashes all source input to detect invalidity; that O(N) check is for proof and is **not** a practical optimization.
- **Long-lived series:** reject when copies per series exceed a configured budget, here 2.0 in the test oracle. Native implementation needs per-metric policy.
- **Unknown label matchers:** exact known label filters only, no inferred eligibility for dynamic regex. No access-control bypass.
- **Atomicity and replication:** source writes, sidecar writes, merges, replica lag, TTL expiry and backfills must be consistent or the sidecar is not eligible.

## Decision and next executable test

Mathematical potential is large for high-churn, short-lived series and 1d lookbacks, with full ID identity maintained. **Not yet a native ClickHouse speedup or safe write path**. Next use a frozen stock-O1 instance and the existing synthetic R15/R01 corpus to create a *read-only SQL diagnostic* of a day-indexed \`(day,id,selected_labels)\` sidecar and compare identical 1d stage/project label/time/value outputs with native PromQL. Explicitly measure sidecar compressed bytes, all 3 original layers, duplicate-ID read amplification and build/merge CPU. A faster simplified SQL query is not proof of native planner performance. Do not patch C++ until the stored sidecar and semantic gates pass.

Reject if the sidecar can only be kept fresh by per-query O(N) invalidation scans, or if source/sidecar insert atomicity, repeated-ID dedup, write/merge cost, ACL and arbitrary label matching are not solved. Keep old negative outcomes. No scheduled workflow, C++ rebuild, source master or production modification.

## CI completion: exact source-derived 400-slot counts verified

[Immutable evidence and audit contract](../evidence/R07-source-day-cover-r01-41627103046.json), [CI #41627103046](https://github.com/moleus/ClickHouse/actions/runs/41627103046), exact commit \`7afe6f2a77235856d1a7d32077b4145978816956\`, **12/12 PASS** (decoded job logs examined). Two added unit tests assert exact aligned 28,896 entries and shifted 32,424 entries, including the four-day boundary spillover \`[9608,10808,10808,1200]\`. The previous model-only 10/10 result [#41626770898](https://github.com/moleus/ClickHouse/actions/runs/41626770898) also passed; the first attempted continuous-day assertion [#41626607817](https://github.com/moleus/ClickHouse/actions/runs/41626607817) remains a recorded corrected failure.

**Gate result: PASS mathematical semantics, NO native performance qualification.** The next high-leverage experiment needs an actual persisted sidecar and a trusted asynchronous-freshness plan, not another C++ index assumption. The sidecar's copy count 32,424 is a **new additional metadata structure**, in addition to original TAGS and TimeRanges; CPU and write amplification are still unmeasured.
