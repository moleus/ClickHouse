# R33 — audit of the *real* Grafana protocol and Jenkins stage-series workload

Date: 2026-11-04. Owner: chatgpt-r33-grafana-real-query-fixture-parity-20261104. [Canonical claim](../ts-lab/claims/roots/R33.json). This is **E06 workload validation**, not E05 R32 single-bucket finalization and not a license to replace a C++ query plan.

## Critical defects in the historical 1.33m / 14d fixture

The old [generator](../ts-lab-bench/generate_95k_daily_churn_ts.py) produces `95,000 * 14` unique series but **95,000 distinct build label values per day**. Each build thus has exactly *one* stage. Real Jenkins-style stage timing, under the stated 2,474 builds/day ×~38 stages model, needs about **2,500 distinct build IDs/day ×38 stage series/build**, still exactly 95k series/day. The old data matches global series cardinality while violating the entity relationship that controls label compression, matching, grouping and multiselect.

Its labels are `build,stage,branch,project`, not the intended `app,build,branch,jenkins_job,project,stage,result`. The project label is synthetic and may not exist in a specific Grafana datasource; the actual panel/label census must be supplied separately before calling it an exact prod profile.

Old modular arithmetic is worse than a simple random-seed bias:

- `stage=i%38`, `branch=feature-(i%19)`. Since 38 is a multiple of 19, each fixed stage predicts **exactly one** non-main feature branch. The query selectivity model for `stage` AND `branch` is broken.
- `project=i%8` and `stage=i%38`. Both have the parity of `i`. **Half** of the stage/project combinations are mathematically impossible, even though production job/project assignments need not follow stage parity. Each stage sees only four of eight projects, rather than eight.
- `build=day-{day}-build-{i}` is unique for every stage row. An actual build label should repeat across the build's 38 stage observations.
- Every series has one sample. This is a valid **sparse CI stage event** profile, not a generic scraped metrics workload with repeated 15s/1m samples, resets and staleness. Keep the two workloads distinct.
- All daily samples are intentionally placed inside the UTC day. The old `query_range` ends at midnight and steps exactly 24h. Other query alignments, partial days and stage completion after midnight are untested.

## Query mismatch: SQL-only versus Grafana

Prior R30/R31 usually ran standalone **generated ID-selection SQL**, often `SELECT count(), uniqExact(id) FROM (SELECT timeSeriesStoreTags(...))`. This is not a whole PromQL query, let alone Grafana HTTP request. The even larger R31 SQL checks only two aggregate 64-bit hashes and 64 complete samples; exact native result equality is not proved by those hashes.

The existing full native SQL call uses `prometheusQueryRange(table, expr, start, end, 86400)` with mostly `stage=` and `project=` selectors and fixed `avg by(stage)(avg_over_time(metric[1d]))`. It does not test how Grafana resolves multi-value variables (`=~`, regex alternation), min step, range vs instant, request alignment, shift by 6h, or HTTP label lookup.

Grafana Prometheus datasource uses **HTTP** `/api/v1/query_range` for Range, `/api/v1/query` for Instant, and in Both mode can issue both. Query Inspector shows the expanded expression and the exact `start/end/step` sent after alignment. `start` and `end` in Prometheus range API are both inclusive. The panel `$__interval` depends on range and panel width; it is not always 1d. Multi-value and All variables require regex matching (not simple equality). See [Grafana query editor](https://grafana.com/docs/grafana/latest/datasources/prometheus/query-editor/), [variables](https://grafana.com/docs/grafana/latest/datasources/prometheus/template-variables/) and [Prometheus HTTP API](https://prometheus.io/docs/prometheus/latest/querying/api/).

Important semantic distinction: `avg by(stage)(metric)` with one sparse sample per series uses the **instant-vector lookback** (5m Prometheus default); `avg by(stage)(avg_over_time(metric[1d]))` includes points from the preceding day. These are not equivalent queries, and a 1d range does not compensate for changing the original PromQL. R33 tests both but does **not assert** which one is the actual production panel until an inspector request is captured. See [Prometheus staleness](https://prometheus.io/docs/prometheus/latest/querying/basics/).

ClickHouse 26.9+ implements Prometheus endpoints but an isolated server with a hand-written minimal XML config does **not automatically register** `prometheus_api_v1`. The first R33 smoke failed HTTP 404 while SQL worked ([CI 41728467999](https://github.com/moleus/ClickHouse/actions/runs/41728467999)). An explicit `http_handlers` rule fixed routing; then the handler with statically configured database/table rejected Grafana's `database` custom query parameter. Configure **one** of these, not both: fixed handler database/table without query overrides; or dynamic handler with database/table supplied by `customQueryParameters`. The R33 test uses the latter. See [ClickHouse 26.9 release call](https://presentations.clickhouse.com/2026-release-26.9/) and [PromQL HTTP integration](https://clickhouse.com/blog/introducing-promql).

## Controlled replacement and exact invariants

[R33 generator](generate_grafana_ci_v2.py), [unit tests](test_grafana_workload.py), [resolved Grafana request catalog](grafana_query_catalog.py), [SQL-versus-HTTP full-output oracle](bench_grafana_http_parity.py) and [isolated CI smoke](../workflows/ts-lab-r33-grafana-api-smoke-20261104.yml).

- Same 95,000 new series/day = **2500 builds × 38 stages** in full profile. `build`, `branch`, `jenkins_job`, `app` and `project` remain constant within a build; stage and stage result vary per stage. Build/branch/job association is deliberate, while stage and branch/project are no longer tied by i%19 or stage parity. This is a **modeled profile**: labels and values must be compared to production metrics by an authorized source, not asserted from thin air.
- Source SHA and fixture SHA pinned independently; old R31 fixture remains byte-for-byte unchanged for historical comparisons. Every build must have precisely 38 distinct stages, daily and total counts exact, every stage spans all eight projects in the full 2500-build profile; no placeholder or all-constant labels.
- First smoke: 14d ×64 builds/day ×38 = 34,048 series. Full capacity target: 14d ×2500 ×38 = **1,330,000**.
- Eight concrete range requests: unfiltered 14d, branch+job regex, stage+project regex, negative result matcher, All regex, shifted 6h, last-day hourly and sparse instant-vector daily. Separate `/query`, `/labels`, `/label/stage/values`, `/label/jenkins_job/values`, `/series` probes. All URL parameters and expanded PromQL stored with evidence.
- For each range case, normalize and compare **every returned (label set, timestamp, value)** from ClickHouse's Prometheus HTTP endpoint and ClickHouse's native SQL `prometheusQueryRange` on the same unmodified stock O2 binary. Empty sparse instant vectors are valid and must not be artificially filled.
- Record error HTTP codes and bodies, request time, output points, bytes, time alignment and physical counts. Do not call this a VictoriaMetrics oracle: HTTP and SQL inside the same ClickHouse server share the PromQL implementation.

## What is required for an actual Grafana parity claim

Capture one production panel **Query Inspector > Query** response including the resolved PromQL, endpoint method, `start/end/step`, range/instant/Both type, datasource Prometheus URL and variable selections. Export a **redacted** dashboard panel JSON and a label census for the benchmarked metric, at least cardinalities of `build`, `stage`, `jenkins_job`, `branch`, `result` and app/project. No tokens or private endpoint credentials should be committed. Until this exists, all R33 profiles are synthetic but structurally better than the old source.

Do not promote R30 ANY or R31 IN from synthetic ID-stage results alone. The new suite establishes transport and semantic correctness; separate matched 1.33m/14d, 1/4/8-client process RSS and write/merge experiments remain mandatory.
