# Production-rate Jenkins daily churn: 95,000 new series/day ×14 days

2026-11-02. Proof-before-experiment, lab only. Current VM / vmselect has a 165,000 unique-series cap, but the purpose is to compare algorithmic costs; the lab VM will have `-search.maxUniqueTimeseries=2000000`, and the production 165k limit remains a separately measured policy barrier.

## Data and fidelity
14 consecutive UTC days ×95,000 new unique series each day, exactly one positive Float64 sample/series, 38 stage labels, 8 projects and 20 branches, unique build per day. Total 1,330,000 series/samples. Same fixture is imported via public outer TimeSeries INSERT and VM 1.134.0 `/api/v1/import/prometheus`. Use one SHA256-verified file; do not synthesize separate engine datasets. CH TimeSeries v11 Float64 with recent_samples_ttl=0, O1 v4 same compiled lab binary as prior; VM standalone 6mo retention. No data older than retention.

## Model
Original per-ID 14-step grid needs O(S⋅E)=18.62m grid cells. V4 should create P=S≈1.33m occupied ID/day groups with O(P) group state, versus 400k-series E40 observed v4 memory306.8MB. Linear projection 1.33m/400k×306.8MB≈1.02GB is only a rough *hypothesis*, not a safe upper bound. Hash tables, index sets and merge allocator could show nonlinear jumps or OOM; test.
VM storage reads posting lists and dedup, query memory not directly equal to CH query memory. End-to-end p50/p95 same data and runner, 2 warmups +4 measured for whole, exact stage, project, regex. Query correctness against VM all label/timestamp/value keys. Capture VM process memory and CH parent+child process RSS along with CH query-log peak memory.

## Failure
If CH v4 OOMs / VM exceeds runner memory / cache drifts, record raw error and classify resource failure; do not silently lower dataset size or unique-series limit, and do not declare speedup on failed queries. This is one 14-day, single-node fixture, not proof for 90 days, distributed access, ingestion or production security. Do not promote to production.
