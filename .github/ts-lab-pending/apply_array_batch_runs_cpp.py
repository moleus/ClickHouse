#!/usr/bin/env python3
"""Experimental batched array-row insertion for TimeSeries grid aggregates.

Preserves samples, serialized state, all aggregate parameters, -If flags,
and NULL aggregation places. Consecutive rows with the same state are fed
through the already existing bulk array reader. The unmodified binary is
the control for correctness and performance.
"""
from pathlib import Path

path = Path("src/AggregateFunctions/TimeSeries/AggregateFunctionTimeseriesBase.h")
src = path.read_text()
before = """        if (array_of_pairs_argument || array_arguments)
        {
            /// A row of arrays holds a whole series, so the generic path's per-row overhead is amortized.
            Base::addBatch(row_begin, row_end, places, place_offset, columns, arena, if_argument_pos);
            return;
        }
"""
after = """        if (array_of_pairs_argument || array_arguments)
        {
            /// Consecutive array rows for the same state can share one bulk sample
            /// insertion. This avoids a virtual add call and column type casts
            /// for each stored TimeSeries bucket.
            const UInt8 * flags = nullptr;
            if (if_argument_pos >= 0)
                flags = typeid_cast<const ColumnUInt8 &>(*columns[if_argument_pos]).getData().data();

            size_t i = row_begin;
            while (i < row_end)
            {
                AggregateDataPtr place = places[i];
                size_t run_end = i + 1;
                while (run_end < row_end && places[run_end] == place)
                    ++run_end;

                if (place)
                    addBatchSinglePlaceWithFlags<true>(i, run_end, place + place_offset, columns, flags);
                i = run_end;
            }
            return;
        }
"""
if src.count(before) != 1:
    raise RuntimeError(f"Unexpected baseline source: {src.count(before)} matching regions")
path.write_text(src.replace(before, after, 1))
print("LAB_TS_ARRAY_BATCH_RUNS_CPP_APPLIED", path)
