#!/usr/bin/env python3
"""LAB ONLY: reduce unrelated sample reads in the v11 late-tags grouped-avg path.

Apply after apply_direct_inner_grouped_cpp.py and
apply_direct_inner_late_tags_cpp.py.
Assumes the canonical v11 two-part ID generator:
(id.first == sipHash64(metric_name)) for ALL historical rows.
This assumption is not safe for modified id_generators, old data written
using different ID rules, or arbitrary external inner target mappings.
DO NOT ENABLE THIS AS A PRODUCTION OPTIMIZATION.
The final tags join still checks metric_name for matching hash collisions.
"""
from pathlib import Path

path=Path("src/Storages/TimeSeries/PrometheusQueryToSQL/Converter.cpp")
src=path.read_text()
old=r"""                    FROM {} AS s
                    WHERE s.min_time <= fromUnixTimestamp64Milli({})
                      AND s.max_time > fromUnixTimestamp64Milli({})"""
new=r"""                    FROM {} AS s
                    WHERE s.id >= tuple(sipHash64('{}'),toUUID('00000000-0000-0000-0000-000000000000'))
                      AND s.id <= tuple(sipHash64('{}'),toUUID('ffffffff-ffff-ffff-ffff-ffffffffffff'))
                      AND s.min_time <= fromUnixTimestamp64Milli({})
                      AND s.max_time > fromUnixTimestamp64Milli({})"""
if src.count(old)!=1:
    raise RuntimeError(f"expected exactly one late-join source, found {src.count(old)}")
src=src.replace(old,new,1)
oldargs=r"""            tableName(settings.lab_samples_target_id),
            last,start-window,"""
newargs=r"""            tableName(settings.lab_samples_target_id),metric,metric,
            last,start-window,"""
if src.count(oldargs)!=1:
    raise RuntimeError(f"expected exactly one fmt argument list, got {src.count(oldargs)}")
src=src.replace(oldargs,newargs,1)
path.write_text(src)
print("LAB_TS_LATE_JOIN_METRIC_ID_RANGE_CPP_APPLIED",path)
