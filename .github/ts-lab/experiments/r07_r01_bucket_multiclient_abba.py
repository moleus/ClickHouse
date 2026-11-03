#!/usr/bin/env python3
"""R07: 1/4/8 synchronized clients native vs stale-safe bucket-reduced SQL.

Uses 2.488m staggered R01 synthetic gauge points, pinned stock O1, identical
TAGS/SAMPLES, no new index/source mutation and no C++ compilation. Each client
must return exactly the independently verified native PromQL labels/timestamp/
values. A/B/B/A nested within each workload/client count, no cancel on push.
"""
import argparse
import concurrent.futures
import json
import os
import pathlib
import statistics
import sys
import threading
import time
import traceback

sys.path[:0]=[".github/ts-lab/experiments",".github/ts-lab-r01",
             ".github/ts-lab-perf",".github/ts-lab-bench",".github/ts-lab-cpu"]
import r07_r01_bucket_reduce_abba as bucket
base=bucket.base
from bench_granularity_v11 import sql,inner,streamed_insert,optimize_table,wait_no_merges
from bench_bucket_cxx_abca import post,normalize_any,assert_same,pct
from bench_native_multiclient_stock_20261103 import (
    complete_logs,physical,sha256,save,RSSMonitor,server_status_pid,
)
from generate_k8s_churn_fixture import DAY,BASE,expected_counts

RUN=os.getenv("GITHUB_RUN_ID","local")
HOST="http://127.0.0.1:18191"
EVAL=BASE+3*DAY
CASES=("node_broad_3d","pod_broad_3d")
CLIENTS=(1,4,8)
WAVES=5
WARMUP_WAVES=1
ORDER=("native","bucket","bucket","native")

def statement(case,variant):
    if variant=="native":return base.native_query(case,EVAL)
    if variant=="bucket":return bucket.bucket_sql_query(case,EVAL)
    raise AssertionError(("Unknown variant",variant))

def run_wave(executor,n,stmt,case,variant,order,wave,reference):
    barrier=threading.Barrier(n+1,timeout=45)
    prefix=f"r07buckcap_{RUN}_{case}_{n}_{variant}_{order}_w{wave}_"
    def one(client):
        barrier.wait()
        started=time.perf_counter()
        qid=prefix+str(client)
        ms,raw=post(HOST,stmt,qid)
        finished=time.perf_counter()
        parsed=normalize_any(raw) if variant=="native" else base.normalize_sql(raw,case,EVAL)
        assert_same(reference,parsed,case,f"{variant}_{n}_{order}_{wave}_{client}")
        return {"qid":qid,"started":started,"ended":finished,"client_ms":ms,
                "client_index":client,"wave":wave,"warmup":wave<WARMUP_WAVES}
    tasks=[executor.submit(one,i) for i in range(n)]
    barrier.wait()
    trials=[f.result() for f in tasks]
    began=min(x["started"] for x in trials)
    ended=max(x["ended"] for x in trials)
    span=max(ended-began,.000001)
    qps=n/span
    if not all(x["client_ms"]>0 for x in trials):
        raise AssertionError("Missing client duration")
    return {"wave":wave,"clients":n,"warmup":wave<WARMUP_WAVES,
            "span_seconds":span,"wave_qps":qps,"trials":trials}

def phase(case,n,variant,order,pid,reference):
    stmt=statement(case,variant)
    item={"variant":variant,"n_clients":n,"order":order,"waves":[]}
    with RSSMonitor(pid) as monitor:
        with concurrent.futures.ThreadPoolExecutor(max_workers=n) as executor:
            for wave in range(WAVES):
                result=run_wave(executor,n,stmt,case,variant,order,wave,reference)
                item["waves"].append(result)
                print("R07_CAP_WAVE",json.dumps({
                    "case":case,"clients":n,"variant":variant,
                    "order":order,"wave":wave,"qps":result["wave_qps"],
                    "span_s":result["span_seconds"]
                },separators=(",",":")),flush=True)
    item["rss"]=monitor.report()
    if item["rss"]["observer_overhead_fraction"]>.02:
        raise AssertionError(("RSS monitor overhead too high",item["rss"]))
    all_trials=[v for w in item["waves"] for v in w["trials"]]
    logs=complete_logs(f"r07buckcap_{RUN}_{case}_{n}_{variant}_{order}_",
                       {x["qid"] for x in all_trials})
    for trial in all_trials:
        row=logs[trial["qid"]]
        events=row.get("ProfileEvents",{})
        trial.update({
            "cpu_ms":events.get("OSCPUVirtualTimeMicroseconds",0)/1000,
            "selected_marks":events.get("SelectedMarks",0),
            "read_rows":row["read_rows"],"read_bytes":row["read_bytes"],
            "query_mem":row["memory_usage"],
            "server_ms":row["query_duration_ms"]
        })
    measured=[x for x in all_trials if not x["warmup"]]
    waves=[x for x in item["waves"] if not x["warmup"]]
    if len(measured)!=n*(WAVES-WARMUP_WAVES):
        raise AssertionError("Incomplete parallel samples")
    med=lambda key:statistics.median(x[key] for x in measured)
    item["summary"]={
        "observations":len(measured),
        "p50_ms":pct([x["client_ms"] for x in measured],.5),
        "p95_ms":pct([x["client_ms"] for x in measured],.95),
        "query_cpu_ms":med("cpu_ms"),"read_rows":med("read_rows"),
        "read_bytes":med("read_bytes"),"selected_marks":med("selected_marks"),
        "query_mem_peak_bytes":max(x["query_mem"] for x in measured),
        "wave_qps_median":statistics.median(w["wave_qps"] for w in waves),
        "wave_qps_min":min(w["wave_qps"] for w in waves),
        "wave_qps_max":max(w["wave_qps"] for w in waves),
        "process_rss_observed_max":item["rss"]["rss_observed_max_bytes"],
    }
    return item

def compare(case,n,pid,reference):
    phases=[phase(case,n,variant,i,pid,reference)
            for i,variant in enumerate(ORDER)]
    group={v:[p["summary"] for p in phases if p["variant"]==v]
           for v in ("native","bucket")}
    mean=lambda x,k:statistics.mean(y[k] for y in x)
    summary={}
    drift={}
    for kind,sub in group.items():
        drift[kind]=abs(sub[0]["p50_ms"]-sub[1]["p50_ms"])/mean(sub,"p50_ms")
        for k in ("p50_ms","p95_ms","query_cpu_ms","read_rows","read_bytes",
                  "selected_marks","query_mem_peak_bytes","wave_qps_median",
                  "process_rss_observed_max"):
            summary[kind+"_"+k]=mean(sub,k)
    summary["control_drift"]=drift
    summary["valid_controls"]=all(x<=.25 for x in drift.values())
    summary["native_over_bucket_p50_ratio"]=summary["native_p50_ms"]/summary["bucket_p50_ms"]
    summary["native_over_bucket_p95_ratio"]=summary["native_p95_ms"]/summary["bucket_p95_ms"]
    summary["bucket_over_native_wave_qps_ratio"]=summary["bucket_wave_qps_median"]/summary["native_wave_qps_median"]
    summary["simultaneous_clients"]=n
    return {"phases":phases,"summary":summary}

def main():
    ap=argparse.ArgumentParser()
    ap.add_argument("--fixture",required=True)
    ap.add_argument("--manifest",required=True)
    ap.add_argument("--pid-file",required=True)
    ap.add_argument("--output",required=True)
    args=ap.parse_args()
    base.TABLE="tslab_r07_bucket_capacity"
    report={"question_id":"R07","status":"STARTED","passed":False,"errors":{},
            "source_sha":base.SOURCE_SHA,"binary_sha256":base.BINARY_SHA,
            "fixture_sha256":base.FIXTURE_SHA,"cases":{},"client_counts":CLIENTS,
            "waves_per_phase":WAVES,"warmup_waves":WARMUP_WAVES}
    save(args.output,report)
    try:
        manifest=json.loads(pathlib.Path(args.manifest).read_text())
        rows,points=expected_counts(3,400)
        if (manifest.get("sha256")!=base.FIXTURE_SHA or
            sha256(args.fixture)!=base.FIXTURE_SHA or
            manifest.get("series")!=28896 or manifest.get("samples")!=2488320 or
            manifest.get("stagger_policy")!="node start = (slot * 317) % 10800 seconds" or
            manifest.get("rows_by_tier")!=rows or manifest.get("samples_by_tier")!=points):
            raise AssertionError("Frozen R01 source/fixture mismatch")
        report["manifest"]=manifest
        report["create"]=base.ddl()
        report["ingest"]=streamed_insert(base.TABLE,args.fixture)
        if report["ingest"]["rows"]!=28896:raise AssertionError("Incomplete ingest")
        report["optimize"]=optimize_table(base.TABLE)
        if any(isinstance(x,dict) for x in report["optimize"].values()):
            raise AssertionError("OPTIMIZE incomplete")
        wait_no_merges()
        report["physical_before"]=physical(base.TABLE)
        report["grid"]=bucket.grid_parity()
        if sum(len(x) for x in report["grid"].values())!=18:
            raise AssertionError("Missing independent native output grid")
        pid=server_status_pid(args.pid_file)
        save(args.output,report)
        for case in CASES:
            report["cases"][case]={}
            reference=base.oracle(case,EVAL)
            for n in CLIENTS:
                report["cases"][case][str(n)]=compare(case,n,pid,reference)
                save(args.output,report)
        report["physical_after"]=physical(base.TABLE)
        if report["physical_before"]!=report["physical_after"]:
            raise AssertionError("Physical merge/TTL state changed while reading")
        report["passed"]=all(x["summary"]["valid_controls"]
            for v in report["cases"].values() for x in v.values())
        report["status"]="PASS_MULTICLIENT_ABBA" if report["passed"] else "INVALID_CONTROL_DRIFT"
    except Exception as exc:
        report["status"]="FAILED"
        report["errors"]={"fatal":str(exc)[:2300],"trace":traceback.format_exc()[-5000:]}
    finally:
        save(args.output,report)
        compact={"status":report["status"],"passed":report["passed"],
                 "errors":report["errors"],
                 "grid_parity":sum(len(x) for x in report.get("grid",{}).values()),
                 "cases":{c:{n:d["summary"] for n,d in v.items()}
                          for c,v in report["cases"].items()},
                 "physical_before":report.get("physical_before")}
        save(str(pathlib.Path(args.output).with_suffix(".compact.json")),compact)
        print("R07_BUCKET_CAPACITY_DONE",json.dumps(compact,separators=(",",":")),flush=True)
    if not report["passed"]:raise SystemExit(1)

if __name__=="__main__":
    main()
