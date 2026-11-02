#!/usr/bin/env python3
"""Direct per-ID scalar rate over the SAME TimeSeries samples target.

This SQL-only research plan avoids the tags->group mapping because the root
sum(rate(exact metric)) discards all labels. This is not a general PromQL
rewrite. Exact output against the ORIGINAL ClickHouse PromQL is mandatory.
"""
import argparse
import json
import math
import os
import pathlib
from bench_bucket_cxx_abca import post,vm_call,pct

def native_grid(raw):
    rows=[json.loads(x) for x in raw.splitlines() if x.strip()]
    if len(rows)!=1 or rows[0]["tags"] != []:
        raise AssertionError(("unexpected native sum(rate) output",rows[:2]))
    return [float(value) for timestamp,value in rows[0]["samples"]]

def direct_grid(raw):
    rows=[json.loads(x) for x in raw.splitlines() if x.strip()]
    if len(rows)!=1:
        raise AssertionError(("unexpected direct rate shape",rows[:2]))
    return [None if v is None else float(v) for v in rows[0]["values"]]

def match(native,direct,phase):
    if len(native)!=len(direct):
        raise AssertionError((phase,"length",len(native),len(direct)))
    for i,(a,b) in enumerate(zip(native,direct)):
        if b is None or not math.isclose(a,b,rel_tol=1e-10,abs_tol=1e-10):
            raise AssertionError((phase,"mismatch",i,a,b))

def main():
    p=argparse.ArgumentParser()
    p.add_argument("--manifest",required=True)
    p.add_argument("--output",required=True)
    p.add_argument("--warmups",type=int,default=2)
    p.add_argument("--repeats",type=int,default=6)
    a=p.parse_args()
    m=json.loads(pathlib.Path(a.manifest).read_text())
    start,end,step=(m[x] for x in ("eval_start_epoch","eval_end_epoch","eval_step_seconds"))
    _,names=post("http://127.0.0.1:18191",
        "SELECT name FROM system.tables WHERE database='default' "
        "AND startsWith(name,'.inner_id.samples.') FORMAT TSVRaw")
    names=names.decode().strip().splitlines()
    if len(names)!=1:raise AssertionError(("unexpected inner targets",names))
    qname="default."+chr(96)+names[0].replace(chr(96),chr(96)*2)+chr(96)
    start_ms,end_ms=start*1000,end*1000
    rate=(f"timeSeriesRateToGrid(fromUnixTimestamp64Milli({start_ms}),"
          f"fromUnixTimestamp64Milli({end_ms}),"
          f"toIntervalSecond({step}),toIntervalSecond(300))"
          "(arrayFilter(sample -> reinterpretAsUInt64(tupleElement(sample,2)) "
          "!= 9218868437227405314,samples))")
    inner=(f"SELECT sumForEach(grid) AS values FROM "
           f"(SELECT {rate} AS grid FROM {qname} "
           f"WHERE min_time <= fromUnixTimestamp64Milli({end_ms}) "
           f"AND max_time > fromUnixTimestamp64Milli({start_ms-300000}) "
           "AND tupleElement(id,1)=sipHash64('bench_metric') "
           "GROUP BY id)")
    outer=(f"SELECT * FROM prometheusQueryRange("
           f"dense_ts,'sum(rate(bench_metric[5m]))',{start},{end},{step})")
    phases=[
        ("native_base",outer+" SETTINGS max_threads=4",True),
        ("inner_default",inner+" SETTINGS max_threads=4",False),
        ("inner_parallel4",inner+" SETTINGS max_threads=4,"
            "merge_tree_min_bytes_for_concurrent_read=1,"
            "merge_tree_min_rows_for_concurrent_read=1,"
            "optimize_read_in_order=0,optimize_aggregation_in_order=0",False),
        ("inner_parallel8",inner+" SETTINGS max_threads=8,"
            "merge_tree_min_bytes_for_concurrent_read=1,"
            "merge_tree_min_rows_for_concurrent_read=1,"
            "optimize_read_in_order=0,optimize_aggregation_in_order=0",False),
        ("native_recheck",outer+" SETTINGS max_threads=4",True),
    ]
    prefix="tslab_idrate_"+os.environ.get("GITHUB_RUN_ID","local")
    measured={x[0]:[] for x in phases}
    canonical=None
    for i in range(a.warmups+a.repeats):
        for phase,q,is_native in phases:
            qid=f"{prefix}_{phase}_{i}"
            ms,raw=post("http://127.0.0.1:18191",q+" FORMAT JSONEachRow",qid)
            v=native_grid(raw) if is_native else direct_grid(raw)
            if canonical is None:
                canonical=v
                assert len(v)>0,("empty result",phase)
            if is_native:
                if len(v)!=len(canonical):raise AssertionError(("native length",phase))
                for k,(x,y) in enumerate(zip(v,canonical)):
                    if not math.isclose(x,y,rel_tol=1e-10,abs_tol=1e-10):
                        raise AssertionError(("native values changed",phase,k,x,y))
            else:
                match(canonical,v,phase)
            if i>=a.warmups:measured[phase].append(ms)
    output={"dataset":m,"target":qname,"points":len(canonical),
            "trials":measured,"stats":{},"queries":{k:q for k,q,_ in phases}}
    for phase,q,_ in phases:
        t=measured[phase]
        output["stats"][phase]={"p50_ms":pct(t,.5),"p95_ms":pct(t,.95)}
        try:
            _,p=post("http://127.0.0.1:18191","EXPLAIN PIPELINE "+q)
            lines=p.decode().splitlines()
            selection=[line.strip() for line in lines if
                any(term in line for term in (
                    "AggregatingTransform","ReadPool","MergeTreeSelect","ReadPoolInOrder"))]
            output["stats"][phase]["pipeline"]=selection
            print("ID_RATE_PIPELINE",phase,json.dumps(selection[:40]),flush=True)
        except Exception as e:
            output["stats"][phase]["pipeline_error"]=str(e)
    vm_ms=[]
    for i in range(a.warmups+a.repeats):
        t,_=vm_call("sum(rate(bench_metric[5m]))",start,end,step)
        if i>=a.warmups:vm_ms.append(t)
    output["vm_ms"]={"p50":pct(vm_ms,.5),"p95":pct(vm_ms,.95),
                     "caution":"VM MetricsQL rate is not numerically identical by assumption"}
    post("http://127.0.0.1:18191","SYSTEM FLUSH LOGS")
    _,buf=post("http://127.0.0.1:18191",
        "SELECT query_id,read_rows,read_bytes,memory_usage,query_duration_ms,exception_code "
        "FROM system.query_log WHERE type='QueryFinish' "
        f"AND startsWith(query_id,'{prefix}') FORMAT JSONEachRow")
    qlogs={row["query_id"]:row for line in buf.splitlines() if line.strip()
           for row in [json.loads(line)]}
    for phase,q,_ in phases:
        rows=[qlogs[f"{prefix}_{phase}_{i}"] for i in range(a.warmups,a.warmups+a.repeats)]
        assert all(x["exception_code"]==0 for x in rows),(phase,rows)
        output["stats"][phase].update({
            "read_rows_p50":pct([x["read_rows"] for x in rows],.5),
            "read_bytes_p50":pct([x["read_bytes"] for x in rows],.5),
            "memory_p95":pct([x["memory_usage"] for x in rows],.95)})
    output["exact_CH_values"]=True
    pathlib.Path(a.output).write_text(json.dumps(output,indent=2)+"\n")
    print("ID_RATE_ABA",json.dumps({
        "points":len(canonical),
        "p50_ms":{k:v["p50_ms"] for k,v in output["stats"].items()},
        "read_rows":{k:v["read_rows_p50"] for k,v in output["stats"].items()},
        "memory_p95":{k:v["memory_p95"] for k,v in output["stats"].items()},
        "vm_p50":output["vm_ms"]["p50"],
        "all_exact":True},sort_keys=True),flush=True)

if __name__=="__main__":main()
