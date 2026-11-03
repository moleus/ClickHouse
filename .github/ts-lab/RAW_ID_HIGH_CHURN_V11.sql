-- ClickHouse TimeSeries v11: tested high-churn ID layout.
-- Laboratory template only. Create a NEW TimeSeries table, do not ALTER a
-- populated production table's ID type or hash expression.
--
-- The reference benchmark uses 1.33m distinct series, 95k new/day.
-- Identical hash semantics versus the stock Tuple(UInt64, LowCardinality(UUID)).
-- Explicitly preserve the default metric_name hash, complete tags hash,
-- TAGS key/value inverted text index, and all inner table ID types.
--
-- Source SHA: 52529f0c711566d696917678bf15e7068772fdde
-- Stock v11 A/B/B/A: https://github.com/moleus/ClickHouse/actions/runs/41596921882
-- DENSE/Sparse: https://github.com/moleus/ClickHouse/actions/runs/41590057398

CREATE TABLE IF NOT EXISTS tslab_high_churn_rawid
ENGINE = TimeSeries
SETTINGS recent_samples_ttl_seconds = 0
SAMPLES INNER COLUMNS (
    id Tuple(UInt64, UUID)
)
TAGS INNER COLUMNS (
    id Tuple(UInt64, UUID) DEFAULT tuple(
        sipHash64(metric_name),
        reinterpretAsUUID(sipHash128(tags))
    ),
    metric_name LowCardinality(String),
    tags Map(LowCardinality(String), String),
    INDEX tags_idx tags TYPE text(tokenizer = 'keyValuePairs') GRANULARITY 100000000
)
TIME RANGES INNER COLUMNS (
    id Tuple(UInt64, UUID)
);

-- Audit generated definitions before use:
-- SHOW CREATE TABLE tslab_high_churn_rawid;
-- SELECT name, type FROM system.columns WHERE database = currentDatabase()
--   AND startsWith(table, '.inner_id.') AND name='id';
-- Validate exact ID hashes and PromQL values against a frozen reference.
