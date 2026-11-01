#!/usr/bin/env python3
"""Compare raw instant MetricsQL/PromQL vs explicit 5m/1d rollup semantics.

One observation per unique CI stage-duration series. Raw instant default
lookback may differ; do NOT call that a correctness bug. Explicit [5m] and
[1d] variants require exact stage/timestamp/value equality.
"""
import argparse
import json
import os
import pathlib
from bench_jenkins_querypath import post,query_vm,normalized_ch,normalized_vm,normalized_events,compare,pct


def main():
    a=argparse.ArgumentParser()
    a.add_argument("--manifest",required=True)
    a.add_argument("--output",required=True)
    a.add_argument("--repeats",type=int,default=4)
    args=a.parse_args()
    m=json.loads(pathlib.Path(args.manifest).read_text())
    start,end,step=m["query_start_epoch"],m["query_end_epoch"],m["step_seconds"]
    name=m["metric"]
    cases=[
        ("raw_instant",f"avg by(stage) ({name})"),
        ("explicit_5m",f"avg by(stage) (last_over_time({name}[5m]))"),
        ("explicit_1d",f"avg by(stage) (avg_over_time({name}[1d]))"),
    ]
    prefix="tslab_jsem_"+os.environ.get("GITHUB_RUN_ID","local")
    result={"manifest":m,"run_id":os.environ.get("GITHUB_RUN_ID"),
            "comparison_scope":"raw-instant may differ by default_rollup/lookback and MUST NOT pass an equivalence gate",
            "cases":{}}
    for case,expr in cases:
        q=f"SELECT * FROM prometheusQueryRange(jenkins_ts, '{expr}', {start}, {end}, {step}) FORMAT JSONEachRow"
        durations={"vm":[],"ch_ts":[]}
        results={"vm":[],"ch_ts":[]}
        for i in range(args.repeats+1):
            ms,resp=post("http://127.0.0.1:18191",q,f"{prefix}_{case}_ch_ts_{i}")
            result_ch=normalized_ch(resp)
            vm_ms,vm_resp=query_vm("http://127.0.0.1:8430",expr,start,end,step)
            result_vm=normalized_vm(vm_resp)
            if i:
                durations["ch_ts"].append(ms)
                durations["vm"].append(vm_ms)
            results["ch_ts"].append(result_ch)
            results["vm"].append(result_vm)
        for engine in ("ch_ts","vm"):
            first=results[engine][0]
            for other in results[engine][1:]:
                compare(first,other,case,engine)
        ch,vm=results["ch_ts"][-1],results["vm"][-1]
        equality={"ch_points":len(ch),"vm_points":len(vm),
                  "common_points":len(set(ch)&set(vm)),
                  "only_ch":len(set(ch)-set(vm)),"only_vm":len(set(vm)-set(ch)),
                  "example_only_vm":sorted(set(vm)-set(ch))[:6],
                  "example_only_ch":sorted(set(ch)-set(vm))[:6]}
        if case!="raw_instant":
            equality["explicit_windows_equal"]=compare(ch,vm,case,"CH-vs-VM")
        if case=="explicit_1d":
            sql=(f"SELECT toUInt64(toUnixTimestamp(toStartOfDay(ts))+86400) AS epoch, "
                 "stage,avg(duration) AS value FROM jenkins_events "
                 f"WHERE ts >= fromUnixTimestamp({start-86400}) AND ts < fromUnixTimestamp({end}) "
                 "GROUP BY epoch,stage FORMAT JSONEachRow")
            _,raw=post("http://127.0.0.1:18191",sql)
            reference=normalized_events(raw)
            equality["ch_vs_sql"]=compare(reference,ch,case,"native-SQL")
            equality["vm_vs_sql"]=compare(reference,vm,case,"native-SQL")
        result["cases"][case]={"expr":expr,"comparison":equality,
                              "timings":{engine:{"p50_ms":pct(times,.5),"p95_ms":pct(times,.95),
                                                  "raw_ms":times} for engine,times in durations.items()}}
        print("JENKINS_SEMANTICS",case,json.dumps(result["cases"][case],sort_keys=True),flush=True)
    post("http://127.0.0.1:18191","SYSTEM FLUSH LOGS")
    _,logs=post("http://127.0.0.1:18191",
        "SELECT query_id,read_rows,read_bytes,memory_usage,query_duration_ms,exception_code "
        f"FROM system.query_log WHERE type='QueryFinish' AND startsWith(query_id,'{prefix}') FORMAT JSONEachRow")
    q={row["query_id"]:row for line in logs.splitlines() if line.strip() for row in [json.loads(line)]}
    for case in result["cases"]:
        ids=[f"{prefix}_{case}_ch_ts_{i}" for i in range(1,args.repeats+1)]
        rows=[q[i] for i in ids if i in q]
        if len(rows)!=args.repeats:raise AssertionError(("missing QueryFinish",case,len(rows)))
        assert all(row["exception_code"]==0 for row in rows),(case,rows)
        result["cases"][case]["timings"]["ch_ts"]["query_log"]={
            "read_rows_p50":pct([x["read_rows"] for x in rows],.5),
            "read_bytes_p50":pct([x["read_bytes"] for x in rows],.5),
            "memory_p95_bytes":pct([x["memory_usage"] for x in rows],.95)}
    path=pathlib.Path(args.output)
    path.parent.mkdir(parents=True,exist_ok=True)
    path.write_text(json.dumps(result,indent=2)+"\n")
    print("JENKINS_SEMANTICS_DONE",json.dumps({"cases":len(cases),"run_id":result["run_id"],
          "raw_instant_equal":result["cases"]["raw_instant"]["comparison"]["only_vm"]==0 and
                               result["cases"]["raw_instant"]["comparison"]["only_ch"]==0,
          "explicit_windows_equal":True},sort_keys=True),flush=True)


if __name__=="__main__":
    main()
