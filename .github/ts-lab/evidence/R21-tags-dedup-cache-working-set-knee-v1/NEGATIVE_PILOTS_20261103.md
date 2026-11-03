# R21 — two rejected cache instrumentation pilots (not performance results)

**Question ownership:** [R21 root](../../claims/roots/R21.json), [claim](../../claims/R21-tags-dedup-cache-working-set-knee-v1.json), [pinned script](../../experiments/r21_tags_cache_capacity_abba.py).

## Pilot A: [CI 41651757821](https://github.com/moleus/ClickHouse/actions/runs/41651757821), `INVALID_METHOD`

Both independent GitHub runners generated and inserted exactly 12×4096 = 49152 correct physical SAMPLES values on fresh unmodified stock O1 v11 TimeSeries with 1MiB TAGS cache. The large-cache control had 4096 TAGS rows and 4096 distinct IDs, 49152 TimeRanges rows and 12 SAMPLES parts (merges stopped). However `system.query_log.ProfileEvents` reported no `TimeSeriesTagsDeduplicationCacheHits` or `TimeSeriesTagsDeduplicationCacheMisses` in the INSERT QueryFinish events. Treating this as zero caused an incorrect hard failure. **Do not infer actual zero cache hits from query-log absence.** The complete phase files in GitHub ZIP digests:
- fixed official artifact `13237487242`, SHA256 `5e1ec1ccf082869063d4061f0def34cb9277207a3e63565ea9a7c73b11330571`.
- rotate official artifact `13238495251`, SHA256 `ca72f742e9af0d99aea01a9940564db1dd05584d461b990cf1c06cdb34ae3177`.

## Pilot B: [CI 41652494693](https://github.com/moleus/ClickHouse/actions/runs/41652494693), `INVALID_METHOD`

Corrected to read **global** `system.events` worker-inclusive ProfileEvents before and after the 12 INSERTs. Both runners had identical large-cache physical baseline (4096 distinct TAGS rows, 49152 sample points), and *after* ingest global counters were **45056 hits and 4096 misses**, exactly matching the 144B/entry source model and 1MiB cache. But the *before*-ingest global `system.events` returned **no counter rows**, not explicit zero values. The test falsely required both keys to exist in both snapshots. A fresh server can omit counters not incremented yet; use `start.get(event,0)`, require only both events after traffic and preserve the snapshots. This changes **only the instrumentation validity gate**, not the fixture, cache settings, binary or acceptance threshold.
- fixed official artifact `13238551228`, SHA256 `2fb3c2635635f3ef7aa8391e0f8036603ee0c22ccd7367fbe7eafea102667448`.
- rotate official artifact `13237568223`, SHA256 `6ae2af0dfe8a40f155480d01c372079b6df15a7f5609c48acbd7bf6f2c3ca276`.

**Lesson for other agents:** `ProfileEvents` key absence is not evidence for a zero-cost operation. QueryFinish counters can omit work assigned to pipeline worker threads; `system.events` may omit entries prior to their first increment. Separate QueryFinish-specific CPU from full process tree CPU and query logs. Do not pool any pilot CPU/latency statistics into valid R21 results.

## Next exact gate

[Third R21 CI #41652867042](https://github.com/moleus/ClickHouse/actions/runs/41652867042) reuses the *same* pinned stock O1 binary SHA256 `f0a2fa1f814c58853f43423705cac10ab9df3bfe5887e27ebe9620ba7cb9660e`, same canonical fixture SHA256 `052b2058e7adc280a956b083c9af7d2e636eba6ab2607c204e6f676fcc5d1883`, four fresh phases per order and original A/A ≤25% drift gate. It differs only by correct handling of absent pre-traffic global event keys. No C++ rebuild, no schedules, no production mutation.
