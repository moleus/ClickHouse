#!/usr/bin/env python3
"""Query-path A/B for one-sample Jenkins stage events on CH TimeSeries, VM, MergeTree.

Query: avg by(stage)(avg_over_time(metric{filters}[1d])) at daily eval steps.
One input sample per label set makes its result equivalent to native event SQL
AVG(duration) by day/stage. Verify ALL labels, timestamps, values at each step.
This does NOT assert equivalence for raw instant gauge PromQL or rate().
"""
import argparse
import datetime as dt
import json
import math
import os
import pathlib
import random
import statistics
import time
import urllib.error
import urllib.parse
import urllib.request


def pct(nums,p):
    vals=sorted(nums)
    assert vals
    x=(len(vals)-1)*p
    a=int(x)
    return round(vals[a]+(vals[min(a+1,len(vals)-1)]-vals[a])*(x-a),3)


def post(url,query,query_id=None):
    params={"allow_experimental_time_series_table":1,"wait_end_of_query":1,
            "max_execution_time":340,"max_memory_usage":3000000000,"max_threads":4}
    if query_id:
        params["query_id"]=query_id
    link=url.rstrip("/")+"/?"+urllib.parse.urlencode(params)
    req=urllib.request.Request(link,data=query.encode(),method="POST")
    start=time.perf_counter()
    try:
        with urllib.request.urlopen(req,timeout=355) as response: data=response.read()
    except urllib.error.HTTPError as e:
        raise RuntimeError("CH HTTP "+str(e.code)+": "+e.read().decode("utf-8","replace")[:1000]) from e
    return round((time.perf_counter()-start)*1000,3),data


def query_vm(root,expr,start,end,step):
    params={"query":expr,"start":start,"end":end,"step":step,"nocache":1}
    url=root.rstrip("/")+"/api/v1/query_range?"+urllib.parse.urlencode(params)
    begin=time.perf_counter()
    with urllib.request.urlopen(url,timeout=355) as response:
        body=response.read()
    cost=round((time.perf_counter()-begin)*1000,3)
    data=json.loads(body)
    if data.get("status")!="success":
        raise RuntimeError(("VM not success",data))
    return cost,data


def epoch(v):
    if isinstance(v,(int,float)):
        return int(v)
    t=dt.datetime.fromisoformat(str(v).replace(" ","T").replace("Z","+00:00"))
    if t.tzinfo is None:t=t.replace(tzinfo=dt.timezone.utc)
    return int(t.timestamp())


def normalized_ch(raw):
    obj=[json.loads(x) for x in raw.splitlines() if x.strip()]
    result={}
    for row in obj:
        labels=dict(row.get("tags",[]))
        if set(labels)!={"stage"}:
            raise AssertionError(("unexpected ClickHouse labels",labels))
        for stamp,val in row.get("samples",[]):
            key=(labels["stage"],epoch(stamp))
            if key in result:raise AssertionError(("CH duplicate",key))
            if val is not None:result[key]=float(val)
    return result


def normalized_vm(data):
    result={}
    for row in data["data"]["result"]:
        metric=row.get("metric",{})
        if set(metric)!={"stage"}:
            raise AssertionError(("unexpected VM labels",metric))
        for stamp,val in row.get("values",[]):
            key=(metric["stage"],epoch(stamp))
            if key in result:raise AssertionError(("VM duplicate",key))
            if val is not None and val!="NaN":result[key]=float(val)
    return result


def normalized_events(raw):
    result={}
    for line in raw.splitlines():
        if line.strip():
            v=json.loads(line)
            key=(v["stage"],epoch(v["epoch"]))
            if key in result:raise AssertionError(("SQL duplicate",key))
            result[key]=float(v["value"])
    return result


def compare(reference,actual,case,engine):
    missing=set(reference)-set(actual)
    extra=set(actual)-set(reference)
    if missing or extra:
        raise AssertionError((case,engine,"key mismatch",len(missing),len(extra),
                              sorted(missing)[:4],sorted(extra)[:4]))
    absolute=0.0
    for key,v in reference.items():
        w=actual[key]
        if not math.isclose(v,w,rel_tol=1e-8,abs_tol=1e-7):
            raise AssertionError((case,engine,"value",key,v,w))
        absolute=max(absolute,abs(v-w))
    return {"points":len(reference),"max_abs_error":absolute,"exact_stage_timestamp_keys":True}


def main():
    p=argparse.ArgumentParser()
    p.add_argument("--manifest",required=True)
    p.add_argument("--output",required=True)
    p.add_argument("--ch",default="http://127.0.0.1:18191")
    p.add_argument("--vm",default="http://127.0.0.1:8430")
    p.add_argument("--repeats",type=int,default=4)
    p.add_argument("--warmups",type=int,default=1)
    a=p.parse_args()
    m=json.loads(pathlib.Path(a.manifest).read_text())
    start,end,step=m["query_start_epoch"],m["query_end_epoch"],m["step_seconds"]
    name=m["metric"]
    cases=[
        ("all","",""),
        ("branch_main",'{branch="main"}',"AND branch='main'"),
        ("project_00",'{project="project-00"}',"AND project='project-00'"),
        ("stage_0000_0009",'{stage=~"stage-000[0-9]"}',"AND stage IN ('stage-0000','stage-0001','stage-0002','stage-0003','stage-0004','stage-0005','stage-0006','stage-0007','stage-0008','stage-0009')"),
    ]
    prefix="tslab_jenkins_"+os.environ.get("GITHUB_RUN_ID","local")
    all_results={"manifest":m,"run_id":os.environ.get("GITHUB_RUN_ID"),
                 "semantics":"Daily [1d] range; one sample per series; native SQL avg of events in previous day; no PromQL rate",
                 "cases":{}}
    rng=random.Random(134011)
    for case,matcher,sql_where in cases:
        expr=f"avg by(stage) (avg_over_time({name}{matcher}[1d]))"
        sql=(f"SELECT toUInt64(toUnixTimestamp(toStartOfDay(ts))+86400) AS epoch, "
             f"stage,avg(duration) AS value FROM jenkins_events "
             f"WHERE ts >= fromUnixTimestamp({start-86400}) "
             f"AND ts < fromUnixTimestamp({end}) {sql_where} "
             f"GROUP BY epoch,stage ORDER BY epoch,stage FORMAT JSONEachRow")
        ch_sql=f"SELECT * FROM prometheusQueryRange(jenkins_ts, '{expr}', {start}, {end}, {step}) FORMAT JSONEachRow"
        metrics={k:[] for k in ["ch_ts","vm","ch_sql"]}
        reference=None
        validators={}
        orders=[]
        for i in range(a.warmups+a.repeats):
            observed={}
            order=["ch_ts","vm","ch_sql"]
            rng.shuffle(order)
            orders.append(order)
            for engine in order:
                if engine=="vm":
                    ms,body=query_vm(a.vm,expr,start,end,step)
                    current=normalized_vm(body)
                elif engine=="ch_sql":
                    qid=f"{prefix}_{case}_{engine}_{i}"
                    ms,raw=post(a.ch,sql,qid)
                    current=normalized_events(raw)
                else:
                    qid=f"{prefix}_{case}_{engine}_{i}"
                    ms,raw=post(a.ch,ch_sql,qid)
                    current=normalized_ch(raw)
                observed[engine]=current
                if i>=a.warmups:
                    metrics[engine].append(ms)
            canonical=observed["ch_sql"]
            if reference is None:reference=canonical
            assert reference==canonical,("SQL changed between trials",case,i)
            # Assert exact stage/timestamp keys and numeric equivalence in
            # EVERY measured repetition, not just one cold invocation.
            for engine in ["ch_ts","vm"]:
                validators[engine]=compare(canonical,observed[engine],case,engine)
            if i>=a.warmups:
                print("JENKINS_TRIAL",case,i,dict((e,metrics[e][-1]) for e in metrics),flush=True)
        all_results["cases"][case]={"query":expr,"native_sql":sql,
            "points_expected":len(reference),"validation":validators,
            "timings":{e:{"p50_ms":pct(v,.5),"p95_ms":pct(v,.95),
                           "repetitions_ms":v} for e,v in metrics.items()},
            "repeat_orders":orders}
        print("JENKINS_QUERY_CASE",case,json.dumps({
            "points":len(reference),
            "p50_ms":{e:all_results["cases"][case]["timings"][e]["p50_ms"] for e in metrics},
            "validation":validators},sort_keys=True),flush=True)
    post(a.ch,"SYSTEM FLUSH LOGS")
    _,log=post(a.ch,"SELECT query_id,read_rows,read_bytes,memory_usage,query_duration_ms,exception_code "
                    "FROM system.query_log WHERE type='QueryFinish' "
                    f"AND startsWith(query_id,'{prefix}') FORMAT JSONEachRow")
    by_id={v["query_id"]:v for l in log.splitlines() if l.strip() for v in [json.loads(l)]}
    for case in all_results["cases"]:
        for eng in ("ch_ts","ch_sql"):
            ids=[f"{prefix}_{case}_{eng}_{i}" for i in range(a.warmups,a.warmups+a.repeats)]
            rows=[by_id[q] for q in ids if q in by_id]
            if len(rows)!=a.repeats:raise AssertionError(("QueryFinish missing",case,eng,len(rows)))
            assert all(r["exception_code"]==0 for r in rows),(case,eng,rows)
            all_results["cases"][case]["timings"][eng]["query_log"]={
                "read_rows_p50":pct([x["read_rows"] for x in rows],.5),
                "read_bytes_p50":pct([x["read_bytes"] for x in rows],.5),
                "memory_p50_bytes":pct([x["memory_usage"] for x in rows],.5),
                "memory_p95_bytes":pct([x["memory_usage"] for x in rows],.95),
                "peak_memory_bytes":max(x["memory_usage"] for x in rows)}
    dest=pathlib.Path(a.output)
    dest.parent.mkdir(parents=True,exist_ok=True)
    dest.write_text(json.dumps(all_results,indent=2)+"\n")
    print("JENKINS_QUERY_PATH_DONE",json.dumps({
        "cases":len(cases),"all_engines_values_equal_to_event_sql":True,
        "series":m["total_series"],"run_id":all_results["run_id"]},sort_keys=True),flush=True)


if __name__=="__main__":
    main()
