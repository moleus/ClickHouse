#!/usr/bin/env python3
"""Regular dense metrics: sample bucket size 15m vs default 1h vs 4h.

Same TimeSeries source and labels; only samples_bucket_step_seconds differs.
A/B/C/A phases in one server. All CH output keys/time/values must match.
VM MetricsQL rate is used for latency only. Record query_log read/memory.
"""
import argparse
import json
import os
import pathlib
import urllib.parse

from bench_bucket_cxx_abca import (
    post,normalize_any,to_json_rows,from_json_rows,assert_same,
    vm_call,pct
)


def main():
    p=argparse.ArgumentParser()
    p.add_argument("--manifest",required=True)
    p.add_argument("--output",required=True)
    p.add_argument("--warmups",type=int,default=1)
    p.add_argument("--repeats",type=int,default=6)
    a=p.parse_args()
    m=json.loads(pathlib.Path(a.manifest).read_text())
    start,end,step=(m[k] for k in ("eval_start_epoch","eval_end_epoch","eval_step_seconds"))
    phases=[
        ("base_before","dense_base"),
        ("bucket_15m","dense_15m"),
        ("bucket_4h","dense_4h"),
        ("base_after","dense_base")
    ]
    cases=[
        ("host","bench_metric{host=\"h0001\"}"),
        ("host_rate","rate(bench_metric{host=\"h0001\"}[5m])"),
        ("sum_rate","sum(rate(bench_metric[5m]))"),
        ("rate_by_team","sum by(team)(rate(bench_metric[5m]))"),
        ("rate_by_region","sum by(region)(rate(bench_metric[5m]))"),
        ("regex_host","sum(rate(bench_metric{host=~\"h000[1-4]\"}[5m]))"),
    ]
    source="tslab_densebucket_"+os.environ.get("GITHUB_RUN_ID","local")
    result={"run_id":os.environ.get("GITHUB_RUN_ID"),"manifest":m,
            "phases":phases,"cases":{}}
    for key,expr in cases:
        observed={}
        timings={phase:[] for phase,_ in phases}
        all_sets={}
        for i in range(a.warmups+a.repeats):
            outputs={}
            for phase,table in phases:
                q=(f"SELECT * FROM prometheusQueryRange({table},"
                   f"'{expr}',{start},{end},{step}) FORMAT JSONEachRow")
                query_id=f"{source}_{key}_{phase}_{i}"
                ms,raw=post("http://127.0.0.1:18191",q,query_id)
                rows=normalize_any(raw)
                outputs[phase]=rows
                if i>=a.warmups:timings[phase].append(ms)
            canonical=outputs["base_before"]
            for phase,table in phases:
                assert_same(canonical,outputs[phase],key,phase)
                all_sets[phase]=len(outputs[phase])
        vm_times=[]
        for i in range(a.warmups+a.repeats):
            ms,data=vm_call(expr,start,end,step)
            if i>=a.warmups:vm_times.append(ms)
        result["cases"][key]={"expr":expr,"points":len(canonical),
            "correctness":{"all_layouts_equal":True,"phase_points":all_sets},
            "timings":{ph:{"p50_ms":pct(v,.5),"p95_ms":pct(v,.95),
                           "raw_ms":v} for ph,v in timings.items()}}
        result["cases"][key]["vm"]={"p50_ms":pct(vm_times,.5),
                                     "p95_ms":pct(vm_times,.95),
                                     "raw_ms":vm_times,
                                     "note":"MetricsQL rate may differ from PromQL; output not asserted equal to ClickHouse"}
        print("DENSE_BUCKET_CASE",key,json.dumps({"points":len(canonical),
            "p50_ms":{ph:result["cases"][key]["timings"][ph]["p50_ms"] for ph in timings},
            "vm_p50_ms":result["cases"][key]["vm"]["p50_ms"]},sort_keys=True),flush=True)

    post("http://127.0.0.1:18191","SYSTEM FLUSH LOGS")
    _,raw=post("http://127.0.0.1:18191",
        "SELECT query_id,read_rows,read_bytes,memory_usage,query_duration_ms,exception_code "
        "FROM system.query_log WHERE type='QueryFinish' "
        f"AND startsWith(query_id,'{source}') FORMAT JSONEachRow")
    logs={x["query_id"]:x for line in raw.splitlines() if line.strip() for x in [json.loads(line)]}
    for case,entry in result["cases"].items():
        t=entry["timings"]
        for phase,table in phases:
            ids=[f"{source}_{case}_{phase}_{i}" for i in range(a.warmups,a.warmups+a.repeats)]
            rows=[logs[q] for q in ids if q in logs]
            if len(rows)!=a.repeats:raise AssertionError(("incomplete query_log",case,phase,len(rows)))
            if any(x["exception_code"] for x in rows):raise AssertionError(("unexpected CH query exception",case,phase))
            stats=t[phase]
            stats["read_rows_p50"]=pct([x["read_rows"] for x in rows],.5)
            stats["read_bytes_p50"]=pct([x["read_bytes"] for x in rows],.5)
            stats["memory_p95_bytes"]=pct([x["memory_usage"] for x in rows],.95)
        baseline=t["base_after"]["p50_ms"]
        entry["speedup_vs_recheck"]={ph:round(baseline/t[ph]["p50_ms"],4)
                                     for ph,_ in phases if ph!="base_after"}
        print("DENSE_BUCKET_ABCA",case,json.dumps({
            "p50_ms":{ph:t[ph]["p50_ms"] for ph,_ in phases},
            "read_rows":{ph:t[ph]["read_rows_p50"] for ph,_ in phases},
            "memory_p95":{ph:t[ph]["memory_p95_bytes"] for ph,_ in phases},
            "speedup_vs_recheck":entry["speedup_vs_recheck"]},sort_keys=True),flush=True)
    path=pathlib.Path(a.output)
    path.parent.mkdir(parents=True,exist_ok=True)
    path.write_text(json.dumps(result,indent=2)+"\n")
    print("DENSE_BUCKET_DONE",json.dumps({"cases":len(result["cases"]),
      "all_phases_outputs_equal":True,
      "dense_samples":m["samples_total"],"run":result["run_id"]},sort_keys=True),flush=True)


if __name__=="__main__":
    main()
