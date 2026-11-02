# TimeSeries layout results: automatic failure-aware digest

Six independent synthetic high-cardinality selective comparisons, two full-query results and public label corpus.
Prior broad high-cardinality candidate IDs all reached OOM; sparse wins cannot authorize production.

| ID type | Fastpath setting | All controls passed | Stage x | Project x | Both x | Regex x |
|---|---|---|---:|---:|---:|---:|
| stage | 0 | True | 2.58x | 1.20x | 3.31x | 0.98x |
| stage | 1 | True | 2.94x | 1.18x | 3.90x | 0.96x |
| project | 0 | True | 1.31x | 1.36x | 2.55x | 0.99x |
| project | 1 | True | 1.61x | 1.77x | 2.61x | 0.99x |
| project_stage | 0 | True | 2.84x | 1.49x | 2.97x | 0.97x |
| project_stage | 1 | True | 2.53x | 1.49x | 3.06x | 0.91x |

## Stable 15s dense sample data (bulk-ingested rows)

| Query | Original ms | Candidate ms | Speedup |
|---|---:|---:|---:|
| cluster | 49.037 | 46.419 | 1.056x |
| both | 29.919 | 21.413 | 1.397x |
| whole | 104.868 | 112.638 | 0.931x |
| namespace | 33.084 | 26.284 | 1.259x |

## Result status

All six sparse jobs passed: True. Errors: 0.
All raw per-trial values and EXPLAIN remain in source CI artifacts.
Public Grafana dashboard panel counts are not query traffic weights.

## CI runs
- sparse: https://github.com/moleus/ClickHouse/actions/runs/41505253205
- dense: https://github.com/moleus/ClickHouse/actions/runs/41504786378
- mark_model: https://github.com/moleus/ClickHouse/actions/runs/41503557195
- public_corpus: https://github.com/moleus/ClickHouse/actions/runs/41502908657
