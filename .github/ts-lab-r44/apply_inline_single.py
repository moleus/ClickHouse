#!/usr/bin/env python3
"""R44 source-pinned inline singleton sample for TimeSeries aggregate bucket state.

Apply only AFTER the exact R34 helper. Source verification restores R34 OLD
and verifies stock Git blob, so R44 cannot silently patch an arbitrary header.
"""
import argparse
import difflib
import hashlib
import importlib.util
from pathlib import Path

PATH = "src/AggregateFunctions/TimeSeries/AggregateFunctionTimeseriesBase.h"

STATE_OLD = """    struct State
    {
        /// Maps bucket index to the set of all timestamps and values
        TimeSeriesBucketsMap<Bucket> buckets;
    };"""
STATE_NEW = """    struct State
    {
        /// R44: keep the first input sample directly in the aggregation state.
        /// A singleton never allocates a bucket map. On its second sample the
        /// state spills to the unchanged R34 hash map and stays there.
        struct SingleSample
        {
            UInt64 bucket_index;
            TimestampType timestamp;
            ValueType value;
        };

        TimeSeriesBucketsMap<Bucket> buckets;
        std::optional<SingleSample> singleton;

        void spillSingleton()
        {
            if (!singleton)
                return;
            const auto old = *singleton;
            buckets[old.bucket_index].add(old.timestamp, old.value);
            singleton.reset();
        }

        Bucket & getBucket(UInt64 bucket_index)
        {
            spillSingleton();
            return buckets[bucket_index];
        }

        void addSample(UInt64 bucket_index, TimestampType timestamp, ValueType value)
        {
            if (!singleton && buckets.size() == 0)
            {
                singleton.emplace(SingleSample{bucket_index, timestamp, value});
                return;
            }
            getBucket(bucket_index).add(timestamp, value);
        }

        void addSamples(UInt64 bucket_index, const TimestampType * timestamps, const ValueType * values, size_t count)
        {
            if (count == 0)
                return;
            if (count == 1 && !singleton && buckets.size() == 0)
            {
                singleton.emplace(SingleSample{bucket_index, timestamps[0], values[0]});
                return;
            }
            getBucket(bucket_index).addMany(timestamps, values, count);
        }
    };"""

MERGE_OLD = """    void mergeImpl(AggregateDataPtr __restrict place, ConstAggregateDataPtr rhs, Arena *) const override
    {
        auto & buckets = data(place)->buckets;
        const auto & rhs_buckets = data(rhs)->buckets;
        buckets.reserve(rhs_buckets.size());
        for (const auto & rhs_bucket : rhs_buckets)
        {
            auto & bucket = buckets[rhs_bucket.getKey()];
            bucket.merge(rhs_bucket.getMapped());
        }
    }"""
MERGE_NEW = """    void mergeImpl(AggregateDataPtr __restrict place, ConstAggregateDataPtr rhs, Arena *) const override
    {
        auto * state = data(place);
        const auto * other = data(rhs);

        if (other->singleton)
        {
            const auto & src = *other->singleton;
            if (!state->singleton && state->buckets.size() == 0)
            {
                state->singleton = src;
            }
            else
            {
                Bucket one;
                one.add(src.timestamp, src.value);
                state->getBucket(src.bucket_index).merge(one);
            }
        }

        const auto & rhs_buckets = other->buckets;
        if (rhs_buckets.size() == 0)
            return;

        state->spillSingleton();
        auto & buckets = state->buckets;
        buckets.reserve(buckets.size() + rhs_buckets.size());
        for (const auto & rhs_bucket : rhs_buckets)
        {
            auto & bucket = buckets[rhs_bucket.getKey()];
            bucket.merge(rhs_bucket.getMapped());
        }
    }"""

SERIAL_OLD = """        writeBinaryLittleEndian(data(place)->buckets.size(), buf);

        for (const auto & entry : data(place)->buckets)
        {
            writeBinaryLittleEndian(entry.getKey(), buf);
            entry.getMapped().serialize(buf);
        }"""
SERIAL_NEW = """        const auto * state = data(place);
        if (state->singleton)
        {
            /// Same wire format as one bucket in the original hash map.
            writeBinaryLittleEndian(size_t{1}, buf);
            const auto & single = *state->singleton;
            writeBinaryLittleEndian(single.bucket_index, buf);
            Bucket bucket;
            bucket.add(single.timestamp, single.value);
            bucket.serialize(buf);
            return;
        }

        writeBinaryLittleEndian(state->buckets.size(), buf);
        for (const auto & entry : state->buckets)
        {
            writeBinaryLittleEndian(entry.getKey(), buf);
            entry.getMapped().serialize(buf);
        }"""

DESERIAL_OLD = """        size_t buckets_size = 0;
        readBinaryLittleEndian(buckets_size, buf);"""
DESERIAL_NEW = """        /// Deserialization keeps the original hash-map path and format.
        data(place)->singleton.reset();
        size_t buckets_size = 0;
        readBinaryLittleEndian(buckets_size, buf);"""

RESULT_OLD = """        if (!grid_size)
            return;

        const auto & buckets = data(place)->buckets;"""
RESULT_NEW = """        if (!grid_size)
            return;

        const auto * state = data(place);
        if (state->singleton)
        {
            /// No hash lookup, key scan or sorted temporary vector for a single
            /// sample. The ordinary per-function aggregator still computes the
            /// grid, including stale/null windows and counter rate semantics.
            const auto & single = *state->singleton;
            Bucket one_bucket;
            one_bucket.add(single.timestamp, single.value);
            auto aggregator = derived().createAggregator(getStackSizeForTwoStacks(1));
            bool consumed = false;
            for (size_t grid_index = 0; grid_index < grid_size; ++grid_index)
            {
                const size_t window_end = bucketRangeInWindow(grid_index).second;
                if (!consumed && single.bucket_index < window_end)
                {
                    aggregator.add(one_bucket, bucketEnd(single.bucket_index));
                    consumed = true;
                }
                removeOutOfWindow(aggregator, grid_index);
                writer.store(grid_index, derived().getGridPointResult(aggregator, place, grid_index));
            }
            return;
        }

        const auto & buckets = state->buckets;"""

ADD_OLD = """        auto & bucket = data(place)->buckets[bucket_index];
        bucket.add(timestamp, value);"""
ADD_NEW = """        data(place)->addSample(bucket_index, timestamp, value);"""
ADDMANY_OLD = "state->buckets[sample_class.bucket_index].addMany(timestamps + i, values + i, count);"
ADDMANY_NEW = "state->addSamples(sample_class.bucket_index, timestamps + i, values + i, count);"

def git_blob(raw: bytes) -> str:
    return hashlib.sha1(b"blob " + str(len(raw)).encode() + b"\0" + raw).hexdigest()

def replace_exact(s, old, new, name):
    hits = s.count(old)
    if hits != 1:
        raise ValueError(f"{name}: expected exactly one anchor; found {hits}")
    return s.replace(old, new, 1)

def source_pinned_r34(raw, helper_path):
    spec=importlib.util.spec_from_file_location("r34_helper",helper_path)
    module=importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    original=raw.decode()
    if original.count(module.NEW) != 1:
        raise ValueError("R44 input is not the exact R34 grower change")
    stock=original.replace(module.NEW,module.OLD,1)
    got=git_blob(stock.encode())
    if got != module.BLOB:
        raise ValueError(f"R44 expected source {module.BLOB}; got {got}")
    return original

def patch(raw,helper_path):
    s=source_pinned_r34(raw,helper_path)
    for old,new,name in (
        (STATE_OLD,STATE_NEW,"state"),
        (MERGE_OLD,MERGE_NEW,"merge"),
        (SERIAL_OLD,SERIAL_NEW,"serialize"),
        (DESERIAL_OLD,DESERIAL_NEW,"deserialize"),
        (RESULT_OLD,RESULT_NEW,"doInsertResultInto"),
        (ADD_OLD,ADD_NEW,"scalar-add"),
        (ADDMANY_OLD,ADDMANY_NEW,"batch-add")):
        s=replace_exact(s,old,new,name)
    return s

def main():
    p=argparse.ArgumentParser()
    p.add_argument("--source",default=PATH)
    p.add_argument("--r34-helper",required=True)
    p.add_argument("--out",required=True)
    p.add_argument("--apply",action="store_true")
    args=p.parse_args()
    file=Path(args.source);raw=file.read_bytes();candidate=patch(raw,args.r34_helper)
    diff="".join(difflib.unified_diff(raw.decode().splitlines(True),candidate.splitlines(True),
                                     fromfile="a/"+PATH,tofile="b/"+PATH))
    out=Path(args.out);out.parent.mkdir(parents=True,exist_ok=True);out.write_text(diff)
    if args.apply:file.write_text(candidate)
    print("R44_PATCH_SHA256",hashlib.sha256(diff.encode()).hexdigest())
    print("R44_SOURCE_BLOB",git_blob(candidate.encode()))

if __name__=="__main__":
    main()
