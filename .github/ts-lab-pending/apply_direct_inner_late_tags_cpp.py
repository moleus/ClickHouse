#!/usr/bin/env python3
"""Move native TimeSeries tags join AFTER scalar per-ID/window aggregates.

Apply after apply_direct_inner_grouped_cpp.py, instead of groupArray variant.
Earlier direct grouped plan carried a Map(String,String) through two expensive
GROUP BY keys per every sample timestamp. Here duplicate-timestamp semantics
remain per id/timestamp and window semantics remain per id/grid index; tags
are joined with the resulting rows only once per series/window and grouped
by stage. This preserves the original two-stage average weighting.

Research only: shared owner-managed targets and no recent/time_ranges remain
required by the guarded base patch. Mixed metrics may read more sample rows,
so compare non-Jenkins paths and measure read_rows, RSS, latency.
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
                ),
                indexed AS (
                    SELECT series_id,series_tags,sample_ms,value,
                           intDiv(sample_ms - {} + {} - 1,{}) AS gi FROM dedup
                ),
                per_series AS (
                    SELECT series_id,series_tags,gi,avg(value) AS series_avg
                    FROM indexed
                    WHERE gi >= 0 AND gi < {}
                      AND sample_ms > {} + gi * {} - {}
                      AND sample_ms <= {} + gi * {}
                    GROUP BY series_id,series_tags,gi
                ),
                per_stage AS (
                    SELECT timeSeriesTagsToGroup(
                            if(mapContains(series_tags,'stage'),
                              [tuple('stage',series_tags['stage'])],
                              CAST([],'Array(Tuple(String,String))'))) AS new_group,
                           gi,avg(series_avg) AS result_value
                    FROM per_series
                    GROUP BY new_group,gi
                )'''
new=r'''                src AS (
                    SELECT s.id AS series_id, arrayJoin(s.samples) AS sample
                    FROM {} AS s
                    WHERE s.min_time <= fromUnixTimestamp64Milli({})
                      AND s.max_time > fromUnixTimestamp64Milli({})
                ),
                flattened AS (
                    SELECT series_id,
                           toUnixTimestamp64Milli(tupleElement(sample,1)) AS sample_ms,
                           tupleElement(sample,2) AS sample_v FROM src
                ),
                dedup AS (
                    SELECT series_id,sample_ms,
                           argMax(sample_v,
                             tuple(toUInt8(not isNaN(sample_v)),
                                   if(isNaN(sample_v),0,sample_v))) AS value
                    FROM flattened
                    WHERE reinterpretAsUInt64(sample_v) != 9218868437227405314
                    GROUP BY series_id,sample_ms
                ),
                indexed AS (
                    SELECT series_id,sample_ms,value,
                           intDiv(sample_ms - {} + {} - 1,{}) AS gi FROM dedup
                ),
                per_series AS (
                    SELECT series_id,gi,avg(value) AS series_avg
                    FROM indexed
                    WHERE gi >= 0 AND gi < {}
                      AND sample_ms > {} + gi * {} - {}
                      AND sample_ms <= {} + gi * {}
                    GROUP BY series_id,gi
                ),
                per_stage AS (
                    SELECT timeSeriesTagsToGroup(
                            if(mapContains(t.tags,'stage'),
                              [tuple('stage',t.tags['stage'])],
                              CAST([],'Array(Tuple(String,String))'))) AS new_group,
                           p.gi,avg(p.series_avg) AS result_value
                    FROM per_series AS p
                    INNER JOIN {} AS t FINAL ON p.series_id = t.id
                    WHERE t.metric_name='{}'
                    GROUP BY new_group,p.gi
                )'''
if s.count(old)!=1:raise RuntimeError(f"expected one direct inner SQL template, got {s.count(old)}")
s=s.replace(old,new,1)
old_arguments=r'''            tableName(settings.lab_samples_target_id), tableName(settings.lab_tags_target_id),metric,
            last,start-window,
            start,step,step,points,
            start,step,window,start,step,
            start,step);'''
new_arguments=r'''            tableName(settings.lab_samples_target_id),
            last,start-window,
            start,step,step,points,
            start,step,window,start,step,
            tableName(settings.lab_tags_target_id),metric,
            start,step);'''
if s.count(old_arguments)!=1:raise RuntimeError(f"expected one fmt argument list, got {s.count(old_arguments)}")
s=s.replace(old_arguments,new_arguments,1)
p.write_text(s)
print("LAB_TS_LATE_TAG_JOIN_CPP_APPLIED",p)
