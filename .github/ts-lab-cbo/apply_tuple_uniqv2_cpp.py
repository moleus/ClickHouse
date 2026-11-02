#!/usr/bin/env python3
"""Opt-in source experiment: let uniq_v2 statistics accept Tuple columns.

The collector already uses uniqCombined64 and a SQL smoke demonstrates
uniqCombined64(Tuple) works. We only expand the statistics type validator.
It does not add default stats or change data representation. This is an
isolated lab patch, not a production recommendation.
"""
from pathlib import Path
path=Path("src/Storages/Statistics/StatisticsUniqV2.cpp")
s=path.read_text()
old_include="#include <DataTypes/DataTypeLowCardinality.h>"
new_include=old_include+"\n#include <DataTypes/DataTypeTuple.h>\n#include <Common/typeid_cast.h>"
assert s.count(old_include)==1, "Pinned source include changed"
s=s.replace(old_include,new_include)
old="return inner_data_type->isValueRepresentedByNumber() || isStringOrFixedString(inner_data_type);"
new=("return inner_data_type->isValueRepresentedByNumber() || isStringOrFixedString(inner_data_type) "
     "|| typeid_cast<const DataTypeTuple *>(inner_data_type.get()) != nullptr;")
assert s.count(old)==1, "Pinned UniqV2 validator changed"
s=s.replace(old,new)
path.write_text(s)
print("CBO_UNIQ_V2_TUPLE_VALIDATOR_PATCH_APPLIED")
