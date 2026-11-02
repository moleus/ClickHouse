#!/usr/bin/env python3
"""Guard projection correctness across TimeSeries incremental updates and merges.

Both physical tables accept identical metric inputs, one has an optional
AggregatingMergeTree projection. Query plain and forced projection before and
after inserts and OPTIMIZE FINAL. Fail on any count, timestamp, label, or value
change. This small test never reads or changes production data.
"""
import argparse
import datetime
import hashlib
import json
import pathlib
import sys
import urllib.error
import urllib.parse
import urllib.request

sys.path.insert(0, ".github/ts-lab-perf")
sys.path.insert(0, ".github/ts-lab-bench")
from bench_granularity_v11 import inner
from bench_bucket_cxx_abca import normalize_any, assert_same

URL = "http://127.0.0.1:18191/"
METRIC = "tslab_incremental_time_range"
COUNT = 1024
FILTERS = {
    "recent": "max_time >= toDateTime64('2026-10-31 00:00:00',3,'UTC')",
    "old": "min_time <= toDateTime64('2026-10-19 23:59:59',3,'UTC')",
    "middle": "max_time >= toDateTime64('2026-10-22 00:00:00',3,'UTC')",
    "full": "max_time >= toDateTime64('2026-10-01 00:00:00',3,'UTC')",
}
SETTINGS = {
    "none": "optimize_use_projections=0",
    "cost": "optimize_use_projections=1",
    "force": "optimize_use_projections=1,force_optimize_projection=1",
}

def lit(s):
    return "'" + str(s).replace("'", "\\'") + "'"

def sql(stmt):
    try:
        with urllib.request.urlopen(urllib.request.Request(URL, data=stmt.encode(), method="POST"), timeout=120) as response:
            return response.read().decode()
    except urllib.error.HTTPError as e:
        raise RuntimeError(f"SQL {e.code}: {e.read().decode(errors='replace')[:1500]}: {stmt[:450]}") from e

def json_rows(q):
    return [json.loads(s) for s in sql(q+" FORMAT JSONEachRow").splitlines() if s.strip()]

def save(path, o):
    pathlib.Path(path).parent.mkdir(parents=True, exist_ok=True)
    pathlib.Path(path).write_text(json.dumps(o, indent=2, default=str) + "\n")

def input_for(phase):
    timestamps = ("2026-10-20 08:00:00.000", "2026-10-22 08:00:00.000",
                  "2026-10-19 08:00:00.000", "2026-11-01 08:00:00.000")
    data = []
    for i in range(COUNT):
        if phase == 1:
            stamp = timestamps[0]
        elif phase == 2:
            stamp = timestamps[1] if i%2 == 0 else timestamps[2]
        elif phase == 3 and i%4 == 0:
            stamp = timestamps[3]
        else:
            continue
        row = {"metric_name": METRIC,
               "tags": {"job":"update-smoke", "instance":f"node-{i:04}",
                        "cluster":f"cluster-{i%4:02}"},
               "samples": [[stamp,float(i+phase/10)]]}
        data.append(json.dumps(row,separators=(",",":"),sort_keys=True))
    return "\n".join(data)+"\n"

def ingest(table, text):
    sql(f"INSERT INTO {table} (metric_name,tags,samples) FORMAT JSONEachRow\n"+text)
    return {"records":len(text.splitlines()),
            "sha256":hashlib.sha256(text.encode()).hexdigest()}

def diagnostic(table, use_projection, qcondition):
    target=inner(table,"timeranges")
    statement=(f"SELECT count() AS n FROM {target} "
               f"WHERE {FILTERS[qcondition]} "
               f"SETTINGS {SETTINGS[use_projection]},optimize_trivial_count_query=0")
    plan=sql("EXPLAIN PLAN indexes=1,projections=1 "+statement)
    value=json_rows(statement)[0]
    return {"count":value,"plan":plan[:23000],
            "selected_projection":"time_by_max" in plan}

def prometheus(table):
    start=int(datetime.datetime(2026,10,19,12,tzinfo=datetime.timezone.utc).timestamp())
    end=start+14*86400
    promql="avg by(cluster)(avg_over_time("+METRIC+"[1d]))"
    q=(f"SELECT * FROM prometheusQueryRange({table},{lit(promql)},{start},{end},86400) "
       "SETTINGS max_threads=4 FORMAT JSONEachRow")
    got=normalize_any(sql(q).encode())
    if len(got)<4:
        raise AssertionError(f"Too few native PromQL points: {len(got)}")
    return got

def phase_control(stage, report):
    out={"stage":stage,"queries":{}}
    for filter_name in FILTERS:
        a=diagnostic("update_plain","none",filter_name)
        b=diagnostic("update_indexed","none",filter_name)
        if a["count"]!=b["count"]:
            raise AssertionError(f"Base table results differ: {filter_name} {a['count']} != {b['count']}")
        controls={"plain":a,"indexed":b}
        for mode in ("cost","force"):
            got=diagnostic("update_indexed",mode,filter_name)
            if got["count"]!=b["count"]:
                raise AssertionError(f"Projection changed results: {stage} {filter_name} {mode} {got['count']} != {b['count']}")
            controls[mode]=got
        out["queries"][filter_name]=controls
        # Cardinality is checked independently; keep the timed projection
        # query identical to the verified COUNT-only projection smoke.
        tr=inner("update_indexed","timeranges")
        out.setdefault("distinct_ids",{})[filter_name]=json_rows(
            f"SELECT uniqExact(id) AS ids FROM {tr} WHERE {FILTERS[filter_name]}")[0]["ids"]
    a=prometheus("update_plain")
    b=prometheus("update_indexed")
    out["promql"]={"points":len(a),"precision":assert_same(a,b,stage,"indexed")}
    report["phases"].append(out)
    return out

def main():
    p=argparse.ArgumentParser()
    p.add_argument("--out", required=True)
    a=p.parse_args()
    report={"name":"TimeRanges incremental projection semantic control",
            "phases":[],"input":[],"errors":[]}
    try:
        sql("CREATE TABLE update_plain ENGINE=TimeSeries SETTINGS recent_samples_ttl_seconds=0")
        sql("CREATE TABLE update_indexed ENGINE=TimeSeries SETTINGS recent_samples_ttl_seconds=0")
        initial=input_for(1)
        for table in ("update_plain","update_indexed"):
            report["input"].append({"table":table,"phase":1,**ingest(table,initial)})
        tr=inner("update_indexed","timeranges")
        sql(f"ALTER TABLE {tr} MODIFY SETTING deduplicate_merge_projection_mode='rebuild'")
        sql(f"ALTER TABLE {tr} ADD PROJECTION time_by_max (SELECT id,min_time,max_time ORDER BY max_time,id)")
        sql(f"ALTER TABLE {tr} MATERIALIZE PROJECTION time_by_max SETTINGS mutations_sync=2")
        projected=inner("update_indexed","timeranges").split(chr(96))[1]
        report["projection_parts_initial"]=json_rows(
            "SELECT name,parent_name,rows,marks,bytes_on_disk FROM system.projection_parts "
            "WHERE active AND database='default' AND table="+lit(projected))
        if not report["projection_parts_initial"]:
            raise AssertionError("Projection was not materialized before incremental updates")
        phase_control("first_insert_and_materialize",report)
        for k in (2,3):
            data=input_for(k)
            for table in ("update_plain","update_indexed"):
                report["input"].append({"table":table,"phase":k,**ingest(table,data)})
            phase_control(f"insert_{k}_before_merges",report)
            for table in ("update_plain","update_indexed"):
                sql("OPTIMIZE TABLE "+inner(table,"timeranges")+" FINAL SETTINGS optimize_throw_if_noop=0")
            phase_control(f"insert_{k}_after_merge",report)
        recent=report["phases"][-1]["distinct_ids"]["recent"]
        old=report["phases"][-1]["distinct_ids"]["old"]
        if (recent,old)!=(COUNT//4,COUNT//2):
            raise AssertionError(f"Wrong final update ranges: recent={recent} old={old}, expected {COUNT//4} and {COUNT//2}")
        report["passed"]=True
    except Exception as e:
        report["passed"]=False
        report["errors"].append(str(e)[:2200])
    finally:
        save(a.out,report)
        print("TIME_UPDATE_PROJECTION_RESULT",json.dumps({"passed":report["passed"],
            "errors":report["errors"],"phases":len(report["phases"]),
            "native_points":[x["promql"]["points"] for x in report["phases"]],
            "sha256":[x["sha256"] for x in report["input"][:3]]},separators=(",",":")),flush=True)
    if not report["passed"]:
        raise SystemExit(1)

if __name__ == "__main__":
    main()
