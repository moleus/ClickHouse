# R25 — VictoriaMetrics tag-filter cache capacity, source-pinned diagnostic

Status: **INVALID_METHOD for whole 4-selector latency comparison**; no CPU/latency speedup accepted. Both attempts and raw GitHub artifacts retained. This is E04-only VM v1.134.0 (source `eaf7a68c92c8069a67ae02e601e81453a5e002de`) without C++ compilation or ClickHouse changes.

## Frozen design

- Same optimized release archive SHA256 `cd6e109bae85ff7c138d838b32be9a7b3a387a7cf9234f22991da10`, R01 aligned fixture SHA256 `ea9a8d43c9e58ed916f1bf43949556d8ed18169ddd14033c293af4cd54c9881d`, 28,896 time series, 2,488,320 points.
- One baseline imported VM data directory, clean shutdown, cloned for each A/B/B/A fresh process. Compare `-storage.cacheSizeIndexDBTagFilters=1` (low) versus `67108864` (high). All other flags fixed including `-search.disableCache`, `nocache=1`, `-search.maxUniqueTimeseries=165000`, `-memory.allowedBytes=4294967296`.
- 8 warmups +48 untraced native query-range requests per selector and phase; full labels/timestamps/numeric parity asserted; querytracer `trace=1` only **after** timed requests. CPU from whole VM process `/proc/PID/stat`, not isolated query-log time.
- The native 1d range evaluation has three grid points; cases: node broad, namespace, pod broad, 15-second scrape.

## Attempt 1 — invalid assumption, no gain claim

[CI 41668987185](https://github.com/moleus/ClickHouse/actions/runs/41668987185) and [raw artifact](https://github.com/moleus/ClickHouse/actions/runs/41668987185/artifacts/13247121742), artifact digest `sha256:e9cd63338ca8b12caf40de57b37f666d6c95e15561c3f0581bbef2e0be8e1b84`.

After the first low-cache node-broad 48/48 metric requests were misses, the namespace query returned correct nonempty output but its `vm_cache_requests_total` did not rise. The script incorrectly required positive increments for every selector and stopped. A zero metric increment is not proof of a cache-free execution; do not treat this attempt as a timing comparison.

## Attempt 2 — complete timed phases, but not an admissible global speedup

[CI 41669600149](https://github.com/moleus/ClickHouse/actions/runs/41669600149) and [full raw artifact](https://github.com/moleus/ClickHouse/actions/runs/41669600149/artifacts/13247152411), digest `sha256:662989aa4b6927b2b67745976582a2b29096289eef734987464bbce14a2c5c46`.

All 16 selector/phase combinations produced nonempty matching full output and 48 measured HTTP requests each; querytracer captured cache lookups. The `low` 1-byte cache had zero retained entries; the 64MiB cache held 1–3 entries and trace messages indicated cached matching IDs on repeated queries. **Metrics counters are insufficient by themselves**: in some cases `vm_cache_requests_total` was unchanged despite a trace of `search for metricIDs in tag filters cache` / cache-miss path. The exact cause of this counter/view difference has not been established.

| Native selector | Low cache mean phase p50 | 64MiB cache mean phase p50 | Low/high ratio | Caveat |
|---|---:|---:|---:|---|
| node_broad | 15.158ms | 14.307ms | 1.059x | Low A/A p50 drift 24.0%; weak effect and runner/process variation |
| pod_namespace | 1.854ms | 2.025ms | 0.916x | **A/A drift 33.8%**, invalid timing |
| pod_broad | 25.848ms | 27.139ms | 0.952x | Opposite direction, not a universal cache benefit |
| burst15 | 0.625ms | 0.617ms | 1.013x | Tiny timing, coarse CPU ticks |

The above numbers are **diagnostic raw measurements**, not validated improvement claims. The R25 preregistered whole-matrix gate fails because of namespace A/A drift and cache-counter separation not observed in every phase. Do not average these ratios into a claimed acceleration or a CI success. No production cache-size recommendation follows.

## Technical correction and next gate

1. The VM [per-query querytracer](https://github.com/VictoriaMetrics/VictoriaMetrics/blob/v1.134.0/app/vmselect/main.go#L131-L170) can show index cache hit/miss and actual per-day index access, even when the aggregate exporter counter does not change as expected. Store trace text and corresponding metric deltas separately; do not equate `0 counter increments` with `no lookup`.
2. The VM [index cache budget](https://github.com/VictoriaMetrics/VictoriaMetrics/blob/v1.134.0/app/vmstorage/main.go#L70-L80) is configurable, and [LRU implementation](https://github.com/VictoriaMetrics/VictoriaMetrics/blob/v1.134.0/lib/lrucache/lrucache.go#L266-L286) evicts entries above its per-shard byte cap.
3. A future R25 follow-up must use a stable test runner and separately trace hit/miss rates and CPU before claiming benefit. Do not duplicate ongoing E03/R06. Current priority shifts to the independent R26 **index path** source flag experiment rather than another cache capacity rerun.
