#!/usr/bin/env python3
"""Lab-only correctness fix for UniqV2 on LowCardinality columns.

Current implementation hashes every dictionary entry, including the implicit
default that is not referenced by rows. For a column holding only one
non-default value, this produces NDV=2 instead of NDV=1. Build the sketch from
the distinct *referenced* dictionary positions instead. This adds O(rows)
index scanning during statistics materialization; measure ingest CPU/memory.
"""
from pathlib import Path
p=Path("src/Storages/Statistics/StatisticsUniqV2.cpp")
s=p.read_text()
include="#include <Columns/ColumnLowCardinality.h>"
assert s.count(include)==1
s=s.replace(include,include+"\n#include <Columns/ColumnsNumber.h>")
a="    const IColumn * raw_column_ptr = nullptr;"
b="    ColumnPtr used_dictionary_values;\n"+a
assert s.count(a)==1
s=s.replace(a,b)
old="""    else if (const auto * column_low_cardinality = typeid_cast<const ColumnLowCardinality *>(column.get()))
    {
        raw_column_ptr = column_low_cardinality->getDictionary().getNestedColumn().get();
    }"""
new="""    else if (const auto * column_low_cardinality = typeid_cast<const ColumnLowCardinality *>(column.get()))
    {
        /// The dictionary contains reserved/default and possibly unused values.
        /// Use only indices referenced by actual rows, or NDV can be biased.
        const auto used_positions = column_low_cardinality->getDistinctIndexes(0, column_low_cardinality->size());
        auto positions_column = ColumnUInt64::create();
        auto & positions = positions_column->getData();
        positions.reserve(used_positions.size());
        for (UInt64 position : used_positions)
            positions.push_back(position);
        used_dictionary_values = column_low_cardinality->getDictionary().getNestedColumn()->index(*positions_column, 0);
        raw_column_ptr = used_dictionary_values.get();
    }"""
assert s.count(old)==1
s=s.replace(old,new)
p.write_text(s)
print("LOWCARD_NDV_REFERENCED_DICTIONARY_PATCH_APPLIED")
