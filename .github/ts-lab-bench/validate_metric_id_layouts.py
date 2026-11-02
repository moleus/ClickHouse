#!/usr/bin/env python3
"""Adversarial TimeSeries v11 ID-layout correctness for C++ guarded fastpaths.

Compare original ClickHouse and patched binary on canonical, shifted
two-component and alternative UInt64 series IDs. The two patched cases
require the same stage/timestamp/value output as the baseline.
"""
import json
import math
import subprocess
import sys

if len(sys.argv) != 3:
    raise SystemExit("Usage: validate_metric_id_layouts.py BASELINE_CLICKHOUSE PATCHED_CLICKHOUSE")

baseline,patched=sys.argv[1:]

schemas={
    "canonical":"",
    "shifted_tuple":(
        "TAGS INNER COLUMNS ("
        "id Tuple(UInt64, LowCardinality(UUID)) "
        "DEFAULT tuple(sipHash64(metric_name) + toUInt64(17), "
        "toLowCardinality(reinterpretAsUUID(sipHash128(tags)))))"
    ),
    "uint64":(
        "TAGS INNER COLUMNS (id UInt64 DEFAULT sipHash64(tags))"
    ),
}

def fetch(binary,schema):
    setup="""
    SET allow_experimental_time_series_table = 1;
    SET enable_time_series_aggregate_functions = 1;
    SET session_timezone = 'UTC';
    CREATE TABLE t ENGINE = TimeSeries
    SETTINGS recent_samples_ttl_seconds=0, store_time_ranges=false
    %s;
    INSERT INTO t (metric_name,tags,samples) VALUES
     ('foo',map('stage','a','job','a'),[(toDateTime64(172800,3),3.0),(toDateTime64(259200,3),9.0)]),
     ('foo',map('stage','a','job','b'),[(toDateTime64(172800,3),5.0),(toDateTime64(259200,3),11.0)]),
     ('foo',map('stage','b','job','a'),[(toDateTime64(172800,3),7.0),(toDateTime64(259200,3),13.0)]),
     ('bar',map('stage','x'),[(toDateTime64(172800,3),99.0),(toDateTime64(259200,3),109.0)]);
    """%schema
    q=("SELECT * FROM prometheusQueryRange("
       "t,'avg by(stage)(avg_over_time(foo[1d]))',172800,259200,86400) "
       "ORDER BY ALL FORMAT JSONEachRow;")
    proc=subprocess.run([binary,"local","--tmp","--multiquery"],
        input=setup+q, text=True,capture_output=True,timeout=110)
    if proc.returncode:
        raise RuntimeError(f"binary={binary} schema={schema!r}: {proc.stderr[-4000:]}")
    rows=[json.loads(x) for x in proc.stdout.splitlines() if x.strip()]
    output={}
    for row in rows:
        tags=tuple(tuple(x) for x in row["tags"])
        values=tuple((str(t),float(v)) for t,v in row["samples"])
        output[tags]=values
    if not output:
        raise AssertionError(("No outputs",binary,schema))
    return output

def same(a,b):
    assert a.keys()==b.keys(),(a,b)
    for key in a:
        assert len(a[key])==len(b[key]),(key,a[key],b[key])
        for (ta,va),(tb,vb) in zip(a[key],b[key]):
            assert ta==tb,(key,ta,tb)
            assert math.isclose(va,vb,rel_tol=1e-12,abs_tol=1e-12),(key,ta,va,vb)

for name,schema in schemas.items():
    expected=fetch(baseline,schema)
    actual=fetch(patched,schema)
    same(expected,actual)
    print("ID_LAYOUT_CH_EXACT",json.dumps({
        "schema":name,"groups":len(actual),
        "points":sum(len(v) for v in actual.values()),
        "original_equals_patched":True}),flush=True)
print("ID_LAYOUT_DONE",json.dumps({"checked":list(schemas),"equal":True}),flush=True)
