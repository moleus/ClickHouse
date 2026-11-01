#!/usr/bin/env python3
"""Capture native PromQL outputs across node generations for selector fastpath safety.

Checks exact equality between baseline and new CXX selector implementation on
short-lived series. This is not a full staleness/HA or late-data proof.
"""
import argparse
import json
import math
import os
import pathlib
import statistics
import time
import urllib.parse
import urllib.request

def main():
    a=argparse.ArgumentParser()
    a.add_argument("--manifest",required=True)
    a.add_argument("--table",default="long_ts")
    a.add_argument("--ch",default="http://127.0.0.1:18191")
    a.add_argument("--phase",required=True)
    a.add_argument("--output",required=True)
    a.add_argument("--edge",action="store_true",help="Include injected gap/reset/late samples")
    a.add_argument("--warmups",type=int,default=0)
    a.add_argument("--repetitions",type=int,default=1)
    a.add_argument("--focus",default="",help="Comma-separated query names; defaults to all")
    a.add_argument("--recent-hours",type=int,default=0,help="Evaluate only final N hours of the manifest")
    p=a.parse_args()
    if p.repetitions<1 or p.warmups<0:
        raise ValueError("Invalid benchmark repeats")
    m=json.loads(pathlib.Path(p.manifest).read_text())
    metric=m["metric"]
    q=[
        ("exact_old", 'rate('+metric+'{instance="node-000-000"}[5m])'),
        ("sum_exact_old", 'sum(rate('+metric+'{instance="node-000-000"}[5m]))'),
        ("sum_exact_new", 'sum(rate('+metric+'{instance="node-001-000"}[5m]))'),
        ("project_both", 'sum(rate('+metric+'{project="project-00"}[5m]))'),
        ("group_project", 'sum by (region) (rate('+metric+'{project="project-00"}[5m]))'),
        ("absent", 'sum(rate('+metric+'{instance="missing"}[5m]))'),
        ("regex", 'sum(rate('+metric+'{instance=~"node-000-000|node-001-000"}[5m]))'),
        ("negated", 'sum(rate('+metric+'{instance!="node-000-000"}[5m]))'),
        ("offset", 'sum(rate('+metric+'{instance="node-000-000"}[5m] offset 1h))'),
    ]
    if p.edge:
        q.extend([
            ("edge_gap_old", 'rate('+metric+'{instance="edge-gap-old"}[5m])'),
            ("edge_gap_new", 'rate('+metric+'{instance="edge-gap-new"}[5m])'),
            ("edge_sum_old", 'sum(rate('+metric+'{instance="edge-gap-old"}[5m]))'),
            ("edge_offset", 'sum(rate('+metric+'{instance="edge-gap-old"}[5m] offset 15m))'),
            ("edge_project", 'sum by (region) (rate('+metric+'{project="project-00"}[5m]))')
        ])
    if p.recent_hours:
        newest=f'node-{m["generations"]-1:03d}-000'
        q.extend([
            ("exact_last", 'rate('+metric+'{instance="'+newest+'"}[5m])'),
            ("sum_exact_last", 'sum(rate('+metric+'{instance="'+newest+'"}[5m]))'),
            ("offset_last", 'sum(rate('+metric+'{instance="'+newest+'"}[5m] offset 15m))'),
        ])
    if p.focus:
        requested=p.focus.split(",")
        available={name for name,_ in q}
        unknown=set(requested)-available
        if unknown:
            raise ValueError(("unknown selectors",sorted(unknown),sorted(available)))
        if len(requested)!=len(set(requested)):
            raise ValueError("duplicate names in --focus")
        q=[case for case in q if case[0] in set(requested)]
    start,end=m["query_start_epoch"],m["query_end_epoch"]
    if p.recent_hours:
        start=max(start,end-p.recent_hours*3600)
    output={"phase":p.phase,"manifest":m,"cases":{},"timings":{}}
    def pct(a,pct_value):
        values=sorted(a)
        x=(len(values)-1)*pct_value
        i=int(x)
        return round(values[i]+(values[min(i+1,len(values)-1)]-values[i])*(x-i),3)
    def assert_equal_results(left,right,context):
        if len(left)!=len(right):
            raise AssertionError((context,"series_count",len(left),len(right)))
        for before,after in zip(left,right):
            if before["tags"]!=after["tags"]:
                raise AssertionError((context,"label_set",before["tags"],after["tags"]))
            a,b=before["samples"],after["samples"]
            if len(a)!=len(b):
                raise AssertionError((context,"point_count",len(a),len(b)))
            for (t,v),(u,w) in zip(a,b):
                if t!=u:
                    raise AssertionError((context,"timestamp",t,u))
                equal=(math.isnan(v) and math.isnan(w)) or math.isclose(v,w,rel_tol=1e-10,abs_tol=1e-8)
                if not equal:
                    raise AssertionError((context,"value",t,v,w,abs(v-w)))
    for name,exp in q:
        sql=("SELECT * FROM prometheusQueryRange("+p.table+", '"+exp.replace("'","''")+
             "', "+str(start)+", "+str(end)+", 60) FORMAT JSONEachRow")
        results=[]
        durations=[]
        for iteration in range(p.warmups+p.repetitions):
            query_id="tslabedge_"+os.environ.get("GITHUB_RUN_ID","local")+"_"+p.phase+"_"+name+"_"+str(iteration)
            params=urllib.parse.urlencode({"allow_experimental_time_series_table":1,"wait_end_of_query":1,
                                           "query_id":query_id,
                                           "log_comment":"tslab_edge_"+p.phase+"_"+name})
            req=urllib.request.Request(p.ch.rstrip('/')+'/?'+params,sql.encode())
            try:
                t0=time.perf_counter_ns()
                with urllib.request.urlopen(req,timeout=180) as resp:raw=resp.read()
                elapsed=(time.perf_counter_ns()-t0)/1e6
            except Exception as exc:
                raise RuntimeError(f"failed {name}: {exp}; {exc}") from exc
            rows=[json.loads(line) for line in raw.splitlines() if line]
            record=sorted([{"tags":tuple(tuple(x) for x in row["tags"]),
                            "samples":tuple((str(t),float(v)) for t,v in row["samples"])}
                           for row in rows],key=lambda x:x["tags"])
            if results:
                assert_equal_results(results[0],record,(p.phase,name,iteration))
            results.append(record)
            if iteration>=p.warmups:durations.append(elapsed)
        output["cases"][name]=results[-1]
        output["timings"][name]={"p50_ms":pct(durations,.5),"p95_ms":pct(durations,.95),
                                  "individual_ms":durations,
                                  "query_id_prefix":"tslabedge_"+os.environ.get("GITHUB_RUN_ID","local")+"_"+p.phase+"_"+name+"_",
                                  "warmups":p.warmups,"repetitions":p.repetitions}
        print("CHURN_CAPTURE",p.phase,name,"series",len(results[-1]),
              "points",sum(len(row['samples']) for row in results[-1]),
              "p50_ms",pct(durations,.5),"p95_ms",pct(durations,.95),flush=True)
    pathlib.Path(p.output).write_text(json.dumps(output,indent=2)+'\n')

if __name__=="__main__":
    main()
