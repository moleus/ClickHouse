#!/usr/bin/env python3
"""R10 native TimeSeries bucket 3600 vs 10800 at 1/4/8 concurrent clients.

Fully identical frozen 400-node R01 multi-cadence 3d data with 2.49m
samples. Two RAW UUID TimeSeries tables vary only SAMPLES bucket step.
SOURCE/BINARY/FIXTURE SHA pinned; stable one-part layouts, STOP MERGES;
each ABBA phase restarts ClickHouse to bound RSS allocator retention.
Each phase 2 warmup +8 measured concurrent waves; native PromQL full
nonempty output parity. Measures p50/p95/p99, CPU, runnable wait,
throughput, query/whole-server memory; no full-thread /proc sampling.
"""
import argparse
import concurrent.futures
import json
import os
import pathlib
import statistics
import subprocess
import sys
import time
import traceback

sys.path[:0] = [
    ".github/ts-lab-r10", ".github/ts-lab-r01",
    ".github/ts-lab-perf", ".github/ts-lab-bench",
    ".github/ts-lab-cpu",
]
from bench_granularity_v11 import streamed_insert, inner, optimize_table, sql, wait_no_merges
from bench_bucket_cxx_abca import post, normalize_any, assert_same
from bench_jenkins_querypath import pct
from bench_native_multiclient_stock_20261103 import (
    RSSMonitor, complete_logs, physical, run_wave, save, server_status_pid, sha256
)
from bench_k8s_mixed_native import (
    CASES, query as r01query, jsonrows, DAY, SOURCE_SHA, BINARY_SHA
)
from bench_bucket_id_factorial import physical_audit, FIXTURE_SHA
from generate_k8s_churn_fixture import BASE, LIFE, expected_counts

HOST="http://127.0.0.1:18191"
CASES_DEFAULT=("node_broad","pod_namespace","pod_broad")
CLIENTS_DEFAULT=(1,4,8)
WAVES=10
WARMUP=2
A=3600
B=10800
TABLES={A:"tslab_r10_read_b3600",B:"tslab_r10_read_b10800"}
RUN=os.getenv("GITHUB_RUN_ID","local")


def create_raw(name, step):
    sql(
        f"CREATE TABLE {name} ENGINE=TimeSeries "
        f"SETTINGS recent_samples_ttl_seconds=0,"
        f"samples_bucket_step_seconds={step} "
        "SAMPLES INNER COLUMNS (id Tuple(UInt64,UUID)) "
        "TAGS INNER COLUMNS ("
        "id Tuple(UInt64,UUID) DEFAULT tuple(sipHash64(metric_name),"
        "reinterpretAsUUID(sipHash128(tags))),"
        "metric_name LowCardinality(String),"
        "tags Map(LowCardinality(String),String),"
        "INDEX tags_idx tags TYPE text(tokenizer = 'keyValuePairs') "
        "GRANULARITY 100000000) "
        "TIME RANGES INNER COLUMNS (id Tuple(UInt64,UUID))"
    )


def query(table, case, begin, end):
    result = r01query(table,case,begin,end)
    replacement=result.replace("SETTINGS max_threads=4,","SETTINGS max_threads=2,")
    if replacement==result:
        raise AssertionError("R01 query source changed max_threads setting")
    return replacement


def phase(case, n, order, step, end, reference, expected, ticks, args, before):
    subprocess.run([args.restart_script,args.binary,
                    f"r10read_{case}_{n}_{order}_{step}"],
                   timeout=260,check=True)
    pid=server_status_pid(args.pid_file)
    wait_no_merges()
    current={t:physical(t) for t in TABLES.values()}
    if current!=before:
        raise AssertionError(("Read-only physical parts drift",current,before))
    table=TABLES[step]
    stmt=query(table,case,end-2*DAY,end)
    prefix=f"r10read_{RUN}_{case}_{n}_{order}_{step}"
    record={"case":case,"clients":n,"order":order,"step":step,
            "table":table,"query":stmt,"server_pid":pid,
            "waves":[],"trials":[]}
    with RSSMonitor(pid) as observer:
        with concurrent.futures.ThreadPoolExecutor(max_workers=n) as executor:
            for wave in range(WAVES):
                current=run_wave(
                    executor,n,stmt,case,prefix,wave,reference,expected,ticks
                )
                record["waves"].append(current)
                record["trials"].extend(current["trials"])
    record["rss"]=observer.report()
    if record["rss"]["observer_overhead_fraction"]>.02:
        raise AssertionError(("RSS observer too expensive",record["rss"]))
    ids={t["qid"] for t in record["trials"]}
    logs=complete_logs(prefix,ids)
    for trial in record["trials"]:
        row=logs[trial["qid"]]
        ev=row.get("ProfileEvents",{})
        if ev.get("OSCPUVirtualTimeMicroseconds",0)<=0:
            raise AssertionError(("Missing real CPU accounting",trial["qid"]))
        trial.update({
            "server_ms":row["query_duration_ms"],
            "cpu_ms":round(ev["OSCPUVirtualTimeMicroseconds"]/1000,3),
            "runqueue_ms":round(ev.get("OSCPUWaitMicroseconds",0)/1000,3),
            "pool_lock_wait_ms":round(
                (ev.get("GlobalThreadPoolLockWaitMicroseconds",0)+
                 ev.get("LocalThreadPoolLockWaitMicroseconds",0))/1000,3),
            "query_memory_bytes":row["memory_usage"],
            "read_rows":row["read_rows"],"read_bytes":row["read_bytes"],
            "read_compressed_bytes":ev.get("ReadCompressedBytes",0),
            "selected_marks":ev.get("SelectedMarks",0),
        })
    measured=[x for x in record["trials"] if not x["warmup"]]
    waves=[x for x in record["waves"] if not x["warmup"]]
    duration=sum(x["duration_s"] for x in waves)
    if len(measured)!=n*(WAVES-WARMUP) or duration<=0:
        raise AssertionError(("Wrong measured batch",n,len(measured),duration))
    mean=lambda name:statistics.mean(t[name] for t in measured)
    ms=[t["client_ms"] for t in measured]
    record["metrics"]={
        "clients":n,"requests":len(measured),
        "p50_ms":pct(ms,.50),"p95_ms":pct(ms,.95),"p99_ms":pct(ms,.99),
        "throughput_rps":round(len(measured)/duration,4),
        "mean_cpu_per_request_ms":round(mean("cpu_ms"),3),
        "mean_runqueue_wait_per_request_ms":round(mean("runqueue_ms"),3),
        "mean_pool_lock_wait_per_request_ms":round(mean("pool_lock_wait_ms"),3),
        "mean_read_rows":round(mean("read_rows"),3),
        "mean_compressed_bytes":round(mean("read_compressed_bytes"),3),
        "mean_selected_marks":round(mean("selected_marks"),3),
        "max_query_memory_bytes":max(t["query_memory_bytes"] for t in measured),
        "peak_observed_process_rss_bytes":record["rss"]["rss_observed_max_bytes"],
        "rss_observer_overhead":record["rss"]["observer_overhead_fraction"],
    }
    print("R10_CONCURRENT_PHASE",json.dumps({
        "case":case,"clients":n,"order":order,"step":step,
        "metrics":record["metrics"]},separators=(",",":")),flush=True)
    return record


def compare(phases):
    a=[x["metrics"] for x in phases if x["step"]==A]
    b=[x["metrics"] for x in phases if x["step"]==B]
    if len(a)!=2 or len(b)!=2:raise AssertionError("Incomplete ABBA")
    avg=lambda rows,prop:statistics.mean(x[prop] for x in rows)
    result={"baseline_step":A,"candidate_step":B}
    for k in ("p50_ms","p95_ms","p99_ms","mean_cpu_per_request_ms",
              "throughput_rps","mean_runqueue_wait_per_request_ms",
              "mean_pool_lock_wait_per_request_ms","mean_read_rows",
              "mean_compressed_bytes","mean_selected_marks",
              "max_query_memory_bytes","peak_observed_process_rss_bytes"):
        base=avg(a,k);candidate=avg(b,k)
        drift=abs(a[0][k]-a[1][k])/max(base,1e-9)
        result[k]={
            "base":round(base,4),"candidate":round(candidate,4),
            "candidate_div_base":round(candidate/max(base,1e-9),4),
            "baseline_control_drift":round(drift,5)
        }
    result["valid_baseline"] = (
        result["p50_ms"]["baseline_control_drift"]<=.25 and
        result["mean_cpu_per_request_ms"]["baseline_control_drift"]<=.25 and
        result["throughput_rps"]["baseline_control_drift"]<=.25
    )
    return result


def main():
    ap=argparse.ArgumentParser()
    for name in ("binary","restart-script","pid-file","fixture","manifest","output"):
        ap.add_argument("--"+name,required=True)
    ap.add_argument("--cases",nargs="+",choices=CASES,default=list(CASES_DEFAULT))
    ap.add_argument("--clients",nargs="+",type=int,choices=CLIENTS_DEFAULT,
                    default=list(CLIENTS_DEFAULT))
    args=ap.parse_args()
    report={
        "question_id":"R10-bucket-id-factorial-v1",
        "dependent_gate":"native multi-client bucket 3600 vs10800",
        "fixture_sha256":FIXTURE_SHA,"source_sha":SOURCE_SHA,
        "binary_sha256":BINARY_SHA,"cases":{},"errors":[],
        "read_only":True,"memory_limit_bytes":4800000000,
    }
    save(args.output,report)
    try:
        m=json.loads(pathlib.Path(args.manifest).read_text())
        rows,samples=expected_counts(3,400)
        if (sha256(args.binary)!=BINARY_SHA or
            sha256(args.fixture)!=FIXTURE_SHA or m.get("sha256")!=FIXTURE_SHA or
            m.get("series")!=sum(rows.values()) or
            m.get("samples")!=sum(samples.values()) or
            m.get("days")!=3 or m.get("node_slots")!=400 or
            m.get("base")!=BASE):
            raise AssertionError("Pinned R10 binary/fixture/distribution mismatch")
        report["manifest"]=m
        report["table_build"]={}
        for step,name in TABLES.items():
            create_raw(name,step)
            ingested=streamed_insert(name,args.fixture)
            optimized=optimize_table(name)
            if (ingested["rows"]!=m["series"] or
                any(isinstance(x,dict) for x in optimized.values())):
                raise AssertionError(("Incomplete source/optimizer ingestion",name,ingested,optimized))
            report["table_build"][name]={"inserted":ingested,"optimize":optimized}
        wait_no_merges()
        report["physical_before"]={}
        for step,name in TABLES.items():
            report["physical_before"][name]=physical(name)
            audit=physical_audit(
                name,m["series"],m["samples"],m["series"]*LIFE//step,"raw")
            report.setdefault("source_audit",{})[name]=audit
        ids={v["identity"]["digest"] for v in report["source_audit"].values()}
        if len(ids)!=1:raise AssertionError("Two schemas have unequal logical IDs")
        save(args.output,report)
        end=int(m["end"]);ticks={end-2*DAY,end-DAY,end}
        reference={}
        for case in args.cases:
            expected=int(CASES[case][3])*3
            qid=f"r10readoracle_{RUN}_{case}"
            _,output=post(HOST,query(TABLES[A],case,end-2*DAY,end),qid)
            points=normalize_any(output)
            if len(points)!=expected or {t for _,t in points}!=ticks:
                raise AssertionError(("Wrong native control grid",case,len(points),expected))
            reference[case]=points
        for case in args.cases:
            report["cases"][case]={}
            expected=len(reference[case])
            for n in args.clients:
                key=str(n);out={"phases":[]}
                report["cases"][case][key]=out
                save(args.output,report)
                for order,step in enumerate((A,B,B,A)):
                    current=phase(case,n,order,step,end,reference[case],
                                  expected,ticks,args,report["physical_before"])
                    out["phases"].append(current)
                    save(args.output,report)
                out["summary"]=compare(out["phases"])
                if not out["summary"]["valid_baseline"]:
                    report["errors"].append({"case":case,"clients":n,
                                             "invalid_baseline":out["summary"]})
                print("R10_CONCURRENT_COMPARE",json.dumps({
                    "case":case,"clients":n,"summary":out["summary"]
                },separators=(",",":")),flush=True)
                save(args.output,report)
        report["physical_after"]={t:physical(t) for t in TABLES.values()}
        if report["physical_after"]!=report["physical_before"]:
            raise AssertionError("Source physical parts mutated during read-only waves")
        report["passed"]=not report["errors"]
    except Exception as e:
        report["passed"]=False
        report["errors"].append({
            "fatal":str(e)[:2400],"traceback":traceback.format_exc()[-4300:]
        })
    finally:
        save(args.output,report)
        compact={
            "passed":report["passed"],"errors":report["errors"],
            "cases":{case:{n:v.get("summary") for n,v in arr.items()}
                     for case,arr in report["cases"].items()},
            "manifest":report.get("manifest"),
            "physical":report.get("physical_before"),
        }
        save(str(pathlib.Path(args.output).with_suffix(".compact.json")),compact)
        print("R10_CONCURRENT_DONE",json.dumps(compact,separators=(",",":")),flush=True)
    if not report["passed"]:raise SystemExit(1)


if __name__=="__main__":
    main()
