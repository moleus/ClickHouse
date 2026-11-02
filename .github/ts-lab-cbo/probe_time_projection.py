#!/usr/bin/env python3
"""Probe skip-index and time-ordered projection support for native TimeRanges.

This is a schema and EXPLAIN smoke before any 1.33m benchmark. An unsupported
projection is a recorded result, not permission to modify upstream sources.
"""
import argparse,json,pathlib,sys
sys.path.insert(0,".github/ts-lab-perf")
from bench_granularity_v11 import inner,sql
def collect(q,report,key):
    try:report[key]={"ok":True,"result":sql(q)[:85000]}
    except Exception as e:report[key]={"ok":False,"error":str(e)[:1800]}
def main():
    ap=argparse.ArgumentParser();ap.add_argument("--out",required=True);args=ap.parse_args()
    out=pathlib.Path(args.out);out.parent.mkdir(parents=True,exist_ok=True)
    report={"checks":{}}
    table="tslab_time_projection_smoke"
    sql(f"CREATE TABLE {table} ENGINE=TimeSeries SETTINGS recent_samples_ttl_seconds=0")
    rows=[]
    for i in range(1000):
        rows.append(json.dumps({"metric_name":"node_cpu_seconds_total",
          "tags":{"instance":f"node-{i:08x}","cluster":f"c{i%10}"},
          "samples":[["2026-10-20 08:00:00.000",float(i)]]},
          separators=(",",":")))
    sql(f"INSERT INTO {table} (metric_name,tags,samples) FORMAT JSONEachRow\n"+"\n".join(rows))
    tr=inner(table,"timeranges")
    report["time_ranges"]=tr
    collect("SHOW CREATE TABLE "+tr+" FORMAT TSVRaw",report["checks"],"ddl")
    collect(f"ALTER TABLE {tr} ADD INDEX idx_time_max max_time TYPE minmax GRANULARITY 1",
            report["checks"],"add_minmax")
    if report["checks"]["add_minmax"]["ok"]:
        collect(f"ALTER TABLE {tr} MATERIALIZE INDEX idx_time_max SETTINGS mutations_sync=2",report["checks"],"mat_minmax")
    collect(f"ALTER TABLE {tr} ADD PROJECTION time_by_max (SELECT id,min_time,max_time ORDER BY max_time,id)",
            report["checks"],"add_projection")
    if report["checks"]["add_projection"]["ok"]:
        collect(f"ALTER TABLE {tr} MATERIALIZE PROJECTION time_by_max SETTINGS mutations_sync=2",
                report["checks"],"mat_projection")
    query=(f"SELECT count() FROM {tr} WHERE max_time >= "
           "toDateTime64('2026-10-19 00:00:00',3,'UTC')")
    for label,opts in {
        "none":"optimize_use_projections=0",
        "cost":"optimize_use_projections=1,prefer_projection_optimization=0",
        "prefer":"optimize_use_projections=1,prefer_projection_optimization=1",
    }.items():
        collect("EXPLAIN PLAN indexes=1,projections=1 "+query+" SETTINGS "+opts,
                report["checks"],"explain_"+label)
        collect(query+" SETTINGS "+opts+" FORMAT TSV",report["checks"],"query_"+label)
    report["passed"]=bool(report["checks"]["ddl"]["ok"])
    out.write_text(json.dumps(report,indent=2)+"\n")
    print("TIME_PROJECTION_SMOKE",json.dumps({k:{"ok":v["ok"],"error":v.get("error")}
              for k,v in report["checks"].items()},separators=(",",":")),flush=True)
    if not report["passed"]:raise SystemExit(1)
if __name__=="__main__":
    main()
