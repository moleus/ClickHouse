#!/usr/bin/env python3
"""Test whether CH rate aggregation is serialized by in-order MergeTree read.

Same physical TimeSeries inner samples, same grouped rate result. Vary only
query execution settings (optimize_read_in_order, aggregation_in_order,
minimum bytes/rows for parallel reads, max_threads). All sampled grid values
must equal the max_threads=1 reference with strict floating tolerance.
"""
import argparse
import json
import math
import os
import pathlib
import random

from bench_jenkins_querypath import post,pct


def main():
    p=argparse.ArgumentParser()
    p.add_argument("--manifest",required=True)
    p.add_argument("--output",required=True)
    p.add_argument("--warmups",type=int,default=2)
    p.add_argument("--repeats",type=int,default=6)
    a=p.parse_args()
    m=json.loads(pathlib.Path(a.manifest).read_text())
    _,raw=post("http://127.0.0.1:18191",
         "SELECT name FROM system.tables WHERE database='default' AND startsWith(name,'.inner_id.samples.') FORMAT TSVRaw")
    names=raw.decode().strip().splitlines()
    assert len(names)==1,("expected one samples table",names)
    table="default.`"+names[0]+"`"
    start,end,step=(m[k] for k in ("eval_start_epoch","eval_end_epoch","eval_step_seconds"))
    startms,endms=start*1000,end*1000
    rate=(f"timeSeriesRateToGrid(fromUnixTimestamp64Milli({startms}),"
          f"fromUnixTimestamp64Milli({endms}),toIntervalSecond({step}),"
          "toIntervalSecond(300))(samples)")
    where=(f" WHERE min_time<=fromUnixTimestamp64Milli({endms})"
           f" AND max_time>=fromUnixTimestamp64Milli({startms-300000})")
    base=("SELECT sumForEach(grid) AS values FROM (SELECT "
         +rate+" AS grid FROM "+table+where+" GROUP BY id) ")
    variations=[
        ("ordered_1",{"max_threads":1}),
        ("ordered_4",{"max_threads":4}),
        ("unordered_4",{"max_threads":4,"optimize_read_in_order":0,
                        "optimize_aggregation_in_order":0}),
        ("unordered_4_forced",{"max_threads":4,"optimize_read_in_order":0,
                               "optimize_aggregation_in_order":0,
                               "merge_tree_min_bytes_for_concurrent_read":1,
                               "merge_tree_min_rows_for_concurrent_read":1}),
        ("unordered_8_forced",{"max_threads":8,"optimize_read_in_order":0,
                               "optimize_aggregation_in_order":0,
                               "merge_tree_min_bytes_for_concurrent_read":1,
                               "merge_tree_min_rows_for_concurrent_read":1})
    ]
    queries={}
    for key,settings in variations:
        queries[key]=base+" SETTINGS "+",".join(k+"="+str(v) for k,v in settings.items())
    reference=None
    trials={name:[] for name in queries}
    prefix="tslab_innerparallel_"+os.environ.get("GITHUB_RUN_ID","local")
    comparisons={}
    rng=random.Random(20261102)
    for i in range(a.warmups+a.repeats):
        names=list(queries)
        rng.shuffle(names)
        for name in names:
            q=queries[name]+" FORMAT JSONEachRow"
            ms,body=post("http://127.0.0.1:18191",q,f"{prefix}_{name}_{i}")
            parsed=[json.loads(row) for row in body.splitlines() if row.strip()]
            if len(parsed)!=1:raise AssertionError(("expected one group",name,parsed[:3]))
            nums=[None if v is None else float(v) for v in parsed[0]["values"]]
            if reference is None:
                reference=nums
            if len(nums)!=len(reference):
                raise AssertionError(("different grid sizes",name,len(nums),len(reference)))
            max_abs=0.
            for j,(v,u) in enumerate(zip(nums,reference)):
                if (v is None)!=(u is None):raise AssertionError(("different null",name,i,j,v,u))
                if v is not None:
                    if not math.isclose(v,u,rel_tol=1e-11,abs_tol=1e-8):
                        raise AssertionError(("grid value changed",name,i,j,v,u))
                    max_abs=max(max_abs,abs(v-u))
            comparisons[name]=max(comparisons.get(name,0),max_abs)
            if i>=a.warmups:trials[name].append(ms)
        print("PARALLEL_TRIAL",i,{k:trials[k][-1] if trials[k] else None for k in trials},flush=True)
    post("http://127.0.0.1:18191","SYSTEM FLUSH LOGS")
    _,logs=post("http://127.0.0.1:18191",
        "SELECT query_id,read_rows,read_bytes,memory_usage,query_duration_ms,exception_code "
        "FROM system.query_log WHERE type='QueryFinish' "
        f"AND startsWith(query_id,'{prefix}') FORMAT JSONEachRow")
    logrows={r["query_id"]:r for line in logs.splitlines() if line.strip() for r in [json.loads(line)]}
    result={"manifest":m,"run_id":os.environ.get("GITHUB_RUN_ID"),"query":base,
            "source":table,"comparisons":comparisons,"variants":{}}
    output=pathlib.Path(a.output)
    for name,settings in variations:
        times=trials[name]
        ids=[f"{prefix}_{name}_{i}" for i in range(a.warmups,a.warmups+a.repeats)]
        rows=[logrows[q] for q in ids if q in logrows]
        if len(rows)!=a.repeats:raise AssertionError(("QueryFinish missing",name,len(rows)))
        if any(r["exception_code"] for r in rows):raise AssertionError(("query exceptions",name,rows))
        q=queries[name]
        stats={"settings":settings,"p50_ms":pct(times,.5),"p95_ms":pct(times,.95),
               "raw_ms":times,
               "read_rows_p50":pct([r["read_rows"] for r in rows],.5),
               "read_bytes_p50":pct([r["read_bytes"] for r in rows],.5),
               "query_memory_p95_bytes":pct([r["memory_usage"] for r in rows],.95)}
        result["variants"][name]=stats
        try:
            _,plan=post("http://127.0.0.1:18191","EXPLAIN PIPELINE "+q)
            output.with_name(output.stem+"-"+name+".pipeline.txt").write_text(plan.decode())
        except Exception as e:
            output.with_name(output.stem+"-"+name+".error.txt").write_text(str(e)+"\n")
        print("PARALLEL_VARIANT",name,json.dumps(stats,sort_keys=True),flush=True)
    result["all_values_equal"]=True
    result["baseline_recheck_note"]="Randomized order across every trial; original ordered_1 and ordered_4 each measured in every repeat"
    output.write_text(json.dumps(result,indent=2)+"\n")
    print("PARALLEL_DONE",json.dumps({"cases":len(result["variants"]),
         "exact_values":True,"grid_points":len(reference)},sort_keys=True),flush=True)


if __name__=="__main__":
    main()
