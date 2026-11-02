# TimeSeries v4 authorization and tag row-policy equivalence — proof before experiment

2026-11-02. Pinned ClickHouse source `52529f0c711566d696917678bf15e7068772fdde`. Standalone CI server only, no production access control changes.

## Source facts
- `src/Storages/TimeSeries/PrometheusQueryToSQL/fromSelector.cpp` creates a `timeSeriesSelector` table function through AST literals containing the complete selector.
- v4 uses the **same** `timeSeriesSelector` with full parsed selector text escaped by `quoteString`, then `timeSeriesIdToGroup` and `timeSeriesGroupToTags` like the normal converter.
- `StorageTimeSeriesSelector::readImpl` builds a query over the target TAGS/SAMPLES tables, executes it via `InterpreterSelectQueryAnalyzer` using a modified copy of the caller's context, then adds `query_info.storage_limits`. The source does not constitute a formal proof that all row policies apply; **test with a non-admin user and a real policy**.
- v11 inner TAGS is a `ReplacingMergeTree` with `tags Map(LowCardinality(String),String)`; its inner name has prefix `.inner_id.tags.` (see `StorageTimeSeries.cpp` inline engine documentation).

## Falsification contract
In an isolated ClickHouse server with SQL access management enabled, create public `access_ts ENGINE=TimeSeries` with one metric and two otherwise similar series:
- `project=allowed,stage=stage-allowed,build=a`
- `project=denied,stage=stage-denied,build=b`

Create user `lab_reader` (no password, localhost-only) and grant read permission to the isolated default database. Find the physical inner TAGS target (only one exists); create a **permissive SELECT row policy on TAGS** that allows only `tags['project']='allowed'` to `lab_reader`. Verify a raw direct SELECT from TAGS as `lab_reader` returns only the allowed row; without this precondition, the test cannot establish policy semantics and must fail.

For the reader, compare original baseline / compiled v4 opt-in / v4 opt-out / original baseline on `avg by(stage)(avg_over_time(metric[1d]))` with broad selector, project allowed/denied filters and one-day window. Every phase must return `stage-allowed` only, never `stage-denied`, and the raw TAGS policy must remain in force. Include a control as admin showing both project stages exist. Store raw SQL/errors and query outputs. Query-by-query exact labels/time/value is required; a differing authorization error is a material finding, not a silent skip.

Potential alternate outcome: the TimeSeries functions require explicit grants on underlying target tables; then grant them deliberately in the isolated test. Neither user-only nor row-policy results should be assumed from code comments.

No upstream/master writes, no password or other credentials and no scheduled CI. If the server cannot enforce the policy, classify the experiment as **inconclusive**, not as ACL success.
