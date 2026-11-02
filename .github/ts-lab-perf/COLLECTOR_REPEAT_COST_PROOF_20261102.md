# Actual ClickHouse typed collector: repeated-ID memory/CPU cost surface

2026-11-02 fork lab. No C++ patch. Input is generated via system.numbers_mt, no disk fixture and no extra builds. Binary fixed O2 no jemalloc, identical fastpath/selector/KBN patch blobs as previous O1. Compare 1/4 threads, 8192/65536 input rows per block, new-ID fraction 1%,10%,50%,100%; total rows N=1,330,000 for all cases.

## Source evidence

Pinned ContextTimeSeriesTagsCollector.cpp lines 1201-1269: storeTagsTyped scans every ID under SharedLockGuard and for missing IDs takes exclusive lock, calls tryAddGroupUnlocked(tags), then id_map.emplace. Typed map uses ClickHouse HashMap<IDType,Group,IDMapHash>, not std::unordered_map; group registry uses std::unordered_map<TagsKey,Group>. Software CPU-clock profile 41492330376 included a memset leaf with frames from HashTable::resize to storeTagsTyped, but exclusive contribution unknown.

Mathematical work hypothesis: let N=input rows, f=unique IDs fraction, H=shared lookup cost, I=hash insert/group registration cost, R=map resize/clear cost and L=locking contention. C ~= N*H + N*f*I + R(N*f) + L(f,block,threads). A one-pass under exclusive lock might save N*H at f near1 but lose parallelism at f near0. No conclusion without varying f and block/thread concurrency.

## Workload and falsifiers

Use native stateful SQL functions, not a surrogate hash model:
timeSeriesIdToGroup(timeSeriesStoreTags(UInt64_ID,NULL,'stage',stage_tag))
where UInt64_ID=number modulo D, stage=UInt64_ID modulo38, D=1330000,665000,133000,13300. Repeated IDs always have identical stage labels. Check N output rows and group max38, no exceptions. Compare thread counts 1/4, max_block_size 8192/65536, 3 independent fresh-process trials per case. Save raw timings, tracked memory, process RSS, query-log CPU, disk bytes, CPU identity, case order and evidence of missing metrics. Add no-collector control with the same generated modulo/string work.

H1: at f=100% collector memory/CPU grow with N and map resize. H2: at f=1%, shared lookup might dominate; exclusive one-pass might regress. H3: adaptive strategy is required only if both regimes actually differ. If optimizer suppresses the stateful side effect, group max, memory and query plan must expose it; reject the run.

Native isolated registration is NOT full PromQL performance, does not prove authorization, duplicate conflicting tags, other ID types or dense/long query safety. Later patch must pass end-to-end A/B/A and VM numerical semantics.

Observed smoke on O2: count=3800, max_group=38. Collector reserves group0 for empty tags; stage groups use indices1..38. Fixed benchmark oracle accordingly; previous 41498825170 failure was incorrect test assertion, not O2 bug.
