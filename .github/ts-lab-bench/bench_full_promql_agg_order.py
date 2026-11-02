#!/usr/bin/env python3
"""Full TimeSeries PromQL in-order aggregation pipeline comparison.

Read the same dense TimeSeries table, including tags, ID selector and samples.
We vary only query SETTINGS; all ClickHouse PromQL values/labels/timestamps
must agree for every query and every repeat. VM MetricsQL rate numbers
are timing context only, not a numeric equivalence oracle.
"""
import argparse
import json
import os
import pathlib
import random

from bench_bucket_cxx_abca import (
    post,normalize_any,assert_same,vm_call,pct
)


def main():
    p=argparse.ArgumentParser()
    p.add_argument("--manifest",required=True)
    p.add_argument("--output",required=True)
    p.add_argument("--warmups",type=int,default=1)
    p.add_argument("--repeats",type=int,default=5)
    a=p.parse_args()
    m=json.loads(pathlib.Path(a.manifest).read_text())
    start,end,step=(m[k] for k in ("eval_start_epoch","eval_end_epoch","eval_step_seconds"))
    phases=[
        ("baseline_4",{"max_threads":4}),
        ("agg_order_4",{"max_threads":4,"optimize_aggregation_in_order":1}),
        ("read_agg_order_4",{"max_threads":4,"optimize_read_in_order":1,
                             "optimize_aggregation_in_order":1}),
        ("read_noorder_4",{"max_threads":4,"optimize_read_in_order":0,
                            "optimize_aggregation_in_order":0}),
        ("baseline_4_recheck",{"max_threads":4}),
    ]
    cases=[
        ("rate_sum","sum(rate(bench_metric[5m]))"),
        ("rate_team","sum by(team)(rate(bench_metric[5m]))"),
        ("rate_region","sum by(region)(rate(bench_metric[5m]))"),
        ("host_exact",'bench_metric{host="h0001"}'),
        ("host_rate",'rate(bench_metric{host="h0001"}[5m])'),
    ]
    prefix="tslab_orderagg_"+os.environ.get("GITHUB_RUN_ID","local")
    stats={"manifest":m,"run_id":os.environ.get("GITHUB_RUN_ID"),
           "cases":{},"note":"full CH TimeSeries PromQL query plus VM timing context; exact output for CH phases"}
    output=pathlib.Path(a.output)
    for case,expr in cases:
        queries={}
        for phase,setting in phases:
            setting_text=",".join(f"{k}={v}" for k,v in setting.items())
            queries[phase]=(f"SELECT * FROM prometheusQueryRange(dense_ts,'{expr}',"
                            f"{start},{end},{step}) SETTINGS {setting_text}")
        results={ph:[] for ph,_ in phases}
        canonical=None
        for iteration in range(a.warmups+a.repeats):
            for phase,setting in phases:
                qid=f"{prefix}_{case}_{phase}_{iteration}"
                ms,raw=post("http://127.0.0.1:18191",queries[phase]+" FORMAT JSONEachRow",qid)
                rows=normalize_any(raw)
                if canonical is None:canonical=rows
                assert_same(canonical,rows,case,phase)
                if iteration>=a.warmups:results[phase].append(ms)
        vm_times=[]
        for i in range(a.warmups+a.repeats):
            ms,data=vm_call(expr,start,end,step)
            if i>=a.warmups:vm_times.append(ms)
        stats["cases"][case]={"expr":expr,"points":len(canonical),
            "ch_phases":{ph:{"settings":setting,"p50_ms":pct(results[ph],.5),
                             "p95_ms":pct(results[ph],.95),
                             "trials_ms":results[ph]} for ph,setting in phases},
            "vm":{"p50_ms":pct(vm_times,.5),"p95_ms":pct(vm_times,.95),
                  "note":"do not assert rate semantics equal"}}
        print("ORDER_AGG_TIMING",case,json.dumps({
            "points":len(canonical),
            "ch_p50":{ph:stats["cases"][case]["ch_phases"][ph]["p50_ms"] for ph,_ in phases},
            "vm_p50":stats["cases"][case]["vm"]["p50_ms"]},sort_keys=True),flush=True)
        for phase,_ in phases:
            try:
                _,p=post("http://127.0.0.1:18191","EXPLAIN PIPELINE "+queries[phase])
                output.with_name(output.stem+"-"+case+"-"+phase+".pipeline.txt").write_text(p.decode())
                lines=[line.strip() for line in p.decode().splitlines()
                       if any(term in line for term in (
                           'AggregatingInOrder', 'AggregatingTransform',
                           'MergeTreeSelect', 'ReadPoolInOrder', 'ReadPool'))]
                print("ORDER_AGG_EXPLAIN",case,phase,json.dumps(lines[:40]),flush=True)
            except Exception as e:
                output.with_name(output.stem+"-"+case+"-"+phase+".explain-error.txt").write_text(str(e)+"\n")
    post("http://127.0.0.1:18191","SYSTEM FLUSH LOGS")
    _,raw=post("http://127.0.0.1:18191",
        "SELECT query_id,read_rows,read_bytes,memory_usage,query_duration_ms,exception_code "
        "FROM system.query_log WHERE type='QueryFinish' "
        f"AND startsWith(query_id,'{prefix}') FORMAT JSONEachRow")
    logs={x["query_id"]:x for line in raw.splitlines() if line.strip() for x in [json.loads(line)]}
    for case,item in stats["cases"].items():
        for phase,_ in phases:
            ids=[f"{prefix}_{case}_{phase}_{i}" for i in range(a.warmups,a.warmups+a.repeats)]
            rows=[logs[id] for id in ids if id in logs]
            if len(rows)!=a.repeats:raise AssertionError(("QueryFinish count",case,phase,len(rows)))
            if any(r["exception_code"] for r in rows):raise AssertionError(("query error",case,phase,rows))
            x=item["ch_phases"][phase]
            x["read_rows_p50"]=pct([r["read_rows"] for r in rows],.5)
            x["read_bytes_p50"]=pct([r["read_bytes"] for r in rows],.5)
            x["memory_p95_bytes"]=pct([r["memory_usage"] for r in rows],.95)
        base=item["ch_phases"]["baseline_4_recheck"]
        item["ratios_vs_recheck"]={ph:round(base["p50_ms"]/item["ch_phases"][ph]["p50_ms"],4)
                                    for ph,_ in phases}
        print("ORDER_AGG_ABCA",case,json.dumps({
            "p50":{ph:item["ch_phases"][ph]["p50_ms"] for ph,_ in phases},
            "read_rows":{ph:item["ch_phases"][ph]["read_rows_p50"] for ph,_ in phases},
            "memory_p95":{ph:item["ch_phases"][ph]["memory_p95_bytes"] for ph,_ in phases},
            "ratios":item["ratios_vs_recheck"]},sort_keys=True),flush=True)
    stats["every_clickhouse_output_equal"]=True
    output.write_text(json.dumps(stats,indent=2)+"\n")
    print("ORDER_AGG_DONE",json.dumps({"cases":len(cases),
        "all_clickhouse_values_exact":True},sort_keys=True),flush=True)


if __name__=="__main__":
    main()
