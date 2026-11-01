#!/usr/bin/env python3
"""Lab-only, pinned ClickHouse TimeSeries output preallocation cap.

The writer must still materialize the dense Array(Nullable(T)) contract.
This only limits up-front reserve() allocation; it cannot bound the final
output size or prove an RSS improvement without a paired benchmark.
"""
from pathlib import Path

p=Path("src/AggregateFunctions/TimeSeries/AggregateFunctionTimeSeriesResultWriter.h")
s=p.read_text()
if s.count("#include <optional>")!=1: raise RuntimeError("pinned writer include changed")
s=s.replace("#include <optional>","#include <algorithm>\n#include <optional>",1)

old_numeric="""    void reserve(size_t rows_count)
    {
        offsets.reserve(offsets.size() + rows_count);
        data.reserve(data.size() + rows_count * grid_size);
        null_map.reserve(null_map.size() + rows_count * grid_size);
    }"""
new_numeric="""    void reserve(size_t rows_count)
    {
        /// Do not reserve a full block's dense grid before any row is emitted.
        /// This is only a preallocation cap: addRow() still materializes the
        /// exact number of nullable cells required by the result type.
        constexpr size_t reservation_budget_bytes = 16 * 1024 * 1024;
        constexpr size_t bytes_per_grid_point = sizeof(ResultType) + sizeof(UInt8);
        const size_t max_rows = grid_size ? std::max<size_t>(1, reservation_budget_bytes / bytes_per_grid_point / grid_size) : rows_count;
        const size_t reserve_rows = std::min(rows_count, max_rows);
        offsets.reserve(offsets.size() + rows_count);
        data.reserve(data.size() + reserve_rows * grid_size);
        null_map.reserve(null_map.size() + reserve_rows * grid_size);
    }"""
old_pair="""    void reserve(size_t rows_count)
    {
        offsets.reserve(offsets.size() + rows_count);
        first_data.reserve(first_data.size() + rows_count * grid_size);
        first_null_map.reserve(first_null_map.size() + rows_count * grid_size);
        second_data.reserve(second_data.size() + rows_count * grid_size);
        second_null_map.reserve(second_null_map.size() + rows_count * grid_size);
    }"""
new_pair="""    void reserve(size_t rows_count)
    {
        constexpr size_t reservation_budget_bytes = 16 * 1024 * 1024;
        constexpr size_t bytes_per_grid_point = sizeof(T1) + sizeof(T2) + 2 * sizeof(UInt8);
        const size_t max_rows = grid_size ? std::max<size_t>(1, reservation_budget_bytes / bytes_per_grid_point / grid_size) : rows_count;
        const size_t reserve_rows = std::min(rows_count, max_rows);
        offsets.reserve(offsets.size() + rows_count);
        first_data.reserve(first_data.size() + reserve_rows * grid_size);
        first_null_map.reserve(first_null_map.size() + reserve_rows * grid_size);
        second_data.reserve(second_data.size() + reserve_rows * grid_size);
        second_null_map.reserve(second_null_map.size() + reserve_rows * grid_size);
    }"""
for old,new in ((old_numeric,new_numeric),(old_pair,new_pair)):
    if s.count(old)!=1: raise RuntimeError('pinned C++ writer reserve() body changed')
    s=s.replace(old,new,1)
p.write_text(s)
print('Applied 16MiB preallocation cap for numeric and tuple TimeSeries grid writers')
