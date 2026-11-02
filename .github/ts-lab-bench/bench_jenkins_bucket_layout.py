#!/usr/bin/env python3
"""Same-run Jenkins 1h versus 4h samples bucket layout A/B/A.

Two TimeSeries tables receive identical single-sample labelsets, tags and time-range settings. Both outputs
must match native MergeTree event averages and VM MetricsQL over daily windows.
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
    prefix="tslab_jbucket_"+os.environ.get("GITHUB_RUN_ID","local")
    cases=[
        ("all","",""),
        ("branch_main",'{branch="main"}',"AND branch='main'"),
        ("project_00",'{project="project-00"}',"AND project='project-00'"),
        ("stage_0000_0009",'{stage=~"stage-000[0-9]"}',
         "AND stage IN ('stage-0000','stage-0001','stage-0002','stage-0003','stage-0004','stage-0005','stage-0006','stage-0007','stage-0008','stage-0009')"),
    ]
    output={"run_id":os.environ.get("GITHUB_RUN_ID"),"manifest":m,"cases":{},
            "semantics":"CH TimeSeries v11 samples bucket size=1h/4h on same events; daily avg_over_time exact equivalence"}
    for case,matcher,condition in cases:
        expr=f"avg by(stage) (avg_over_time({m['metric']}{matcher}[1d]))"
        sql=(f"SELECT toUInt64(toUnixTimestamp(toStartOfDay(ts))+86400) AS epoch, "
             f"stage,avg(duration) AS value FROM jenkins_events "
             f"WHERE ts >= fromUnixTimestamp({start-86400}) "
             f"AND ts < fromUnixTimestamp({end}) {condition} "
             f"GROUP BY epoch,stage ORDER BY epoch,stage FORMAT JSONEachRow")
        _,raw=post("http://127.0.0.1:18191",sql)
        canonical=normalized_events(raw)
        timings={"base_before":[],"bucket_4h":[],"base_after":[],"vm":[]}
        validations={}
        for iteration in range(a.warmups+a.repeats):
            # Three phase A/B/A makes warm/cold drift visible.
            for phase,table in [("base_before","jenkins_ts"),
                                ("bucket_4h","jenkins_ts_4h"),
                                ("base_after","jenkins_ts")]:
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
        print("JBUCKET_TIMING",case,json.dumps({"points":len(canonical),
              "p50":{k:output["cases"][case]["timings"][k]["p50_ms"] for k in timings}},sort_keys=True),flush=True)
    post("http://127.0.0.1:18191","SYSTEM FLUSH LOGS")
    _,log=post("http://127.0.0.1:18191",
            "SELECT query_id,read_rows,read_bytes,memory_usage,query_duration_ms,exception_code "
            "FROM system.query_log WHERE type='QueryFinish' "
            f"AND startsWith(query_id,'{prefix}') FORMAT JSONEachRow")
    rows={v["query_id"]:v for line in log.splitlines() if line.strip() for v in [json.loads(line)]}
    for case in output["cases"]:
        for phase in ["base_before","bucket_4h","base_after"]:
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
        off=parts["bucket_4h"]["p50_ms"]
        output["cases"][case]["aba"]={"baseline_drift_ratio":round(after/before,4),
                                      "four_h_vs_recheck_ratio":round(off/after,4),
                                      "four_h_speedup_vs_warm_baseline":round(after/off,4)}
        print("JBUCKET_ABA",case,json.dumps({
            "p50_ms":{ph:parts[ph]["p50_ms"] for ph in parts},
            "read_rows":{ph:parts[ph].get("read_rows_p50") for ph in parts},
            "query_memory_p95":{ph:parts[ph].get("memory_p95_bytes") for ph in parts},
            "ratios":output["cases"][case]["aba"]},sort_keys=True),flush=True)
    path=pathlib.Path(a.output)
    path.parent.mkdir(parents=True,exist_ok=True)
    path.write_text(json.dumps(output,indent=2)+"\n")
    print("JBUCKET_DONE",json.dumps({"cases":len(cases),
          "all_output_exact":True,"run_id":output["run_id"]},sort_keys=True),flush=True)


if __name__=="__main__":
    main()
