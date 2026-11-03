#!/usr/bin/env python3
"""Native stock O1 RAW_UUID: max_threads=1 vs 2 at fixed concurrent clients.

A/B/B/A across max_threads (2,1,1,2), 2 warmups and 8 measured waves in
each phase. Exact frozen v11 1.33m fixture. Full native PromQL parity for
all successful queries; CPU/request, runqueue delay, memory/RSS, throughput
and tail latency. No compiler or source changes. Long-lived process RSS
retention is an explicit limitation.
"""
import argparse
import concurrent.futures
import json
import os
import pathlib
import statistics
import sys
import time
import traceback

sys.path[:0]=[".github/ts-lab-perf",".github/ts-lab-bench",".github/ts-lab-cpu"]
from bench_granularity_v11 import (
    inner, sql, streamed_insert, optimize_table, wait_no_merges
)
from bench_bucket_cxx_abca import assert_same, normalize_any
from bench_jenkins_querypath import pct, post
from bench_native_multiclient_stock_20261103 import (
    RAW, LC, HOST, SOURCE_SHA, FIXTURE_SHA,
    SELS, EVENTS, WAVES, WARMUP_WAVES,
    create_raw, server_status_pid, physical, statement, sha256,
    run_wave, RSSMonitor, complete_logs, save
)
from bench_cpu_knobs import capture_profiles

def query(case, end, threads):
    prior=statement(RAW,case,end)
    assert "max_threads=2" in prior
    return prior.replace("max_threads=2",f"max_threads={threads}",1)


def phase_run(case, n, end, threads, order, reference, expected, ticks, pid):
    prefix=f"clbudget_{case}_{n}_{threads}_{order}_{os.getenv('GITHUB_RUN_ID','local')}"
    q=query(case,end,threads)
    ph={
        "threads":threads,"case":case,"clients":n,"order":order,
        "query":q,"waves":[],"trials":[],
    }
    with RSSMonitor(pid) as monitor:
        with concurrent.futures.ThreadPoolExecutor(max_workers=n) as pool:
            for wave in range(WAVES):
                one=run_wave(pool,n,q,case,prefix,wave,reference,expected,ticks)
                ph["waves"].append(one)
                ph["trials"].extend(one["trials"])
                print("THREAD_BUDGET_WAVE",json.dumps({
                    "case":case,"clients":n,"threads":threads,
                    "order":order,"wave":wave,"elapsed_s":one["duration_s"],
                },separators=(",",":")),flush=True)
    ph["rss"]=monitor.report()
    if ph["rss"]["observer_overhead_fraction"]>0.02:
        raise AssertionError("RSS observer overhead too high")
    ids={x["qid"] for x in ph["trials"]}
    logs=complete_logs(prefix,ids)
    for sample in ph["trials"]:
        row=logs[sample["qid"]]
        ev=row.get("ProfileEvents",{})
        sample["query_duration_ms"]=row["query_duration_ms"]
        sample["query_peak_memory_bytes"]=row["memory_usage"]
        sample["read_rows"]=row["read_rows"]
        sample["read_bytes"]=row["read_bytes"]
        sample["events"]={event:int(ev.get(event,0)) for event in EVENTS}
    items=[x for x in ph["trials"] if not x["warmup"]]
    waves=[x for x in ph["waves"] if not x["warmup"]]
    ms=[x["client_ms"] for x in items]
    ph["metrics"]={
        "p50_ms":pct(ms,.5),"p95_ms":pct(ms,.95),"p99_ms":pct(ms,.99),
        "rate_rps":round(len(items)/sum(w["duration_s"] for w in waves),5),
        "cpu_per_request_ms":round(statistics.mean(
            x["events"]["OSCPUVirtualTimeMicroseconds"] for x in items)/1000,3),
        "runqueue_wait_per_request_ms":round(statistics.mean(
            x["events"]["OSCPUWaitMicroseconds"] for x in items)/1000,3),
        "pool_lock_wait_per_request_ms":round(statistics.mean(
            x["events"]["GlobalThreadPoolLockWaitMicroseconds"]+
            x["events"]["LocalThreadPoolLockWaitMicroseconds"] for x in items)/1000,3),
        "peak_query_memory_bytes":max(x["query_peak_memory_bytes"] for x in items),
        "peak_rss_bytes":ph["rss"]["rss_observed_max_bytes"],
        "mean_compressed_read_bytes":round(statistics.mean(
            x["events"]["ReadCompressedBytes"] for x in items),2),
        "mean_selected_marks":round(statistics.mean(
            x["events"]["SelectedMarks"] for x in items),2),
        "read_rows":round(statistics.mean(x["read_rows"] for x in items),2),
        "sampled_requests":len(items),
    }
    ph["profile_qid"]=items[0]["qid"]
    return ph


def main():
    p=argparse.ArgumentParser()
    p.add_argument("--case",required=True,choices=SELS)
    p.add_argument("--clients",type=int,required=True,choices=(4,8))
    p.add_argument("--fixture",required=True)
    p.add_argument("--manifest",required=True)
    p.add_argument("--pid-file",required=True)
    p.add_argument("--output",required=True)
    args=p.parse_args()
    r={
        "case":args.case,"clients":args.clients,"fixture_sha256":FIXTURE_SHA,
        "source_sha":SOURCE_SHA,"phases":[],"errors":[],
        "comparison":"max_threads 2 1 1 2, fixed client count",
    }
    save(args.output,r)
    try:
        manifest=json.loads(pathlib.Path(args.manifest).read_text())
        if manifest["sha256"]!=FIXTURE_SHA or manifest["series"]!=1330000:
            raise AssertionError("Wrong frozen manifest")
        if sha256(args.fixture)!=FIXTURE_SHA:
            raise AssertionError("Wrong source fixture bytes")
        sel,days,groups=SELS[args.case]
        end=int(manifest["end"])
        ticks={end-i*86400 for i in range(days)}
        expected=groups*days
        pid=server_status_pid(args.pid_file)
        r["server_pid"]=pid
        create_raw()
        r["raw_ingest"]=streamed_insert(RAW,args.fixture)
        if r["raw_ingest"]["rows"]!=1330000:
            raise AssertionError("Incomplete RAW ingest")
        r["optimizations"]={t:optimize_table(t) for t in (LC,RAW)}
        wait_no_merges()
        r["physical_before"]={t:physical(t) for t in (LC,RAW)}
        sql("SYSTEM FLUSH LOGS")
        _,response=post(HOST,query(args.case,end,2),
                        f"clbudget_oracle_{args.case}_{os.getenv('GITHUB_RUN_ID','local')}")
        ref=normalize_any(response)
        if len(ref)!=expected or {t for _,t in ref}!=ticks:
            raise AssertionError(("Reference output grid incorrect",len(ref),expected))
        r["logical_oracle"]={"points":expected,"days":days,"selector":sel,"timestamps":sorted(ticks)}
        save(args.output,r)
        for order,threads in enumerate((2,1,1,2)):
            ph=phase_run(args.case,args.clients,end,threads,order,ref,expected,ticks,pid)
            r["phases"].append(ph)
            save(args.output,r)
            print("THREAD_BUDGET_PHASE",json.dumps({
                "case":args.case,"clients":args.clients,
                "threads":threads,"order":order,"metrics":ph["metrics"],
            },separators=(",",":")),flush=True)
        baseline=[x for x in r["phases"] if x["threads"]==2]
        alt=[x for x in r["phases"] if x["threads"]==1]
        avg=lambda ls,key:statistics.mean(x["metrics"][key] for x in ls)
        prior=avg(baseline,"p50_ms")
        newer=avg(alt,"p50_ms")
        drift=abs(baseline[0]["metrics"]["p50_ms"]-
                  baseline[1]["metrics"]["p50_ms"])/prior
        r["summary"]={
            "max_threads_baseline":2,"max_threads_candidate":1,
            "clients":args.clients,
            "baseline_p50_ms":round(prior,3),
            "candidate_p50_ms":round(newer,3),
            "baseline_p95_ms":round(avg(baseline,"p95_ms"),3),
            "candidate_p95_ms":round(avg(alt,"p95_ms"),3),
            "baseline_cpu_ms":round(avg(baseline,"cpu_per_request_ms"),3),
            "candidate_cpu_ms":round(avg(alt,"cpu_per_request_ms"),3),
            "baseline_runnable_wait_ms":round(avg(baseline,"runqueue_wait_per_request_ms"),3),
            "candidate_runnable_wait_ms":round(avg(alt,"runqueue_wait_per_request_ms"),3),
            "baseline_throughput_rps":round(avg(baseline,"rate_rps"),4),
            "candidate_throughput_rps":round(avg(alt,"rate_rps"),4),
            "baseline_peak_query_memory_bytes":max(x["metrics"]["peak_query_memory_bytes"] for x in baseline),
            "candidate_peak_query_memory_bytes":max(x["metrics"]["peak_query_memory_bytes"] for x in alt),
            "baseline_peak_process_rss_bytes":max(x["metrics"]["peak_rss_bytes"] for x in baseline),
            "candidate_peak_process_rss_bytes":max(x["metrics"]["peak_rss_bytes"] for x in alt),
            "baseline_control_drift":round(drift,5),"valid":drift<=0.25,
        }
        sample=[baseline[0]["profile_qid"],alt[0]["profile_qid"]]
        try:
            r["representative_cpu_profiles"]=capture_profiles(sample)
        except Exception as e:
            r["cpu_profile_error"]=str(e)[:1600]
        r["physical_after"]={t:physical(t) for t in (LC,RAW)}
        if r["physical_after"]!=r["physical_before"]:
            r["errors"].append({"physical_parts_changed":True})
        if drift>0.25:
            r["errors"].append({"baseline_drift":drift})
        r["passed"]=not r["errors"]
    except Exception as e:
        r["errors"].append({"fatal":str(e)[:2400],
                            "traceback":traceback.format_exc()[-4000:]})
        r["passed"]=False
    finally:
        save(args.output,r)
        compact={
            "case":args.case,"clients":args.clients,
            "passed":r["passed"],"errors":r["errors"],
            "summary":r.get("summary"),
            "phases":[{
                "order":x["order"],"threads":x["threads"],"metrics":x["metrics"],
            } for x in r["phases"]],
        }
        save(str(pathlib.Path(args.output).with_suffix(".compact.json")),compact)
        print("THREAD_BUDGET_DONE",json.dumps(compact,separators=(",",":")),flush=True)
    if not r["passed"]:
        raise SystemExit(1)

if __name__=="__main__":
    main()
