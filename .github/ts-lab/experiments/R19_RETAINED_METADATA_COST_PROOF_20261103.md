# R19 — retained TimeSeries metadata after SAMPLES retention

Status: source-backed hypothesis; physical and native performance gates must run on pinned stock-O1 ClickHouse. No production change.

## Pinned source proof

- Source revision: `52529f0c711566d696917678bf15e7068772fdde`; binary SHA256 `f0a2fa1f814c58853f43423705cac10ab9df3bfe5887e27ebe9620ba7cb9660e` from stock-O1 build [41596421285](https://github.com/moleus/ClickHouse/actions/runs/41596421285).
- `src/Storages/StorageTimeSeries.cpp`, `## Retention {#retention}` (in pinned source around offset 72,700): main SAMPLES has **no default TTL**; it can receive `SAMPLES INNER ENGINE ... TTL toDateTime(bucket,'UTC') + INTERVAL 30 DAY`. Source explicitly states: **the TAGS target has no TTL by default and its IDs remain after all samples have been deleted**.
- `src/Storages/TimeSeries/TimeSeriesSettings.cpp`: `tags_deduplication_cache_expiration_seconds` defaults to 3600 seconds and controls **only an in-memory cache** of recently written tags. It is **not** a disk retention period.
- The SAMPLES `recent_samples_ttl_seconds` setting controls another target, not TAGS or TimeRanges. Do not equate its 4-day default with retention of the full metric history.

## Cost bound and falsifier

Let `K=400` node slots, mean node lifetime `L=3h`, one logical metric per new node. Idealized churn `N_new/day = K×24/L = 3200` unique IDs/day. If main SAMPLES retention is `R=15d`, eligible live sample IDs after warm-up are at most `N_new×R≈48000`; after `H=365d`, absent TAGS eviction the table can contain `N_new×H≈1168000` unique IDs. Thus the **logical** retained-metadata to live-ID ratio is `H/R≈24.33`. This is *not* a count of bytes, marks, RSS, or native CPU. More metrics per node, pod churn, deduplication and series continuity change the numbers.

If broad selection reads `M` TAGS/TimeRanges rows before pruning `M_active`, a lower-order CPU term can grow with `M/M_active` even when SAMPLES are bounded. However source-index selectivity, MergeTree marks, read order and TimeRanges filtering could keep real reads close to active IDs. **Measure `read_rows`, `read_bytes`, `SelectedMarks`, CPU and p50 before calling this a query cost.**

R19 deterministic physical test has two same-source RAW UUID v11 tables:
- **Clean**: 2,000 live unique IDs, samples at 2026-10-20.
- **Retained**: same 2,000 live IDs plus 48,000 historical unique IDs (one sample each at 2026-08-10). Drop only the historical **SAMPLES partition** `202608` from the retained table. This intentionally simulates physical sample expiration without waiting on asynchronous TTL. It does **not** measure the TTL daemon. Assert each table now contains the same 2,000 live sample points and identical full native PromQL outputs; compare 2,000 vs 50,000 TAGS/TimeRanges records and native read CPU.
- Same binary, schema, `recent_samples_ttl_seconds=0`, query text/interval, synthetic fixture generator and host. Broad and `project=p0` selectors, warmups plus A/B/B/A, 25% baseline control drift gate. Reuse exact script and workflow; no C++ compile.

## Correctness hazards before any cleanup proposal

Do not DELETE TAGS entries based solely on age: a series can resume after a long gap or arrive late, the key generator can map to the existing ID, and TimeRanges/label security filters may need its original metadata. A safe retention design must prove bounded late-arrival semantics, consistent TAGS+TimeRanges+SAMPLES retirement and exact native output after restart, reinsert, merges and concurrent writes. This task first measures the baseline risk; it does **not** implement cleanup.

Canonical R19 root: `.github/ts-lab/claims/roots/R19.json`; claim: `.github/ts-lab/claims/R19-expired-samples-retained-tags-timeranges-v1.json`.

First [CI 41635086542](https://github.com/moleus/ClickHouse/actions/runs/41635086542) was **INFRA_FAIL**: the workflow never started the lab server (`Connection refused`), so it supplies no retention or CPU evidence. The follow-up workflow explicitly starts a bounded localhost ClickHouse, waits for `/ping`, and enables QueryFinish logging.
