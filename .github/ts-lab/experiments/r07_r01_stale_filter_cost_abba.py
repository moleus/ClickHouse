#!/usr/bin/env python3
"""R07 source-pinned R01 causal filter cost on the SAME physical TimeSeries.

Compare native PromQL / original-TAGS SQL / same SQL with exact stale-NaN bit
predicate on 2,488,320 synthetic finite samples, in 6-phase reverse order.
The SQL expressions differ by ONE WHERE predicate. No C++/sidecar writes.
"""
import argparse
import json
import os
import pathlib
import statistics
import sys
import traceback

sys.path[:0]=[".github/ts-lab/experiments",".github/ts-lab-r01",
            ".github/ts-lab-perf",".github/ts-lab-bench",".github/ts-lab-cpu"]
import r07_r01_multisample_native_tags_abba as base
from bench_granularity_v11 import sql,inner,streamed_insert,optimize_table,wait_no_merges
from bench_bucket_cxx_abca import post,normalize_any,assert_same,pct
from bench_native_multiclient_stock_20261103 import (
    complete_logs,physical,sha256,save,RSSMonitor,server_status_pid,
)
from generate_k8s_churn_fixture import BASE,DAY,expected_counts

RUN=os.getenv("GITHUB_RUN_ID","local")
HOST="http://127.0.0.1:18191"
STALE_BITS=0x7ff0000000000002
EVAL=BASE+3*DAY
ORDER=("native","raw_sql","safe_sql","safe_sql","raw_sql","native")
WARMUPS=2
MEASURED=4
CASES=("pod_broad_1d","pod_broad_3d","node_broad_1d","burst15_1d")

def safe_query(case,end):
    raw=base.tags_sql_query(case,end)
    needle=" GROUP BY id"
    if raw.count(needle)!=1:
        raise AssertionError(("Cannot safely insert predicate",raw.count(needle)))
    safe=raw.replace(needle,
        " AND reinterpretAsUInt64(tupleElement(point,2)) != "+str(STALE_BITS)+needle,1)
    if "avg(tupleElement(point,2))" not in safe:
        raise AssertionError("Underlying aggregate changed")
    return safe

def sql_output(raw,case,end):
    return base.normalize_sql(raw,case,end)

def strict_grid_parity():
    result={}
    for case in base.CASES:
        result[case]={}
        for t in (BASE+DAY,BASE+2*DAY,EVAL):
            want=base.oracle(case,t)
            q=safe_query(case,t)
            _,body=post(HOST,q,f"r07stgrid_{RUN}_{case}_{t}")
            got=sql_output(body,case,t)
            proof=assert_same(want,got,case,f"safe_grid_{t}")
            result[case][str(t)]=proof
    return result

def phase(case,kind,order,pid,expected):
    query=base.native_query(case,EVAL) if kind=="native" else (
        base.tags_sql_query(case,EVAL) if kind=="raw_sql" else safe_query(case,EVAL))
    prefix=f"r07staleperf_{RUN}_{case}_{order}_{kind}_"
    item={"variant":kind,"order":order,"trials":[]}
    with RSSMonitor(pid) as monitor:
        for i in range(WARMUPS+MEASURED):
            qid=prefix+str(i)
            ms,raw=post(HOST,query,qid)
            got=normalize_any(raw) if kind=="native" else sql_output(raw,case,EVAL)
            assert_same(expected,got,case,f"{kind}_{order}_{i}")
            item["trials"].append({"qid":qid,"ms":ms,"warmup":i<WARMUPS})
    item["rss"]=monitor.report()
    if item["rss"]["observer_overhead_fraction"]>.02:
        raise AssertionError(("RSS observer overhead",item["rss"]))
    logs=complete_logs(prefix,{r["qid"] for r in item["trials"]})
    for trial in item["trials"]:
        row=logs[trial["qid"]]
        ev=row.get("ProfileEvents",{})
        trial.update({
            "cpu_ms":ev.get("OSCPUVirtualTimeMicroseconds",0)/1000,
            "marks":ev.get("SelectedMarks",0),
            "read_rows":row["read_rows"],"read_bytes":row["read_bytes"],
            "peak_query_mem":row["memory_usage"],
            "server_ms":row["query_duration_ms"],
        })
    measured=item["trials"][WARMUPS:]
    med=lambda k:statistics.median(x[k] for x in measured)
    item["summary"]={
        "p50_ms":pct([x["ms"] for x in measured],.5),
        "p95_ms":pct([x["ms"] for x in measured],.95),
        "cpu_ms":med("cpu_ms"),"marks":med("marks"),
        "read_rows":med("read_rows"),"read_bytes":med("read_bytes"),
        "peak_query_mem":max(x["peak_query_mem"] for x in measured),
        "max_observed_rss":item["rss"]["rss_observed_max_bytes"],
    }
    print("R07_STALE_COST_PHASE",json.dumps({"case":case,"variant":kind,
           "summary":item["summary"]},separators=(",",":")),flush=True)
    return item

def compare(case,pid,reference):
    phases=[phase(case,kind,i,pid,reference) for i,kind in enumerate(ORDER)]
    groups={kind:[x["summary"] for x in phases if x["variant"]==kind]
            for kind in ("native","raw_sql","safe_sql")}
    mean=lambda items,key:statistics.mean(x[key] for x in items)
    ctrl={}
    summary={}
    for kind,parts in groups.items():
        drift=abs(parts[0]["p50_ms"]-parts[1]["p50_ms"])/mean(parts,"p50_ms")
        ctrl[kind]=drift
        for key in ("p50_ms","p95_ms","cpu_ms","marks","read_rows","read_bytes","peak_query_mem"):
            summary[kind+"_"+key]=mean(parts,key)
    summary["control_drifts"]=ctrl
    summary["valid_control"]=all(v<=.25 for v in ctrl.values())
    summary["raw_over_safe_wall_ratio"]=summary["raw_sql_p50_ms"]/summary["safe_sql_p50_ms"]
    summary["native_over_safe_wall_ratio"]=summary["native_p50_ms"]/summary["safe_sql_p50_ms"]
    summary["filter_wall_overhead_fraction"]=(
        summary["safe_sql_p50_ms"]/summary["raw_sql_p50_ms"]-1)
    summary["filter_cpu_overhead_fraction"]=(
        summary["safe_sql_cpu_ms"]/summary["raw_sql_cpu_ms"]-1)
    return {"phases":phases,"summary":summary,
            "queries":{"native":base.native_query(case,EVAL),
                "raw_sql":base.tags_sql_query(case,EVAL),
                "safe_sql":safe_query(case,EVAL)}}

def main():
    ap=argparse.ArgumentParser()
    ap.add_argument("--fixture",required=True)
    ap.add_argument("--manifest",required=True)
    ap.add_argument("--pid-file",required=True)
    ap.add_argument("--output",required=True)
    args=ap.parse_args()
    base.TABLE="tslab_r07_stalesafe_r01"
    report={"question_id":"R07","status":"STARTED","passed":False,"cases":{},
            "errors":[],"source_sha":base.SOURCE_SHA,"binary_sha256":base.BINARY_SHA,
            "fixture_sha256":base.FIXTURE_SHA,"stale_filter_bits":hex(STALE_BITS)}
    save(args.output,report)
    try:
        manifest=json.loads(pathlib.Path(args.manifest).read_text())
        expected_rows,expected_samples=expected_counts(3,400)
        if (sha256(args.fixture)!=base.FIXTURE_SHA or
            manifest.get("sha256")!=base.FIXTURE_SHA or
            manifest.get("samples")!=2488320 or
            manifest.get("series")!=28896 or
            manifest.get("stagger_policy")!="node start = (slot * 317) % 10800 seconds" or
            manifest.get("rows_by_tier")!=expected_rows or
            manifest.get("samples_by_tier")!=expected_samples):
            raise AssertionError("Original pinned R01 fixture mismatch")
        report["manifest"]=manifest
        report["ddl"]=base.ddl()
        report["ingest"]=streamed_insert(base.TABLE,args.fixture)
        if report["ingest"]["rows"]!=28896:
            raise AssertionError("Incomplete TimeSeries ingest")
        report["optimize"]=optimize_table(base.TABLE)
        if any(isinstance(x,dict) for x in report["optimize"].values()):
            raise AssertionError(("Merge error",report["optimize"]))
        wait_no_merges()
        report["before"]=physical(base.TABLE)
        report["grid"]=strict_grid_parity()
        if sum(map(len,report["grid"].values()))!=18:
            raise AssertionError("Incomplete 18 exact parity checks")
        save(args.output,report)
        pid=server_status_pid(args.pid_file)
        for case in CASES:
            report["cases"][case]=compare(case,pid,base.oracle(case,EVAL))
            save(args.output,report)
        report["after"]=physical(base.TABLE)
        if report["before"]!=report["after"]:
            raise AssertionError("Physical merge state changed during ABBA")
        report["passed"]=all(x["summary"]["valid_control"] for x in report["cases"].values())
        report["status"]="PASS_NATIVE_RAW_STALE_SAFE_ABBA" if report["passed"] else "INVALID_DRIFT"
    except Exception as e:
        report["status"]="FAILED"
        report["errors"].append({"error":str(e)[:2200],"trace":traceback.format_exc()[-4800:]})
    finally:
        save(args.output,report)
        result={"passed":report["passed"],"status":report["status"],
                "errors":report["errors"],"grid_points":sum(map(len,report.get("grid",{}).values())),
                "cases":{k:v["summary"] for k,v in report["cases"].items()},
                "physical_before":report.get("before")}
        save(str(pathlib.Path(args.output).with_suffix(".compact.json")),result)
        print("R07_STALE_FILTER_COST_DONE",json.dumps(result,separators=(",",":")),flush=True)
    if not report["passed"]:raise SystemExit(1)

if __name__=="__main__":
    main()
