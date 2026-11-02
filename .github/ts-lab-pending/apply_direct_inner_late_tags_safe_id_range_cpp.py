#!/usr/bin/env python3
"""LAB ONLY: complete metric-ID range with a residual tags-ID lookup.

Apply after grouped, late-tags, and ID-range C++ patch scripts. The fast
canonical metric-ID range is a superset of canonical IDs; historical or
custom IDs outside the range are included from the SAME tags target table.
The final JOIN still uses t.metric_name to check the exact metric.
Research-only: current direct-inner path still requires a separate
authorization and row-policy correctness review.
"""
from pathlib import Path

path=Path("src/Storages/TimeSeries/PrometheusQueryToSQL/Converter.cpp")
s=path.read_text()
old=r"""                    WHERE s.id >= tuple(sipHash64('{}'),toUUID('00000000-0000-0000-0000-000000000000'))
                      AND s.id <= tuple(sipHash64('{}'),toUUID('ffffffff-ffff-ffff-ffff-ffffffffffff'))
                      AND s.min_time <= fromUnixTimestamp64Milli({})"""
new=r"""                    WHERE (
                        (s.id >= tuple(sipHash64('{}'),toUUID('00000000-0000-0000-0000-000000000000'))
                         AND s.id <= tuple(sipHash64('{}'),toUUID('ffffffff-ffff-ffff-ffff-ffffffffffff')))
                        OR s.id IN (
                            SELECT id FROM {} FINAL
                            WHERE metric_name='{}'
                              AND tupleElement(id,1) != sipHash64('{}')))
                      AND s.min_time <= fromUnixTimestamp64Milli({})"""
if s.count(old)!=1:raise RuntimeError(f"unexpected range source count: {s.count(old)}")
s=s.replace(old,new,1)
old_args=r"""            tableName(settings.lab_samples_target_id),metric,metric,
            last,start-window,"""
new_args=r"""            tableName(settings.lab_samples_target_id),metric,metric,
            tableName(settings.lab_tags_target_id),metric,metric,
            last,start-window,"""
if s.count(old_args)!=1:raise RuntimeError(f"unexpected fmt args count: {s.count(old_args)}")
s=s.replace(old_args,new_args,1)
path.write_text(s)
print("LAB_TS_SAFE_METRIC_RANGE_CPP_APPLIED",path)
