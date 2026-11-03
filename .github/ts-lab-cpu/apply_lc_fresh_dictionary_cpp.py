#!/usr/bin/env python3
"""Lab-only LC deserialize fast path: adopt immutable dictionary on first read.

In SerializationLowCardinality read_indexes, the no-global-dictionary path
usually calls uniqueInsertRangeFrom(keys), rebuilding a ReverseIndex hash table.
When the destination is empty, the source dictionary is non-nullable, and
entry zero is the required type default, adopt that dictionary and its indexes
directly. Keep the original path for other cases, including continuous reads.

Correctness for a hand-crafted corrupt dictionary and duplicate keys must be
proved separately; do not deploy this patch without such tests.
"""
from pathlib import Path
p=Path("src/DataTypes/Serializations/SerializationLowCardinality.cpp")
s=p.read_text()
before="""            ColumnPtr keys_column = additional_keys;
            if (low_cardinality_state->null_map)
                keys_column = ColumnNullable::create(additional_keys, low_cardinality_state->null_map);
            low_cardinality_column.insertRangeFromDictionaryEncodedColumn(*keys_column, *indexes_column);"""
after="""            ColumnPtr keys_column = additional_keys;
            if (low_cardinality_state->null_map)
                keys_column = ColumnNullable::create(additional_keys, low_cardinality_state->null_map);

            // Lab-only: the on-disk dictionary is already deduplicated by the writer.
            // The first (reserved) slot must be the type's default value.
            // A fresh column only needs the dictionary and the index array;
            // building ReverseIndex and re-mapping every key adds O(unique_ids)
            // hash work and large zero-initialized allocations.
            if (column_is_empty && !dictionary_type->isNullable()
                && !keys_column->empty() && keys_column->isDefaultAt(0))
            {
                auto unique_dictionary = DataTypeLowCardinality::createColumnUnique(
                    *dictionary_type, IColumn::mutate(keys_column));
                auto local_column = ColumnLowCardinality::create(
                    std::move(unique_dictionary), std::move(indexes_column), /*is_shared=*/true);
                low_cardinality_column.insertRangeFrom(*local_column, 0, num_rows);
            }
            else
                low_cardinality_column.insertRangeFromDictionaryEncodedColumn(*keys_column, *indexes_column);"""
assert s.count(before)==1,"Pinned LC decoder source changed"
s=s.replace(before,after)
p.write_text(s)
print("LC_FRESH_DICTIONARY_FASTPATH_APPLIED",flush=True)
