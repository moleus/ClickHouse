#!/usr/bin/env python3
"""R03: full native CH vs VM1.134 query_range, 1/4/8 clients, exact same data.

One runner, same R01 SHA-pinned 400-slot 3-day 2.49m point fixture.
Both services stay alive; each pair of ABBA phases runs only one engine's
queries. Per-client concurrency is synchronized with a start barrier.
Compare complete nonempty labels, evaluation timestamps and numeric values,
never just counts. CH CPU from QueryFinish; cross-engine cost from actual
per-process CPU / query, not VM's absent query_log or ClickHouse-only counters.
Classify invalid/semantic differences separately from valid performance.
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
import urllib.parse
import urllib.request

sys.path[:0]=[
    ".github/ts-lab-r03",".github/ts-lab-r01",
    ".github/ts-lab-bench",".github/ts-lab-perf",".github/ts-lab-cpu"
]
from bench_bucket_cxx_abca import post,normalize_any,assert_same
from bench_ch_vs_vm_dense_sparse import vm_normalized
from bench_jenkins_querypath import pct
from bench_filtered_selector_v4_sql import sql_string
from bench_granularity_v11 import sql,streamed_insert,optimize_table,wait_no_merges,inner
from bench_native_multiclient_stock_20261103 import (
    complete_logs,physical,server_status_pid,rss_bytes,sha256,save
)
from bench_k8s_mixed_native import CASES,DAY,SOURCE_SHA,BINARY_SHA
from generate_k8s_churn_fixture import expected_counts,BASE,LIFE

HOST="http://127.0.0.1:18191"
VM="http://127.0.0.1:8430"
FIXTURE_SHA="ea9a8d43c9e58ed916f1bf43949556d8ed18169ddd14033c293af4cd54c9881d"
CH_TABLE="tslab_r03_raw_uuid"
CLIENTS=(1,4,8)
CASES_TO_RUN=("node_broad","pod_namespace","pod_broad","burst15")
ABBA=("vm","ch","ch","vm")
WARMUP_WAVES=2
MEASURED_WAVES=64
THREADS=2
RUN=os.getenv("GITHUB_RUN_ID","local")


def create_table():
    sql(
        f"CREATE TABLE {CH_TABLE} ENGINE=TimeSeries "
        "SETTINGS recent_samples_ttl_seconds=0 "
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


def query_expr(case):
    metric,matcher,group,_=CASES[case]
    return f"avg by({group})(avg_over_time({metric}{matcher}[1d]))"


def native_sql(case,start,end):
    return (
        f"SELECT * FROM prometheusQueryRange({CH_TABLE},"
        f"{sql_string(query_expr(case))},{start},{end},86400) "
        f"SETTINGS max_threads={THREADS},max_memory_usage=3500000000,"
        "log_queries=1,log_processors_profiles=1 FORMAT JSONEachRow"
    )


def call(engine,case,start,end,qid):
    if engine=="ch":
        return_data=post(HOST,native_sql(case,start,end),qid)
        elapsed,body=return_data
        return elapsed,normalize_any(body)
    url=VM+"/api/v1/query_range?"+urllib.parse.urlencode({
        "query":query_expr(case),"start":start,"end":end,
        "step":DAY,"nocache":1
    })
    first=time.perf_counter()
    with urllib.request.urlopen(url,timeout=250) as response:
        result=json.loads(response.read())
    ms=round((time.perf_counter()-first)*1000,3)
    if result.get("status")!="success":
        raise AssertionError(("VM failed native MetricsQL query_range",case,result))
    return ms,vm_normalized(result)


def proc_cpu_seconds(pid):
    body=pathlib.Path(f"/proc/{pid}/stat").read_text()
    fields=body[body.rfind(")")+2:].split()
    return (int(fields[11])+int(fields[12]))/os.sysconf("SC_CLK_TCK")


def cpu_schedstat_snapshot(pid):
    """All current process threads: nanosecond on-CPU time, not runnable wait."""
    total=0
    count=0
    for path in pathlib.Path(f"/proc/{pid}/task").iterdir():
        try:
            parts=(path/"schedstat").read_text().split()
            if len(parts)!=3:
                continue
            total+=int(parts[0])
            count+=1
        except (OSError,ValueError):
            continue
    return {"run_ns":total,"threads":count}


def wave(executor,engine,n,case,start,end,prefix,idx,reference,ticks,expected):
    barrier=threading.Barrier(n+1,timeout=30)
    def runner(i):
        barrier.wait()
        t0=time.perf_counter()
        qid=f"{prefix}_{idx}_{i}"
        elapsed,rows=call(engine,case,start,end,qid)
        t1=time.perf_counter()
        if len(rows)!=expected or {stamp for _,stamp in rows}!=ticks:
            raise AssertionError(("Bad output grid",engine,case,len(rows),expected))
        assert_same(reference,rows,case,engine)
        return {"qid":qid,"client_ms":elapsed,"start":t0,
                "end":t1,"warmup":idx<WARMUP_WAVES,"wave":idx}
    fs=[executor.submit(runner,i) for i in range(n)]
    barrier.wait()
    trials=[f.result(timeout=360) for f in fs]
    duration=max(x["end"] for x in trials)-min(x["start"] for x in trials)
    return {"wave":idx,"warmup":idx<WARMUP_WAVES,
            "duration_s":round(duration,6),"trials":trials}


def phase(engine,case,n,index,pids,reference,end):
    begin=end-2*DAY
    expected=3*CASES[case][3]
    ticks={begin+i*DAY for i in range(3)}
    pid=pids[engine]
    prefix=f"r03_{RUN}_{case}_{n}_{index}_{engine}"
    rec={"engine":engine,"case":case,"clients":n,
         "phase":index,"pid":pid,
         "expr":query_expr(case),"trials":[],"waves":[]}
    cpu_start=cpu_end=None
    sched_start=sched_end=None
    rss_before=rss_bytes(pid)
    # The previous 4Hz RSS observer used >2% of short VM phase windows.
    # Keep *zero* periodic thread/process polling while timing requests.
    with concurrent.futures.ThreadPoolExecutor(max_workers=n) as pool:
        for i in range(WARMUP_WAVES+MEASURED_WAVES):
            if i==WARMUP_WAVES:
                cpu_start=proc_cpu_seconds(pid)
                sched_start=cpu_schedstat_snapshot(pid)
            w=wave(pool,engine,n,case,begin,end,prefix,i,
                   reference,ticks,expected)
            rec["waves"].append(w)
            rec["trials"].extend(w["trials"])
        cpu_end=proc_cpu_seconds(pid)
        sched_end=cpu_schedstat_snapshot(pid)
    rss_after=rss_bytes(pid)
    rec["rss"]={
        "method":"two /proc/PID/status snapshots outside timed workload; "
                 "VmHWM is cumulative since process start, not phase-local",
        "before":rss_before,"after":rss_after,
    }
    sched_ns=sched_end["run_ns"]-sched_start["run_ns"]
    if sched_ns<0:
        raise AssertionError(("schedstat counter decreased; unstable thread lifetime",
                              sched_start,sched_end))
    rec["schedstat"]={
        "start":sched_start,"end":sched_end,
        "delta_run_ns":sched_ns,
    }
    timed=[x for x in rec["trials"] if not x["warmup"]]
    spans=[x["duration_s"] for x in rec["waves"] if not x["warmup"]]
    if len(timed)!=MEASURED_WAVES*n:
        raise AssertionError("Wrong request count")
    lat=[x["client_ms"] for x in timed]
    rec["summary"]={
        "p50_ms":pct(lat,.50),"p95_ms":pct(lat,.95),"p99_ms":pct(lat,.99),
        "qps":round(len(timed)/sum(spans),4),
        "requests":len(timed),"measured_waves":MEASURED_WAVES,
        "process_cpu_s":round(cpu_end-cpu_start,3),
        "process_cpu_ms_per_request":round(1000*(cpu_end-cpu_start)/len(timed),3),
        "schedstat_cpu_ms_per_request":round(sched_ns/1e6/len(timed),4),
        "rss_snapshot_max_bytes":max(
            rss_before.get("VmRSS") or 0,rss_after.get("VmRSS") or 0
        ),
        "process_lifetime_rss_hwm_bytes":rss_after.get("VmHWM"),
        "rss_sampling":"start/end snapshots, no live observer"
    }
    if engine=="ch":
        ids={x["qid"] for x in rec["trials"]}
        logs=complete_logs(prefix,ids)
        for t in rec["trials"]:
            q=logs[t["qid"]]
            ev=q.get("ProfileEvents",{})
            t["ch_queryfinish"]={
                "cpu_ms":round(ev.get("OSCPUVirtualTimeMicroseconds",0)/1000,3),
                "read_rows":q["read_rows"],"read_bytes":q["read_bytes"],
                "selected_marks":ev.get("SelectedMarks"),
                "compressed_read_bytes":ev.get("ReadCompressedBytes"),
                "peak_query_memory_bytes":q["memory_usage"]
            }
        arr=[x["ch_queryfinish"] for x in timed]
        rec["summary"]["query_finish_cpu_ms"]=round(
            statistics.mean(x["cpu_ms"] for x in arr),3
        )
        rec["summary"]["read_rows"]=round(
            statistics.mean(x["read_rows"] for x in arr),2
        )
        rec["summary"]["read_bytes"]=round(
            statistics.mean(x["read_bytes"] for x in arr),2
        )
        rec["summary"]["max_query_memory_bytes"]=max(
            x["peak_query_memory_bytes"] for x in arr
        )
    print("R03_PHASE",json.dumps({
        "engine":engine,"case":case,"clients":n,
        "phase":index,"summary":rec["summary"]
    },separators=(",",":")),flush=True)
    return rec


def compare(phases):
    a=[z["summary"] for z in phases if z["engine"]=="vm"]
    b=[z["summary"] for z in phases if z["engine"]=="ch"]
    if len(a)!=2 or len(b)!=2:
        raise AssertionError("Incomplete VM/CH/CH/VM comparison")
    mean=lambda ls,k:statistics.mean(z[k] for z in ls)
    fields=["p50_ms","p95_ms","p99_ms","qps",
            "process_cpu_ms_per_request","schedstat_cpu_ms_per_request"]
    results={}
    for field in fields:
        vm=mean(a,field)
        ch=mean(b,field)
        control=abs(a[0][field]-a[1][field])/max(abs(vm),1e-9)
        ch_drift=abs(b[0][field]-b[1][field])/max(abs(ch),1e-9)
        results[field]={"vm":round(vm,4),"ch":round(ch,4),
                        "ch_div_vm":round(ch/vm,5) if vm else None,
                        "vm_control_drift":round(control,5),
                        "ch_control_drift":round(ch_drift,5)}
    results["valid"]=all(
        results[field][f"{name}_control_drift"]<=.25
        for field in ("p50_ms","qps","schedstat_cpu_ms_per_request")
        for name in ("vm","ch")
    )
    return results


def main():
    p=argparse.ArgumentParser()
    p.add_argument("--fixture",required=True)
    p.add_argument("--manifest",required=True)
    p.add_argument("--import-report",required=True)
    p.add_argument("--ch-pid-file",required=True)
    p.add_argument("--vm-pid-file",required=True)
    p.add_argument("--binary",required=True)
    p.add_argument("--output",required=True)
    p.add_argument("--cases",nargs="+",choices=CASES,default=list(CASES_TO_RUN))
    p.add_argument("--clients",nargs="+",type=int,choices=CLIENTS,default=list(CLIENTS))
    args=p.parse_args()
    report={
        "question_id":"R03-k8s-400node-native-vm-concurrency-v1",
        "source_sha":SOURCE_SHA,"fixture_sha":FIXTURE_SHA,
        "pinned_stock_binary_sha":BINARY_SHA,
        "engines":["ClickHouse stock O1 raw UUID TimeSeries",
                   "VictoriaMetrics 1.134.0 single-node"],
        "cases":{},"errors":[]
    }
    save(args.output,report)
    try:
        m=json.loads(pathlib.Path(args.manifest).read_text())
        vm=json.loads(pathlib.Path(args.import_report).read_text())
        expected_rows,expected_samples=expected_counts(3,400)
        if (m.get("sha256")!=FIXTURE_SHA or sha256(args.fixture)!=FIXTURE_SHA
            or m.get("series")!=sum(expected_rows.values())
            or m.get("samples")!=sum(expected_samples.values())
            or not vm.get("passed") or vm.get("samples")!=m["samples"]
            or vm.get("source_sha256")!=FIXTURE_SHA
            or sha256(args.binary)!=BINARY_SHA):
            raise AssertionError("Input/engine data or pinned binary mismatch")
        report["manifest"]=m
        report["vm_ingest"]=vm
        pids={
            "ch":server_status_pid(args.ch_pid_file),
            "vm":int(pathlib.Path(args.vm_pid_file).read_text())
        }
        report["pids"]=pids
        create_table()
        report["ch_ingest"]=streamed_insert(CH_TABLE,args.fixture)
        if report["ch_ingest"]["rows"]!=m["series"]:
            raise AssertionError("Incomplete CH fixture")
        report["ch_optimize"]=optimize_table(CH_TABLE)
        if any(isinstance(v,dict) for v in report["ch_optimize"].values()):
            raise AssertionError(("CH OPTIMIZE error",report["ch_optimize"]))
        wait_no_merges()
        frozen=physical(CH_TABLE)
        report["ch_physical_before"]=frozen
        for k in ("tags","samples","timeranges"):
            if "Tuple(UInt64, UUID)" not in frozen[k]["id_column"].replace(" "," "):
                raise AssertionError(("Not RAW UUID schema",k,frozen[k]))
            if frozen[k]["parts"]["part_count"]!=1:
                raise AssertionError(("Unfrozen CH part",k,frozen[k]))
        sample_inner=inner(CH_TABLE,"samples")
        rows=json.loads(sql(
            "SELECT sum(length(samples)) AS n FROM "+sample_inner
            +" FORMAT JSONEachRow").strip())
        if int(rows["n"])!=m["samples"]:
            raise AssertionError(("Incorrect physical sample arrays",rows))
        save(args.output,report)
        end=int(m["end"])
        begin=end-2*DAY
        for case in args.cases:
            _,ref=call("ch",case,begin,end,f"r03oracle_{RUN}_{case}")
            expected=3*CASES[case][3]
            if len(ref)!=expected or {t for _,t in ref}!={begin,begin+DAY,end}:
                raise AssertionError(("Wrong native oracle",case,len(ref),expected))
            _,from_vm=call("vm",case,begin,end,f"r03vmoracle_{RUN}_{case}")
            report.setdefault("oracles",{})[case]=assert_same(ref,from_vm,case,"cross-engine")
            for n in args.clients:
                phases=[]
                rec={"oracles_equal":True,"phases":phases}
                report["cases"][f"{case}_{n}"]=rec
                for i,engine in enumerate(ABBA):
                    result=phase(engine,case,n,i,pids,ref,end)
                    phases.append(result)
                    save(args.output,report)
                rec["summary"]=compare(phases)
                if not rec["summary"]["valid"]:
                    report["errors"].append({
                        "case":case,"clients":n,
                        "reason":"CH or VM same-runner wall/qps/schedstat CPU control drift exceeds25%",
                        "summary":rec["summary"]
                    })
                print("R03_COMPARE",json.dumps({
                    "case":case,"clients":n,"summary":rec["summary"]
                },separators=(",",":")),flush=True)
                save(args.output,report)
        report["ch_physical_after"]=physical(CH_TABLE)
        if report["ch_physical_after"]!=frozen:
            raise AssertionError("CH physical parts changed during benchmark")
        report["passed"]=not report["errors"]
    except Exception as ex:
        report["errors"].append({"fatal":str(ex)[:1900],
                                 "traceback":traceback.format_exc()[-5000:]})
        report["passed"]=False
    finally:
        save(args.output,report)
        compact={
            "question_id":report["question_id"],"passed":report["passed"],
            "fixture_sha":FIXTURE_SHA,"errors":report["errors"],
            "oracles":report.get("oracles"),
            "comparisons":{k:v.get("summary") for k,v in report["cases"].items()},
            "physical":report.get("ch_physical_before"),
        }
        save(str(pathlib.Path(args.output).with_suffix(".compact.json")),compact)
        print("R03_DONE",json.dumps(compact,separators=(",",":")),flush=True)
    if not report["passed"]:raise SystemExit(1)


if __name__=="__main__":
    main()
