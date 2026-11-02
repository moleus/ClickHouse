#!/usr/bin/env python3
"""Avoid allocating/sorting a bucket-pointer vector for 0/1 bucket states.

Applies only to pinned v11 src/AggregateFunctions/TimeSeries/
AggregateFunctionTimeseriesBase.h. Preserves doInsertResultInto's
sliding-window order, bucketEnd and removeOutOfWindow semantics, including
when the one nonempty bucket is not in the first evaluation window.
Touches no serialization, sample storage, result type or query plan.
All timeSeries*ToGrid functions share this code. Regression/corner tests
and same-run sparse + dense A/B/A MUST pass before any promotion.
"""
from pathlib import Path
p=Path("src/AggregateFunctions/TimeSeries/AggregateFunctionTimeseriesBase.h")
s=p.read_text()
old=r'''        if (use_range_scan)
        {
            size_t next_bucket = 0;'''
new=r'''        /// Many short-lived series and series stored in large time buckets
        /// have zero or one populated grid bucket. The generic sparse path
        /// would allocate a vector, sort it and iterate it even for one cell.
        /// Advance the same sliding aggregator and write every grid position
        /// directly. This also preserves empty-grid (NULL/staleness) behavior.
        if (buckets.size() <= 1)
        {
            const Bucket * single_bucket = nullptr;
            size_t single_bucket_index = 0;
            if (buckets.size() == 1)
            {
                const auto & entry = *buckets.begin();
                single_bucket = &entry.getMapped();
                single_bucket_index = entry.getKey();
            }

            bool consumed = false;
            for (size_t grid_index = 0; grid_index < grid_size; ++grid_index)
            {
                if (single_bucket && !consumed
                    && single_bucket_index < bucketRangeInWindow(grid_index).second)
                {
                    aggregator.add(*single_bucket, bucketEnd(single_bucket_index));
                    consumed = true;
                }
                removeOutOfWindow(aggregator, grid_index);
                writer.store(grid_index, derived().getGridPointResult(aggregator, place, grid_index));
            }
            return;
        }

        if (use_range_scan)
        {
            size_t next_bucket = 0;'''
if s.count(old)!=1:raise RuntimeError(f"Pinned base class changed; exact match {s.count(old)}")
p.write_text(s.replace(old,new,1))
print("LAB_TS_SINGLE_BUCKET_GRID_CPP_APPLIED",p)
