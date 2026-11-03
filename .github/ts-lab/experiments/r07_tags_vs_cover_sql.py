#!/usr/bin/env python3
"""R07 causal isolation: identical SQL sample/AVG plan, TAGS vs covering source.

Single stock O1 ClickHouse and synthetic 70k one-sample-per-ID table.
Query rewriting to SQL is held constant: only the right metadata source
and label materialization (Map vs stored String) differ. Not native planner.
"""
import argparse
import datetime as dt
import json
import os
import pathlib
import statistics
import sys
import time
import traceback

sys.path[:0]=[".github/ts-lab/experiments",".github/ts-lab-perf",
             ".github/ts-lab-bench",".github/ts-lab-cpu"]
from r07_native_cover_sql_smoke import (
    COVER,fixture,create,table,native,build_cover,query_sidecar,normalize_sql,
    DAYS,PER_DAY,METRIC,END)
from bench_granularity_v11 import sql,inner,streamed_insert,optimize_table,wait_no_merges
from bench_bucket_cxx_abca import post,normalize_any,assert_same,pct
from bench_filtered_selector_v4_sql import sql_string
from bench_native_multiclient_stock_20261103 import complete_logs,save

HOST="http://127.0.0.1:18191"
RUN=os.getenv("GITHUB_RUN_ID","local")

def query_original_tags(days,stage=None):
    q=query_sidecar(days,stage)
    begin=END-days*86400
    bday=dt.datetime.fromtimestamp(begin,dt.timezone.utc).strftime("%Y-%m-%d")
    eday=dt.datetime.fromtimestamp(END,dt.timezone.utc).strftime("%Y-%m-%d")
    old=("day>=toDate('"+bday+"') AND day<=toDate('"+eday+
         "') AND metric_name="+sql_string(METRIC))
    if stage is not None:old+=" AND stage="+sql_string(stage)
    where="metric_name="+sql_string(METRIC)
    if stage is not None:where+=" AND tags['stage']="+sql_string(stage)
    tags=inner(table("day"),"tags")
    a="SELECT id FROM "+COVER+" WHERE "+old
    b="SELECT DISTINCT id,stage FROM "+COVER+" WHERE "+old
    if q.count(a)!=1 or q.count(b)!=1:
        raise AssertionError(("Sidecar template changed",q))
    q=q.replace(a,"SELECT id FROM "+tags+" WHERE "+where)
    q=q.replace(b,"SELECT DISTINCT id,tags['stage'] AS stage FROM "+tags+" WHERE "+where)
    if COVER in q:raise AssertionError("Day-cover still referenced in TAGS control")
    return q

def phase(mode,days,stage,order,oracle):
    statement=(query_original_tags(days,stage) if mode=="tags" else
               query_sidecar(days,stage))+" FORMAT JSONEachRow"
    prefix=f"r07tagctl_{RUN}_{days}_{stage}_{mode}_{order}_"
    trials=[]
    for i in range(6):
        qid=prefix+str(i)
        ms,body=post(HOST,statement,qid)
        result=normalize_sql(body)
        assert_same(oracle,result,"r07_tags_control",(mode,days,stage,order,i))
        trials.append({"qid":qid,"ms":ms,"warmup":i<2})
    logs=complete_logs(prefix,{x["qid"] for x in trials})
    for trial in trials:
        row=logs[trial["qid"]];ev=row.get("ProfileEvents",{})
        trial.update({"cpu_ms":ev.get("OSCPUVirtualTimeMicroseconds",0)/1000,
            "marks":ev.get("SelectedMarks",0),"read_rows":row["read_rows"],
            "read_bytes":row["read_bytes"],"memory_bytes":row["memory_usage"]})
    m=trials[2:]
    med=lambda k:statistics.median(x[k] for x in m)
    return {"mode":mode,"phase":order,"trials":trials,"summary":{
        "p50_ms":pct([x["ms"] for x in m],.5),
        "p95_ms":pct([x["ms"] for x in m],.95),
        "cpu_ms":med("cpu_ms"),"marks":med("marks"),
        "read_rows":med("read_rows"),"read_bytes":med("read_bytes"),
        "peak_memory_bytes":max(x["memory_bytes"] for x in m)}}

def compare(days,stage):
    selector="" if stage is None else '{stage="'+stage+'"}'
    _,body=post(HOST,native("day",days,selector),
                f"r07tagctl_oracle_{RUN}_{days}_{stage}")
    reference=normalize_any(body)
    expected=1 if stage is not None else 38
    if len(reference)!=expected:raise AssertionError(("Wrong reference",days,stage,len(reference)))
    statements={k:(query_original_tags(days,stage) if k=="tags" else query_sidecar(days,stage))
                for k in ("tags","cover")}
    explains={k:sql("EXPLAIN indexes=1 "+v) for k,v in statements.items()}
    seq=("tags","cover","cover","tags")
    phases=[phase(mode,days,stage,i,reference) for i,mode in enumerate(seq)]
    a=[x["summary"] for x in phases if x["mode"]=="tags"]
    b=[x["summary"] for x in phases if x["mode"]=="cover"]
    avg=lambda rows,key:statistics.mean(x[key] for x in rows)
    drift=abs(a[0]["p50_ms"]-a[1]["p50_ms"])/avg(a,"p50_ms")
    summary={"valid_control":drift<=.25,"control_drift":drift}
    for key in ("p50_ms","p95_ms","cpu_ms","marks","read_rows","read_bytes","peak_memory_bytes"):
        summary["tags_"+key]=avg(a,key)
        summary["cover_"+key]=avg(b,key)
    summary["tags_over_cover_wall_ratio"]=avg(a,"p50_ms")/avg(b,"p50_ms")
    return {"case":{"days":days,"stage":stage,"native_points":len(reference)},
            "sql":statements,"explain":explains,"phases":phases,"summary":summary}

def main():
    p=argparse.ArgumentParser()
    p.add_argument("--fixture",required=True)
    p.add_argument("--output",required=True)
    args=p.parse_args()
    report={"question_id":"R07","status":"STARTED","result_type":"SQL_TAGS_VS_COVER_NOT_NATIVE","cases":{}}
    save(args.output,report)
    try:
        report["fixture"]=fixture(args.fixture)
        report["ddl"]=create("day")
        report["ingest"]=streamed_insert(table("day"),args.fixture)
        if report["ingest"]["rows"]!=DAYS*PER_DAY:raise AssertionError("Ingest not complete")
        report["optimized"]=optimize_table(table("day"))
        report["cover"]=build_cover()
        wait_no_merges()
        for days,stage in ((1,None),(1,"stage-0001"),(3,None),(14,None)):
            key=f"{days}d-"+(stage or "all")
            report["cases"][key]=compare(days,stage)
            save(args.output,report)
        report["passed"]=all(x["summary"]["valid_control"] for x in report["cases"].values())
        report["status"]="PASS_SQL_METADATA_SOURCE_FACTORIAL" if report["passed"] else "INVALID_DRIFT"
    except Exception as exc:
        report["passed"]=False
        report["status"]="FAILED"
        report["error"]=str(exc)[:2200]
        report["trace"]=traceback.format_exc()[-4200:]
    finally:
        save(args.output,report)
        compact={"passed":report.get("passed"),"status":report["status"],
                 "error":report.get("error"),"cover":report.get("cover"),
                 "cases":{k:v["summary"] for k,v in report["cases"].items()}}
        save(str(pathlib.Path(args.output).with_suffix(".compact.json")),compact)
        print("R07_METADATA_FACTOR_DONE",json.dumps(compact,separators=(",",":")),flush=True)
    if not report["passed"]:raise SystemExit(1)

if __name__=="__main__":
    main()
