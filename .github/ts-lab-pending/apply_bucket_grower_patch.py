#!/usr/bin/env python3
"""ClickHouse TimeSeries bucket-map initial capacity experiment.

Pinned source: 52529f0c711566d696917678bf15e7068772fdde.
Changing 16 -> 4/8 cells affects the initial allocation for every
timeSeries*ToGrid state. Does NOT change the key type, hash, serialization
format, bucket mapping, evaluation grid or query semantics.

Large/dense series may pay additional resizing cost. Require same-run
A/B/A with both short-lived 1-sample and regular 120+ samples/series.
"""
import argparse
from pathlib import Path

parser = argparse.ArgumentParser()
parser.add_argument("--initial-size-degree", type=int, required=True, choices=(2,3))
args=parser.parse_args()
p=Path("src/AggregateFunctions/TimeSeries/AggregateFunctionTimeseriesBase.h")
data=p.read_text()
old="""/// Grower for the bucket maps. The stock `HashTableGrower` quadruples the buffer below `max_size_degree`,
/// which leaves the table at a load factor as low as ~0.125; with the whole `Bucket` stored in every cell that
/// wastes a lot of memory across many aggregation states. This one doubles the buffer and fills it up to 3/4:
/// bucket keys are mostly consecutive, and `TrivialHash` puts a run of them in consecutive cells without collisions.
struct TimeSeriesBucketsHashTableGrower : public HashTableGrower<4>"""
new=f"""/// Grower for the bucket maps. The stock `HashTableGrower` quadruples the buffer below `max_size_degree`,
/// which leaves the table at a load factor as low as ~0.125; with the whole `Bucket` stored in every cell that
/// wastes a lot of memory across many aggregation states. This one doubles the buffer and fills it up to 3/4:
/// bucket keys are mostly consecutive, and `TrivialHash` puts a run of them in consecutive cells without collisions.
/// Lab experiment: start with {1 << args.initial_size_degree} cells instead of 16.
/// This reduces per-series allocation for sparse one-bucket states but
/// may require more resizes for dense time series; all results must be
/// checked against the 16-cell baseline before promotion.
struct TimeSeriesBucketsHashTableGrower : public HashTableGrower<{args.initial_size_degree}>"""
if data.count(old)!=1:
    raise RuntimeError("Source mismatch: expected exactly one pinned TimeSeriesBucketsHashTableGrower declaration")
data=data.replace(old,new,1)
old_set="""    void set(size_t num_elems)
    {
        size_degree = 4;
        while (overflow(num_elems))
            ++size_degree;
    }
};"""
new_set=f"""    void set(size_t num_elems)
    {{
        size_degree = {args.initial_size_degree};
        while (overflow(num_elems))
            ++size_degree;
    }}
}};"""
if data.count(old_set)!=1:
    raise RuntimeError("Source mismatch: expected exactly one pinned bucket-map size-reservation block")
data=data.replace(old_set,new_set,1)
p.write_text(data)
print(f"TS_BUCKET_GROWER_PATCH initial_size_degree={args.initial_size_degree} initial_cells={1<<args.initial_size_degree} source={p}")
