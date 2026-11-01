#!/usr/bin/env python3
"""Replace costly generic duplicate timestamp GROUP BY with the native TS aggregate.

Run AFTER apply_direct_inner_grouped_cpp.py on the pinned v11 source.
timeSeriesGroupArray(samples) is the SAME dedup/sort aggregate used by
the TimeSeries Samples table (handles duplicate timestamps, NaNs,
out-of-order input and merging rows). It produces one merged array per
series id, which is then expanded for disjoint per-window AVG.
Benchmark memory: 32 inline elements per live ID may cost more than
scalar GROUP BY at extreme cardinality; do not promote without A/B/A.
"""
from pathlib import Path

p=Path("src/Storages/TimeSeries/PrometheusQueryToSQL/Converter.cpp")
s=p.read_text()
old=r'''                src AS (
                    SELECT s.id AS series_id, t.tags AS series_tags,
                           arrayJoin(s.samples) AS sample
                    FROM {} AS s
                    INNER JOIN {} AS t FINAL ON s.id=t.id
                    WHERE t.metric_name='{}'
                      AND s.min_time <= fromUnixTimestamp64Milli({})
                      AND s.max_time > fromUnixTimestamp64Milli({})
                ),
                flattened AS (
                    SELECT series_id,series_tags,
                           toUnixTimestamp64Milli(tupleElement(sample,1)) AS sample_ms,
                           tupleElement(sample,2) AS sample_v FROM src
                ),
                dedup AS (
                    SELECT series_id,series_tags,sample_ms,
                           argMax(sample_v,
                             tuple(toUInt8(not isNaN(sample_v)),
                                   if(isNaN(sample_v),0,sample_v))) AS value
                    FROM flattened
                    WHERE reinterpretAsUInt64(sample_v) != 9218868437227405314
                    GROUP BY series_id,series_tags,sample_ms
                ),'''
new=r'''                src AS (
                    SELECT s.id AS series_id, any(t.tags) AS series_tags,
                           timeSeriesGroupArray(s.samples) AS merged_samples
                    FROM {} AS s
                    INNER JOIN {} AS t FINAL ON s.id=t.id
                    WHERE t.metric_name='{}'
                      AND s.min_time <= fromUnixTimestamp64Milli({})
                      AND s.max_time > fromUnixTimestamp64Milli({})
                    GROUP BY s.id
                ),
                flattened AS (
                    SELECT series_id,series_tags,
                           arrayJoin(merged_samples) AS sample
                    FROM src
                ),
                dedup AS (
                    SELECT series_id,series_tags,
                           toUnixTimestamp64Milli(tupleElement(sample,1)) AS sample_ms,
                           tupleElement(sample,2) AS value
                    FROM flattened
                    WHERE reinterpretAsUInt64(tupleElement(sample,2)) != 9218868437227405314
                ),'''
n=s.count(old)
if n!=1:raise RuntimeError(f"Missing exact direct-inner target SQL template: {n}")
p.write_text(s.replace(old,new))
print("LAB_TS_NATIVE_GROUP_ARRAY_MERGE_APPLIED",p)
