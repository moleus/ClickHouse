#!/usr/bin/env python3
"""R45: source-pinned compact exclusive inline singleton / TimeSeries bucket map.

Apply after R34 + R35 + R37 + R40 + R44 exact helpers.
No wire-format, planner, selector or stored-part change.
"""
import argparse, difflib, hashlib, importlib.util
from pathlib import Path

PATH = "src/AggregateFunctions/TimeSeries/AggregateFunctionTimeseriesBase.h"
R44_INPUT_BLOB = "7fda3b333ee00979b6e9a561953d97b525bcb83c"

VARIANT_STATE = """    struct State
    {
        /// R45: a state is empty, has one inline sample, OR uses the old
        /// hash map. Never reserve space for both a map object and an
        /// inline sample at the same time.
        struct SingleSample
        {
            UInt64 bucket_index;
            TimestampType timestamp;
            ValueType value;
        };
        using Map = TimeSeriesBucketsMap<Bucket>;
        std::variant<std::monostate, SingleSample, Map> storage;

        bool empty() const { return std::holds_alternative<std::monostate>(storage); }
        const SingleSample * getSingleton() const { return std::get_if<SingleSample>(&storage); }
        const Map * getBucketMap() const { return std::get_if<Map>(&storage); }

        Map & bucketMapForDeserialize()
        {
            if (auto * map = std::get_if<Map>(&storage))
                return *map;
            return storage.template emplace<Map>();
        }

        Map & mutableBucketMap()
        {
            if (auto * map = std::get_if<Map>(&storage))
                return *map;
            if (const auto * one = std::get_if<SingleSample>(&storage))
            {
                const SingleSample copy = *one;
                auto & map = storage.template emplace<Map>();
                map[copy.bucket_index].add(copy.timestamp, copy.value);
                return map;
            }
            return storage.template emplace<Map>();
        }

        Bucket & getBucket(UInt64 bucket_index)
        {
            return mutableBucketMap()[bucket_index];
        }

        void addSample(UInt64 bucket_index, TimestampType timestamp, ValueType value)
        {
            if (auto * map = std::get_if<Map>(&storage))
            {
                (*map)[bucket_index].add(timestamp, value);
                return;
            }
            if (empty())
            {
                storage.template emplace<SingleSample>(SingleSample{bucket_index, timestamp, value});
                return;
            }
            getBucket(bucket_index).add(timestamp, value);
        }

        void addSamples(UInt64 bucket_index, const TimestampType * timestamps, const ValueType * values, size_t count)
        {
            if (count == 0)
                return;
            if (auto * map = std::get_if<Map>(&storage))
            {
                (*map)[bucket_index].addMany(timestamps, values, count);
                return;
            }
            if (count == 1 && empty())
            {
                storage.template emplace<SingleSample>(SingleSample{bucket_index, timestamps[0], values[0]});
                return;
            }
            getBucket(bucket_index).addMany(timestamps, values, count);
        }
    };"""

VARIANT_MERGE = """    void mergeImpl(AggregateDataPtr __restrict place, ConstAggregateDataPtr rhs, Arena *) const override
    {
        auto * state = data(place);
        const auto * other = data(rhs);

        if (const auto * src = other->getSingleton())
        {
            if (state->empty())
            {
                state->storage.template emplace<typename State::SingleSample>(*src);
            }
            else
            {
                Bucket one;
                one.add(src->timestamp, src->value);
                state->getBucket(src->bucket_index).merge(one);
            }
        }

        const auto * rhs_buckets = other->getBucketMap();
        if (!rhs_buckets || rhs_buckets->size() == 0)
            return;

        auto & buckets = state->mutableBucketMap();
        buckets.reserve(buckets.size() + rhs_buckets->size());
        for (const auto & rhs_bucket : *rhs_buckets)
        {
            auto & bucket = buckets[rhs_bucket.getKey()];
            bucket.merge(rhs_bucket.getMapped());
        }
    }"""

VARIANT_SERIAL = """        const auto * state = data(place);
        if (const auto * single = state->getSingleton())
        {
            /// Exactly the original one-bucket wire format.
            writeBinaryLittleEndian(size_t{1}, buf);
            writeBinaryLittleEndian(single->bucket_index, buf);
            Bucket bucket;
            bucket.add(single->timestamp, single->value);
            bucket.serialize(buf);
            return;
        }

        const auto * buckets = state->getBucketMap();
        writeBinaryLittleEndian(buckets ? buckets->size() : size_t{0}, buf);
        if (buckets)
        {
            for (const auto & entry : *buckets)
            {
                writeBinaryLittleEndian(entry.getKey(), buf);
                entry.getMapped().serialize(buf);
            }
        }"""

VARIANT_DESERIAL = """        /// Deserialization keeps the original R43 hash-map wire path.
        /// An old inline sample is discarded if the destination is reused.
        size_t buckets_size = 0;
        readBinaryLittleEndian(buckets_size, buf);"""

VARIANT_RESULT = """        if (!grid_size)
            return;

        const auto * state = data(place);
        if (const auto * single = state->getSingleton())
        {
            /// Exact original derived aggregator for a one-sample bucket.
            Bucket one_bucket;
            one_bucket.add(single->timestamp, single->value);
            auto aggregator = derived().createAggregator(getStackSizeForTwoStacks(1));
            bool consumed = false;
            for (size_t grid_index = 0; grid_index < grid_size; ++grid_index)
            {
                const size_t window_end = bucketRangeInWindow(grid_index).second;
                if (!consumed && single->bucket_index < window_end)
                {
                    aggregator.add(one_bucket, bucketEnd(single->bucket_index));
                    consumed = true;
                }
                removeOutOfWindow(aggregator, grid_index);
                writer.store(grid_index, derived().getGridPointResult(aggregator, place, grid_index));
            }
            return;
        }

        const auto * map = state->getBucketMap();
        if (!map)
        {
            /// The original empty bucket-map path returns NULL for every
            /// grid point, but still runs the derived aggregator machinery.
            auto aggregator = derived().createAggregator(getStackSizeForTwoStacks(0));
            for (size_t grid_index = 0; grid_index < grid_size; ++grid_index)
            {
                removeOutOfWindow(aggregator, grid_index);
                writer.store(grid_index, derived().getGridPointResult(aggregator, place, grid_index));
            }
            return;
        }

        const auto & buckets = *map;"""

DESERIAL_RESERVE_OLD = "        data(place)->buckets.reserve(std::min(buckets_size, MAX_BUCKETS_TO_RESERVE));"
DESERIAL_RESERVE_NEW = """        auto & buckets = data(place)->bucketMapForDeserialize();
        buckets.reserve(std::min(buckets_size, MAX_BUCKETS_TO_RESERVE));"""
DESERIAL_ENTRY_OLD = "            auto & bucket = data(place)->buckets[bucket_index];"
DESERIAL_ENTRY_NEW = "            auto & bucket = buckets[bucket_index];"

def load_helper(path):
    spec = importlib.util.spec_from_file_location("r44_helper", path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod

def blob(raw):
    return hashlib.sha1(b"blob " + str(len(raw)).encode() + b"\0" + raw).hexdigest()

def replace_once(s,old,new,name):
    if s.count(old) != 1:
        raise ValueError(f"R45 {name}: expected exactly one anchor, found {s.count(old)}")
    return s.replace(old,new,1)

def patch(raw,r44_helper):
    got=blob(raw)
    if got!=R44_INPUT_BLOB:
        raise ValueError(f"R45 expected exact R44 input blob {R44_INPUT_BLOB}; got {got}")
    r44=load_helper(r44_helper)
    s=raw.decode()
    for old,new,name in (
        (r44.STATE_NEW,VARIANT_STATE,"variant state"),
        (r44.MERGE_NEW,VARIANT_MERGE,"merge"),
        (r44.SERIAL_NEW,VARIANT_SERIAL,"serialize"),
        (r44.DESERIAL_NEW,VARIANT_DESERIAL,"deserialize"),
        (r44.RESULT_NEW,VARIANT_RESULT,"finalize"),
        (DESERIAL_RESERVE_OLD,DESERIAL_RESERVE_NEW,"deserialize reserve"),
        (DESERIAL_ENTRY_OLD,DESERIAL_ENTRY_NEW,"deserialize entry"),
        ("#include <optional>\n","#include <optional>\n#include <variant>\n","variant include")):
        s=replace_once(s,old,new,name)
    return s

def main():
    p=argparse.ArgumentParser()
    p.add_argument("--source",default=PATH)
    p.add_argument("--r44-helper",required=True)
    p.add_argument("--out",required=True)
    p.add_argument("--apply",action="store_true")
    args=p.parse_args()
    path=Path(args.source);raw=path.read_bytes()
    result=patch(raw,args.r44_helper)
    diff="".join(difflib.unified_diff(raw.decode().splitlines(True),result.splitlines(True),fromfile="a/"+PATH,tofile="b/"+PATH))
    out=Path(args.out);out.parent.mkdir(parents=True,exist_ok=True);out.write_text(diff)
    if args.apply:path.write_text(result)
    print("R45_PATCH_SHA256",hashlib.sha256(diff.encode()).hexdigest())
    print("R45_SOURCE_BLOB",blob(result.encode()))
if __name__=="__main__":
    main()
