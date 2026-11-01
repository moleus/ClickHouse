#!/usr/bin/env python3
"""Experimental sparse-grid fast path. Apply ONLY to pinned source SHA 52529f0c7115."""
from pathlib import Path

def replace_exact(path: str, old: str, new: str, expected: int = 1):
    p = Path(path)
    s = p.read_text()
    actual = s.count(old)
    if actual != expected:
        raise RuntimeError(f"{path}: expected {expected} instances, found {actual}: {old[:90]!r}")
    p.write_text(s.replace(old, new))

writer = "src/AggregateFunctions/TimeSeries/AggregateFunctionTimeSeriesResultWriter.h"
replace_exact(writer, "#include <optional>", "#include <algorithm>\n#include <optional>")
replace_exact(
    writer,
    """        null_map[row_begin + grid_index] = !result;
    }

private:
    const size_t grid_size;""",
    """        null_map[row_begin + grid_index] = !result;
    }

    /// Used when all windows in a range contain no samples.
    /// The numeric array was already zero-initialized by addRow().
    void storeNulls(size_t first, size_t count)
    {
        chassert(first <= grid_size && count <= grid_size - first);
        std::fill(null_map.begin() + row_begin + first, null_map.begin() + row_begin + first + count, 1);
    }

private:
    const size_t grid_size;""",
)
replace_exact(
    writer,
    """        second_null_map[row_begin + grid_index] = !result;
    }

private:
    /// The Nullable column of the tuple element""",
    """        second_null_map[row_begin + grid_index] = !result;
    }

    void storeNulls(size_t first, size_t count)
    {
        chassert(first <= grid_size && count <= grid_size - first);
        std::fill(first_null_map.begin() + row_begin + first, first_null_map.begin() + row_begin + first + count, 1);
        std::fill(second_null_map.begin() + row_begin + first, second_null_map.begin() + row_begin + first + count, 1);
    }

private:
    /// The Nullable column of the tuple element""",
)
base = "src/AggregateFunctions/TimeSeries/AggregateFunctionTimeseriesBase.h"
start_marker = "    void doInsertResultInto(AggregateDataPtr __restrict place, ResultWriter & writer) const"
end_marker = "    /// Closed range \`[start_time, end_time]\` of timestamps with the scale of the input columns."
p = Path(base)
data = p.read_text()
first = data.index(start_marker)
last = data.index(end_marker, first)
function = data[first:last]
old = """        const auto & buckets = data(place)->buckets;
        auto aggregator = derived().createAggregator(getStackSizeForTwoStacks(buckets.size()));"""
new = """        const auto & buckets = data(place)->buckets;
        if (buckets.empty())
        {
            writer.storeNulls(0, grid_size);
            return;
        }

        /// The grid window must overlap a populated bucket to produce a result.
        /// Find the first and last possible result points by binary search.
        size_t earliest_bucket = bucket_count;
        size_t latest_bucket = 0;
        for (const auto & entry : buckets)
        {
            earliest_bucket = std::min(earliest_bucket, entry.getKey());
            latest_bucket = std::max(latest_bucket, entry.getKey());
        }

        size_t first_grid = 0;
        size_t search_end = grid_size;
        while (first_grid < search_end)
        {
            size_t mid = first_grid + (search_end - first_grid) / 2;
            if (bucketRangeInWindow(mid).second <= earliest_bucket)
                first_grid = mid + 1;
            else
                search_end = mid;
        }

        size_t last_grid = 0;
        search_end = grid_size;
        while (last_grid < search_end)
        {
            size_t mid = last_grid + (search_end - last_grid) / 2;
            if (bucketRangeInWindow(mid).first <= latest_bucket)
                last_grid = mid + 1;
            else
                search_end = mid;
        }

        writer.storeNulls(0, first_grid);
        writer.storeNulls(last_grid, grid_size - last_grid);
        if (first_grid >= last_grid)
            return;

        auto aggregator = derived().createAggregator(getStackSizeForTwoStacks(buckets.size()));"""
if function.count(old) != 1:
    raise RuntimeError("Wrong code version: missing buckets/aggregator initialization")
function = function.replace(old, new)
needle = "for (size_t grid_index = 0; grid_index < grid_size; ++grid_index)"
if function.count(needle) != 2:
    raise RuntimeError(f"Expected two loops, got {function.count(needle)}")
function = function.replace(needle, "for (size_t grid_index = first_grid; grid_index < last_grid; ++grid_index)")
if function.count("size_t next_bucket = 0;") != 1:
    raise RuntimeError("Unexpected dense bucket initialization")
function = function.replace("size_t next_bucket = 0;", "size_t next_bucket = bucketRangeInWindow(first_grid).first;")
p.write_text(data[:first] + function + data[last:])
print("Applied sparse TimeSeries grid experiment to", writer, "and", base)
