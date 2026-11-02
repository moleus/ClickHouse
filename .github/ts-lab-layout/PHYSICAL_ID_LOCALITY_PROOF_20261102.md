# Pre-registered 1.33m sample TimeSeries locality experiment

Pinned source ClickHouse 52529f0c711566d696917678bf15e7068772fdde, synthetic fixture SHA256 8f3a36846f80d6be5a8604d142e934399f2687094766c57bf105e43977f52858, SHA-verified O2 binary artifact 41467151457. All 1.33m series are of ONE metric; 95k new series/day x14d, one sample/series. Intentional high-cardinality worst case, not representative dense scrape behavior.

## Exact hypothesis

Standard version11 uses id=(sipHash64(metric_name),sipHash128(all tags)), with samples ORDER BY(id,bucket). It ALREADY groups metric_name. Stage=0007 (2.63%) scatters within metric hash band; prior actual EXPLAIN touched 5197/5197 marks despite 35000 matched series.

Test three new ID generators, with matching types in TAGS, SAMPLES and TIME RANGES:

stage = (sipHash64(metric_name),sipHash64(stage),sipHash128(all tags))
project = (sipHash64(metric_name),sipHash64(project),sipHash128(all tags))
project_stage = (sipHash64(metric_name),sipHash64(project),sipHash64(stage),sipHash128(all tags))

The full 128-bit tag hash remains in the last LowCardinality(UUID) field. Numeric hash order groups equal values contiguously; not lexicographic strings. Small native smoke 41502387484 passed all 20/20 standard/stage/project/project-stage/regex numerical output comparisons, after two separate insert batches and OPTIMIZE FINAL. Not yet general PromQL/ACL/dense proof.

## Predict before measuring

N=1330000 physical rows, g=256, about5197 marks (one merged part).
- Exact stage only p=35000/N. Default uniform random in metric band -> about 5190/5197 touched marks. If stage prefix is exploited by primary-key pruning: roughly ceil(35000/256)+part boundary, about137-139 marks.
- Exact project00 p=166250/N. Default nearly all marks. Project prefix: roughly650-652 marks.
- Exact stage0007 and project01: due modulo correlation (38 and 8 have gcd2), only odd project labels occur for stage0007. Expect 14x625=8750 rows, about35-37 marks if both prefixes used.
- With project_stage, stage-only may span four separate project subranges and project-only may span 19 stage subranges; both still approach minimum selected rows plus boundary overhead.
- These are conditional expectations. If ClickHouse IN-set filtering cannot prune the primary key on compound IDs, results may remain 100% marks. Even with skipped marks, TAGS/TimeRanges scans, set building, compression, CPU and seeks may remain the bottleneck.

## Controlled full test

Workflow .github/workflows/ts-lab-layout-compare-20261102.yml starts three independent jobs (stage,project,project_stage) max-parallel3. Every runner imports the same immutable fixture into standard and one candidate using the identical SHA-verified O2 binary. Run OPTIMIZE FINAL on both before timing, stop merges, verify physical parts stability and >20% disk free before and after.
Five full native prometheusQueryRange selectors: whole metric, exact stage, exact project, combined exact stage+project, branch regex.
For each selector, A/B/B/A independent fresh-process phase order, 2 warmups +4 timed queries per phase; output keys, stage, timestamps, values identical to original. QueryLog CPU/query memory, peak process RSS, read_rows/read_bytes, p50/p95 and all individual trials preserved. Direct physical SAMPLES id IN matching TAGS sample reads and EXPLAIN indexes included for diagnosis only. Matrix jobs have 50min timeout, no cron, no cancellation of other workflows.

A start/end baseline drift over30% invalidates comparison and needs repeat. Four trials per phase do not prove production p95 tails or significance. No production promotion. Even positive sparse gains must be retested with 15-second dense scrape, high churn, duplicates, late samples, IEEE nonfinite values, ACL, compression, merge write amplification and ingest rates.

## Optimization criterion

For layout L and workload q, choose the Pareto frontier of minimized weighted CPU seconds, wall p95, query memory, disk read and storage/ingest work, subject to full semantic and tenant-security equivalence. Weights come from actual measured query execution volume, NOT from static counts of dashboard panels. The public Grafana dashboard sample can only prioritize hypotheses. Treat each current label stage/project as a synthetic proxy for possible production region/cluster/job grouping, NOT a recommendation of a production key order.

A new ID format means new internal table schema and a migration. It is not a safe in-place ALTER of a populated production table. Multiple parts/partitions/replicas are not globally contiguous, only individually sorted by key.
