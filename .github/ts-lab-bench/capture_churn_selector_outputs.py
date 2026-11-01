#!/usr/bin/env python3
"""Capture native PromQL outputs across node generations for selector fastpath safety.

Checks exact equality between baseline and new CXX selector implementation on
short-lived series. This is not a full staleness/HA or late-data proof.
"""
import argparse
import json
import pathlib
import urllib.parse
import urllib.request

def main():
    a=argparse.ArgumentParser()
    a.add_argument("--manifest",required=True)
    a.add_argument("--table",default="long_ts")
    a.add_argument("--ch",default="http://127.0.0.1:18191")
    a.add_argument("--phase",required=True)
    a.add_argument("--output",required=True)
    p=a.parse_args()
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
    start,end=m["query_start_epoch"],m["query_end_epoch"]
    output={"phase":p.phase,"manifest":m,"cases":{}}
    for name,exp in q:
        sql=("SELECT * FROM prometheusQueryRange("+p.table+", '"+exp.replace("'","''")+
             "', "+str(start)+", "+str(end)+", 60) FORMAT JSONEachRow")
        params=urllib.parse.urlencode({"allow_experimental_time_series_table":1,"wait_end_of_query":1,
                                       "log_comment":"tslab_churn_"+p.phase+"_"+name})
        req=urllib.request.Request(p.ch.rstrip('/')+'/?'+params,sql.encode())
        try:
            with urllib.request.urlopen(req,timeout=180) as resp:raw=resp.read()
        except Exception as exc:
            raise RuntimeError(f"failed {name}: {exp}; {exc}") from exc
        rows=[json.loads(line) for line in raw.splitlines() if line]
        record=sorted([{"tags":tuple(tuple(x) for x in row["tags"]),
                        "samples":tuple((str(t),float(v)) for t,v in row["samples"])}
                       for row in rows],key=lambda x:x["tags"])
        output["cases"][name]=record
        print("CHURN_CAPTURE",p.phase,name,"series",len(record),"points",sum(len(r['samples']) for r in record),flush=True)
    pathlib.Path(p.output).write_text(json.dumps(output,indent=2)+'\n')

if __name__=="__main__":
    main()
