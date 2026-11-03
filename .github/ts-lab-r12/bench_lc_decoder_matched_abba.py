#!/usr/bin/env python3
"""R12: exactly matched O1 baseline vs experimental LC dictionary decoder.

This is a NORMAL, VALID-SERIALIZATION performance test ONLY.
The staged C++ LC fast path fails stock's malformed dictionary index
bounds guarantee. Passing this test MUST NOT authorize promotion, merger,
production use, or unsupervised read of untrusted/malformed LC parts.

Baseline: O1 build 41531656434, original fast-rate+JOIN patches.
Candidate: O1 build 41591617794, same patches plus LC adoption only.
They read identical immutable default-LC UUID v11 physical TimeSeries parts.
Fresh ClickHouse server per A/B/B/A phase, with full native PromQL output
parity and CPU, RSS, physical read and sampled CPU hot path evidence.
"""
import argparse
import json
import os
import pathlib
import statistics
import subprocess
import sys
import time
import traceback

sys.path[:0]=[
    ".github/ts-lab-r12", ".github/ts-lab-perf",
    ".github/ts-lab-bench", ".github/ts-lab-cpu",
]
from bench_granularity_v11 import sql, inner, optimize_table, wait_no_merges
from bench_bucket_cxx_abca import post, normalize_any, assert_same
from bench_filtered_selector_v4_sql import sql_string
from bench_cpu_knobs import capture_profiles
from bench_native_multiclient_stock_20261103 import (
    physical, complete_logs, server_status_pid, RSSMonitor, sha256, save
)
from bench_jenkins_querypath import pct

SOURCE_SHA="52529f0c711566d696917678bf15e7068772fdde"
FIXTURE_SHA="8f3a36846f80d6be5a8604d142e934399f2687094766c57bf105e43977f52858"
NAME="jenkins_ci_95k_daily"
DAY=86400
HOST="http://127.0.0.1:18191"
CASE_SPEC={
    "stage":('{stage="stage-0007"}',14,1),
    "project":('{project="project-00"}',14,19),
    "regex":('{branch=~"main|feature-0[0-9]"}',14,38),
    "whole":("",1,38),
}
WARMUP=2
REPEAT=6


def statement(case,end):
    selector,days,_=CASE_SPEC[case]
    start=end-(days-1)*DAY
    expr=f"avg by(stage)(avg_over_time({NAME}{selector}[1d]))"
    return (
        f"SELECT * FROM prometheusQueryRange(churn_ts,{sql_string(expr)},"
        f"{start},{end},{DAY}) SETTINGS max_threads=2,"
        "max_memory_usage=3600000000,log_queries=1,"
        "log_processors_profiles=1,query_profiler_cpu_time_period_ns=1000000 "
        "FORMAT JSONEachRow"
    )


def auditable_case(case,finish):
    _sel,days,groups=CASE_SPEC[case]
    start=finish-(days-1)*DAY
    return days*groups, {start+i*DAY for i in range(days)}


def run_phase(case,order,variant,end,reference,pid_file,restart_script,binaries,before):
    binary=binaries[variant]
    subprocess.run([restart_script,binary,f"r12_{case}_{order}_{variant}"],
                   timeout=265,check=True)
    pid=server_status_pid(pid_file)
    wait_no_merges()
    current=physical("churn_ts")
    if current!=before:
        raise AssertionError(("Physical data changed during read-only binary swap",
                              case,order,current,before))
    stmt=statement(case,end)
    expected,ticks=auditable_case(case,end)
    qprefix=f"r12lc_{os.getenv('GITHUB_RUN_ID','local')}_{case}_{order}_{variant}_"
    record={
        "case":case,"order":order,"variant":variant,"pid":pid,
        "binary_sha256":sha256(binary),"query":stmt,"trials":[]
    }
    with RSSMonitor(pid) as observer:
        for i in range(WARMUP+REPEAT):
            qid=f"{qprefix}{i}"
            ms,body=post(HOST,stmt,qid)
            points=normalize_any(body)
            if len(points)!=expected or {x for _,x in points}!=ticks:
                raise AssertionError((case,variant,"Bad native query count/timestamps",
                                      len(points),expected))
            if reference is None:
                reference=points
            else:
                assert_same(reference,points,case,"R12 LC dictionary binary")
            record["trials"].append({
                "qid":qid,"client_ms":ms,"warmup":i<WARMUP
            })
    record["rss"]=observer.report()
    if record["rss"]["observer_overhead_fraction"]>.02:
        raise AssertionError("RSS observer overhead >2%")
    logs=complete_logs(qprefix,{t["qid"] for t in record["trials"]})
    for tr in record["trials"]:
        row=logs[tr["qid"]]
        ev=row.get("ProfileEvents",{})
        tr.update({
            "server_ms":row["query_duration_ms"],
            "cpu_ms":round(ev.get("OSCPUVirtualTimeMicroseconds",0)/1000,3),
            "runnable_ms":round(ev.get("OSCPUWaitMicroseconds",0)/1000,3),
            "read_rows":row["read_rows"],
            "read_bytes":row["read_bytes"],
            "compressed_read_bytes":ev.get("ReadCompressedBytes"),
            "marks":ev.get("SelectedMarks"),
            "query_peak_mem_bytes":row["memory_usage"]
        })
    valid=[t for t in record["trials"] if not t["warmup"]]
    record["summary"]={
        "client_p50_ms":pct([x["client_ms"] for x in valid],.5),
        "client_p95_ms":pct([x["client_ms"] for x in valid],.95),
        "server_p50_ms":pct([x["server_ms"] for x in valid],.5),
        "median_cpu_ms":round(statistics.median(x["cpu_ms"] for x in valid),3),
        "median_runnable_ms":round(statistics.median(x["runnable_ms"] for x in valid),3),
        "median_read_rows":statistics.median(x["read_rows"] for x in valid),
        "median_read_bytes":statistics.median(x["read_bytes"] for x in valid),
        "median_read_compressed_bytes":statistics.median(
            x["compressed_read_bytes"] or 0 for x in valid
        ),
        "median_selected_marks":statistics.median(x["marks"] or 0 for x in valid),
        "peak_query_memory_bytes":max(x["query_peak_mem_bytes"] for x in valid),
        "rss_observed_max_bytes":record["rss"]["rss_observed_max_bytes"],
    }
    record["diagnostic_qid"]=valid[0]["qid"]
    print("R12_DECODER_PHASE",json.dumps({
        "case":case,"order":order,"variant":variant,
        "summary":record["summary"]
    },separators=(",",":")),flush=True)
    return record,reference


def compare(phases):
    a=[p["summary"] for p in phases if p["variant"]=="base"]
    b=[p["summary"] for p in phases if p["variant"]=="lcpatch"]
    if len(a)!=2 or len(b)!=2:
        raise AssertionError("Missing 2+2 A/B/B/A phases")
    avg=lambda x,k:statistics.mean(y[k] for y in x)
    bm=avg(a,"client_p50_ms")
    cm=avg(b,"client_p50_ms")
    bc=avg(a,"median_cpu_ms")
    cc=avg(b,"median_cpu_ms")
    drift=abs(a[0]["client_p50_ms"]-a[1]["client_p50_ms"])/bm
    cdrift=abs(a[0]["median_cpu_ms"]-a[1]["median_cpu_ms"])/bc
    return{
        "baseline_p50_ms":round(bm,3),
        "candidate_p50_ms":round(cm,3),
        "baseline_over_candidate_speedup":round(bm/cm,4),
        "baseline_cpu_ms":round(bc,3),
        "candidate_cpu_ms":round(cc,3),
        "cpu_change_fraction":round((cc-bc)/bc,5),
        "baseline_p95_ms":round(avg(a,"client_p95_ms"),3),
        "candidate_p95_ms":round(avg(b,"client_p95_ms"),3),
        "baseline_median_read_rows":round(avg(a,"median_read_rows"),2),
        "candidate_median_read_rows":round(avg(b,"median_read_rows"),2),
        "baseline_query_mem_max_bytes":max(y["peak_query_memory_bytes"] for y in a),
        "candidate_query_mem_max_bytes":max(y["peak_query_memory_bytes"] for y in b),
        "baseline_control_drift":round(drift,5),
        "baseline_cpu_drift":round(cdrift,5),
        "valid":drift<=.25 and cdrift<=.25,
    }


def main():
    p=argparse.ArgumentParser()
    p.add_argument("--fixture",required=True)
    p.add_argument("--manifest",required=True)
    p.add_argument("--base-binary",required=True)
    p.add_argument("--candidate-binary",required=True)
    p.add_argument("--pid-file",required=True)
    p.add_argument("--restart-script",required=True)
    p.add_argument("--output",required=True)
    p.add_argument("--cases",nargs="+",choices=CASE_SPEC,default=list(CASE_SPEC))
    args=p.parse_args()
    bins={"base":args.base_binary,"lcpatch":args.candidate_binary}
    report={
        "question_id":"R12-lc-decoder-matched-binaries-v1",
        "source_sha":SOURCE_SHA,"fixture_sha":FIXTURE_SHA,
        "safety_blocked":True,
        "safety_reason":"C++ adoption bypasses stock checkIndexesAreLimited on malformed indexes; no promotion",
        "binary_sha256":{k:sha256(v) for k,v in bins.items()},
        "cases":{},"errors":[]
    }
    save(args.output,report)
    try:
        if report["binary_sha256"]["base"]==report["binary_sha256"]["lcpatch"]:
            raise AssertionError("Experimental and control binaries unexpectedly identical")
        m=json.loads(pathlib.Path(args.manifest).read_text())
        if m.get("sha256")!=FIXTURE_SHA or sha256(args.fixture)!=FIXTURE_SHA or m.get("series")!=1330000:
            raise AssertionError("Wrong canonical frozen 1.33m Jenkins fixture")
        report["manifest"]=m
        optimize=optimize_table("churn_ts")
        report["optimize"]=optimize
        if any(isinstance(v,dict) for v in optimize.values()):
            raise AssertionError(("OPTIMIZE failed",optimize))
        wait_no_merges()
        before=physical("churn_ts")
        report["physical_before"]=before
        for key in ("tags","samples","timeranges"):
            if "LowCardinality(UUID)" not in before[key]["id_column"]:
                raise AssertionError(("Wrong default LC ID schema",key,before[key]))
            if before[key]["parts"]["part_count"]!=1:
                raise AssertionError(("Wrong frozen part count",key,before[key]))
        report["server_initial_pid"]=server_status_pid(args.pid_file)
        save(args.output,report)
        end=int(m["end"])
        for case in args.cases:
            reference=None
            phases=[]
            report["cases"][case]={"phases":phases}
            for order,kind in enumerate(("base","lcpatch","lcpatch","base")):
                phase,reference=run_phase(case,order,kind,end,reference,
                                         args.pid_file,args.restart_script,bins,before)
                phases.append(phase)
                save(args.output,report)
            comp=compare(phases)
            report["cases"][case]["summary"]=comp
            report["cases"][case]["representative_cpu_profiles"]=capture_profiles(
                [phases[0]["diagnostic_qid"],phases[1]["diagnostic_qid"]]
            )
            if not comp["valid"]:
                report["errors"].append({"case":case,"invalid_control_drift":comp})
            print("R12_DECODER_COMPARE",json.dumps({"case":case,**comp},
                                                  separators=(",",":")),flush=True)
            save(args.output,report)
        report["physical_after"]=physical("churn_ts")
        if report["physical_after"]!=before:
            raise AssertionError("Physical parts/rows/marks/bytes changed")
        report["passed"]=not report["errors"]
    except Exception as e:
        report["errors"].append({"fatal":str(e)[:2100],
                                  "traceback":traceback.format_exc()[-4200:]})
        report["passed"]=False
    finally:
        save(args.output,report)
        compact={
            "question_id":report["question_id"],
            "passed":report["passed"],
            "safety_blocked":True,
            "errors":report["errors"],
            "binary_sha256":report["binary_sha256"],
            "fixture_sha":FIXTURE_SHA,
            "physical_before":report.get("physical_before"),
            "physical_after":report.get("physical_after"),
            "cases":{case:entry.get("summary") for case,entry in report["cases"].items()}
        }
        save(str(pathlib.Path(args.output).with_suffix(".compact.json")),compact)
        print("R12_DECODER_DONE",json.dumps(compact,separators=(",",":")),flush=True)
    if not report["passed"]:
        raise SystemExit(1)


if __name__=="__main__":
    main()
