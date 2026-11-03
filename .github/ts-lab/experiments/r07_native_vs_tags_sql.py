#!/usr/bin/env python3
"""R07: native PromQL vs same-source direct SQL with ORIGINAL TAGS, no sidecar.

This isolates the SQL query rewrite itself, with no additional index writes.
Restricted one-sample-per-ID synthetic fixture and finite avg_over_time.
Not general PromQL semantic or production performance proof.
"""
import argparse
import json
import os
import pathlib
import statistics
import sys
import traceback

sys.path[:0]=[".github/ts-lab/experiments",".github/ts-lab-perf",
            ".github/ts-lab-bench",".github/ts-lab-cpu"]
from r07_tags_vs_cover_sql import query_original_tags, normalize_sql
from r15_native_partition_probe import DAYS,PER_DAY,fixture,create,table,native,identity
from bench_granularity_v11 import sql,streamed_insert,optimize_table,wait_no_merges
from bench_bucket_cxx_abca import post,normalize_any,assert_same,pct
from bench_native_multiclient_stock_20261103 import complete_logs,save

HOST="http://127.0.0.1:18191"
RUN=os.getenv("GITHUB_RUN_ID","local")

def phase(which,days,stage,order,oracle):
    selector="" if stage is None else '{stage="'+stage+'"}'
    statement=(native("day",days,selector) if which=="native" else
               query_original_tags(days,stage)+" FORMAT JSONEachRow")
    prefix=f"r07nativetag_{RUN}_{days}_{stage}_{which}_{order}_"
    rows=[]
    for n in range(6):
        qid=prefix+str(n)
        ms,body=post(HOST,statement,qid)
        candidate=normalize_any(body) if which=="native" else normalize_sql(body)
        assert_same(oracle,candidate,"r07_native_tags",(which,days,stage,order,n))
        rows.append({"qid":qid,"ms":ms,"warmup":n<2})
    logs=complete_logs(prefix,{x["qid"] for x in rows})
    for trial in rows:
        r=logs[trial["qid"]];ev=r.get("ProfileEvents",{})
        trial.update({"cpu_ms":ev.get("OSCPUVirtualTimeMicroseconds",0)/1000,
           "marks":ev.get("SelectedMarks",0),"read_rows":r["read_rows"],
           "read_bytes":r["read_bytes"],"peak_memory":r["memory_usage"]})
    measured=rows[2:]
    med=lambda k:statistics.median(x[k] for x in measured)
    return {"which":which,"phase":order,"trials":rows,"summary":{
        "p50_ms":pct([x["ms"] for x in measured],.5),
        "p95_ms":pct([x["ms"] for x in measured],.95),
        "cpu_ms":med("cpu_ms"),"marks":med("marks"),
        "read_rows":med("read_rows"),"read_bytes":med("read_bytes"),
        "peak_memory":max(x["peak_memory"] for x in measured)}}

def compare(days,stage):
    selector="" if stage is None else '{stage="'+stage+'"}'
    _,raw=post(HOST,native("day",days,selector),
        f"r07native_tag_oracle_{RUN}_{days}_{stage}")
    reference=normalize_any(raw)
    expected=1 if stage is not None else 38
    if len(reference)!=expected:raise AssertionError(("Empty/wrong native output",len(reference)))
    direct=query_original_tags(days,stage)
    plan=sql("EXPLAIN indexes=1 "+direct)
    phases=[phase(w,days,stage,i,reference)
            for i,w in enumerate(("native","tags","tags","native"))]
    a=[x["summary"] for x in phases if x["which"]=="native"]
    b=[x["summary"] for x in phases if x["which"]=="tags"]
    mean=lambda group,k:statistics.mean(x[k] for x in group)
    drift=abs(a[0]["p50_ms"]-a[1]["p50_ms"])/mean(a,"p50_ms")
    summary={"valid_control":drift<=.25,"control_drift":drift}
    for key in ("p50_ms","p95_ms","cpu_ms","marks","read_rows","read_bytes","peak_memory"):
        summary["native_"+key]=mean(a,key)
        summary["tags_sql_"+key]=mean(b,key)
    summary["native_over_tags_sql_wall_ratio"]=mean(a,"p50_ms")/mean(b,"p50_ms")
    return {"case":{"days":days,"stage":stage,"native_points":len(reference)},
            "sql":direct,"explain":plan,"phases":phases,"summary":summary}

def main():
    p=argparse.ArgumentParser()
    p.add_argument("--fixture",required=True)
    p.add_argument("--output",required=True)
    args=p.parse_args()
    report={"question_id":"R07","status":"STARTED","assertions":"no covering sidecar created","cases":{}}
    save(args.output,report)
    try:
        report["fixture"]=fixture(args.fixture)
        report["create"]=create("day")
        report["ingest"]=streamed_insert(table("day"),args.fixture)
        if report["ingest"]["rows"]!=PER_DAY*DAYS:raise AssertionError("bad ingest count")
        report["optimized"]=optimize_table(table("day"))
        wait_no_merges()
        report["physical"]=identity("day")
        for days,stage in ((1,None),(1,"stage-0001"),(3,None),(14,None)):
            key=str(days)+"d-"+(stage or "all")
            report["cases"][key]=compare(days,stage)
            save(args.output,report)
        report["passed"]=all(x["summary"]["valid_control"] for x in report["cases"].values())
        report["status"]="PASS_SQL_REWRITE_NO_SIDECAR" if report["passed"] else "INVALID_CONTROL_DRIFT"
    except Exception as exc:
        report["passed"]=False
        report["status"]="FAILED"
        report["error"]=str(exc)
        report["trace"]=traceback.format_exc()[-5000:]
    finally:
        save(args.output,report)
        summary={"passed":report.get("passed"),"status":report["status"],
            "error":report.get("error"),"fixture":report.get("fixture"),
            "cases":{k:v["summary"] for k,v in report["cases"].items()}}
        save(str(pathlib.Path(args.output).with_suffix(".compact.json")),summary)
        print("R07_NATIVE_TAGS_ONLY",json.dumps(summary,separators=(",",":")),flush=True)
    if not report["passed"]:raise SystemExit(1)

if __name__=="__main__":
    main()
