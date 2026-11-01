#!/usr/bin/env python3
"""Compare v11 TimeSeries label-index layouts on one-sample Jenkins data.

Keep samples identical across four tables; vary only tags_to_columns and tags
primary/sorting keys. Baseline before/after monitors warm drift. Validate every
stage/day result against independent native event SQL and VM MetricsQL.
"""
import argparse
import json
import os
import pathlib
import random

from bench_jenkins_querypath import (
    post, query_vm, normalized_ch, normalized_vm, normalized_events, compare, pct
)


def main():
    p=argparse.ArgumentParser()
    p.add_argument("--manifest",required=True)
    p.add_argument("--output",required=True)
    p.add_argument("--warmups",type=int,default=1)
    p.add_argument("--repeats",type=int,default=4)
    a=p.parse_args()
    m=json.loads(pathlib.Path(a.manifest).read_text())
    start,end,step=m["query_start_epoch"],m["query_end_epoch"],m["step_seconds"]
    prefix="tslab_jorder_"+os.environ.get("GITHUB_RUN_ID","local")
    cases=[
        ("all","",""),
        ("branch_main",'{branch="main"}',"AND branch='main'"),
        ("project_00",'{project="project-00"}',"AND project='project-00'"),
        ("stage_0000_0009",'{stage=~"stage-000[0-9]"}',
         "AND stage IN ('stage-0000','stage-0001','stage-0002','stage-0003','stage-0004','stage-0005','stage-0006','stage-0007','stage-0008','stage-0009')"),
    ]
    phases=[
        ("base_before","jenkins_ts"),
        ("tagcols","jenkins_ts_tagcols"),
        ("stage_sorted","jenkins_ts_stage_sorted"),
        ("project_sorted","jenkins_ts_project_sorted"),
        ("base_after","jenkins_ts"),
    ]
    output={"run_id":os.environ.get("GITHUB_RUN_ID"),"manifest":m,
            "phases":phases,"cases":{},
            "semantics":"all four TimeSeries tables have the same data and no time_ranges; varying labels columns and tags key; 1d avg_over_time"}
    for case,matcher,condition in cases:
        expr=f"avg by(stage) (avg_over_time({m['metric']}{matcher}[1d]))"
        sql=(f"SELECT toUInt64(toUnixTimestamp(toStartOfDay(ts))+86400) AS epoch, "
             f"stage,avg(duration) AS value FROM jenkins_events "
             f"WHERE ts >= fromUnixTimestamp({start-86400}) "
             f"AND ts < fromUnixTimestamp({end}) {condition} "
             f"GROUP BY epoch,stage ORDER BY epoch,stage FORMAT JSONEachRow")
        _,raw=post("http://127.0.0.1:18191",sql)
        canonical=normalized_events(raw)
        timings={phase:[] for phase,_ in phases}
        timings["vm"]=[]
        validations={}
        for iteration in range(a.warmups+a.repeats):
            # Same-run four table layouts plus original baseline recheck.
            for phase,table in phases:
                q=(f"SELECT * FROM prometheusQueryRange({table}, '{expr}', "
                   f"{start}, {end}, {step}) FORMAT JSONEachRow")
                query_id=f"{prefix}_{case}_{phase}_{iteration}"
                ms,raw=post("http://127.0.0.1:18191",q,query_id)
                data=normalized_ch(raw)
                validations[phase]=compare(canonical,data,case,phase)
                if iteration>=a.warmups:
                    timings[phase].append(ms)
            vm_ms,vm_data=query_vm("http://127.0.0.1:8430",expr,start,end,step)
            validations["vm"]=compare(canonical,normalized_vm(vm_data),case,"vm")
            if iteration>=a.warmups:
                timings["vm"].append(vm_ms)
        output["cases"][case]={"expr":expr,"points":len(canonical),
                              "validation":validations,
                              "timings":{k:{"p50_ms":pct(v,.5),"p95_ms":pct(v,.95),"raw_ms":v}
                                         for k,v in timings.items()}}
        print("JORDER_TIMING",case,json.dumps({"points":len(canonical),
              "p50":{k:output["cases"][case]["timings"][k]["p50_ms"] for k in timings}},sort_keys=True),flush=True)
    post("http://127.0.0.1:18191","SYSTEM FLUSH LOGS")
    _,log=post("http://127.0.0.1:18191",
            "SELECT query_id,read_rows,read_bytes,memory_usage,query_duration_ms,exception_code "
            "FROM system.query_log WHERE type='QueryFinish' "
            f"AND startsWith(query_id,'{prefix}') FORMAT JSONEachRow")
    rows={v["query_id"]:v for line in log.splitlines() if line.strip() for v in [json.loads(line)]}
    for case in output["cases"]:
        for phase,table in phases:
            ids=[f"{prefix}_{case}_{phase}_{i}" for i in range(a.warmups,a.warmups+a.repeats)]
            entries=[rows[id] for id in ids if id in rows]
            if len(entries)!=a.repeats:raise AssertionError(("missing query_log",case,phase,len(entries)))
            if any(x["exception_code"] for x in entries):raise AssertionError(("query exception",case,phase,entries))
            s=output["cases"][case]["timings"][phase]
            for metric,field in [("read_rows_p50","read_rows"),("read_bytes_p50","read_bytes"),
                                 ("memory_p50_bytes","memory_usage"),("memory_p95_bytes","memory_usage")]:
                s[metric]=pct([x[field] for x in entries],.95 if metric=="memory_p95_bytes" else .5)
            s["peak_memory_bytes"]=max(x["memory_usage"] for x in entries)
        parts=output["cases"][case]["timings"]
        before,after=parts["base_before"]["p50_ms"],parts["base_after"]["p50_ms"]
        ratios={"baseline_drift_ratio":round(after/before,4)}
        for candidate in ["tagcols","stage_sorted","project_sorted"]:
            ratios[candidate+"_speedup_vs_recheck"]=round(after/parts[candidate]["p50_ms"],4)
            ratios[candidate+"_memory_ratio_vs_recheck"]=round(
                parts[candidate]["memory_p95_bytes"]/max(1,parts["base_after"]["memory_p95_bytes"]),4)
            ratios[candidate+"_read_rows_ratio_vs_recheck"]=round(
                parts[candidate]["read_rows_p50"]/max(1,parts["base_after"]["read_rows_p50"]),4)
        output["cases"][case]["ratios"]=ratios
        print("JORDER_AB",case,json.dumps({
            "p50_ms":{ph:parts[ph]["p50_ms"] for ph in parts},
            "read_rows":{ph:parts[ph].get("read_rows_p50") for ph in parts},
            "query_memory_p95":{ph:parts[ph].get("memory_p95_bytes") for ph in parts},
            "ratios":ratios},sort_keys=True),flush=True)
    path=pathlib.Path(a.output)
    path.parent.mkdir(parents=True,exist_ok=True)
    path.write_text(json.dumps(output,indent=2)+"\n")
    print("JORDER_DONE",json.dumps({"cases":len(cases),
          "all_output_exact":True,"run_id":output["run_id"]},sort_keys=True),flush=True)


if __name__=="__main__":
    main()
