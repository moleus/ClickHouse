#!/usr/bin/env python3
"""R16 14d VM 165k cap, full native ClickHouse vs VM 1/4/8-client stage.

Every source sample is 1:1 with the frozen 1.33m Jenkins series fixture.
VM errors from the expected series limit are NOT successful fast requests.
Source-pinned stock ClickHouse O1 with RAW UUID TimeSeries and VM1.134 release.
No C++ compile, no timed RSS poll; all outputs and CPU controls verified.
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
import urllib.error
import urllib.parse
import urllib.request

sys.path[:0]=[
    ".github/ts-lab-r16",".github/ts-lab-r03",".github/ts-lab-bench",
    ".github/ts-lab-perf",".github/ts-lab-cpu"
]
from bench_bucket_cxx_abca import post,normalize_any,assert_same
from bench_ch_vs_vm_dense_sparse import vm_normalized
from bench_filtered_selector_v4_sql import sql_string
from bench_jenkins_querypath import pct
from bench_granularity_v11 import sql,streamed_insert,optimize_table,wait_no_merges,inner
from bench_native_multiclient_stock_20261103 import physical,server_status_pid,rss_bytes,sha256,save,complete_logs
from bench_native_vm_concurrency import proc_cpu_seconds,cpu_schedstat_snapshot

SOURCE_SHA="52529f0c711566d696917678bf15e7068772fdde"
BINARY_SHA="f0a2fa1f814c58853f43423705cac10ab9df3bfe5887e27ebe9620ba7cb9660e"
FIXTURE_SHA="8f3a36846f80d6be5a8604d142e934399f2687094766c57bf105e43977f52858"
METRIC="jenkins_ci_95k_daily"
TABLE="tslab_r16_raw_uuid"
CH="http://127.0.0.1:18191"
VM="http://127.0.0.1:8430"
DAY=86400
RUN=os.getenv("GITHUB_RUN_ID","local")
WARMUP=2
MEASURED=24
CASES={
    "stage":('{stage="stage-0007"}',1,35000),
    "project":('{project="project-00"}',19,166250),
    "whole":("",38,1330000),
}


def expression(case):
    return f"avg by(stage)(avg_over_time({METRIC}{CASES[case][0]}[1d]))"


def ch_sql(case,start,end):
    return (
        f"SELECT * FROM prometheusQueryRange({TABLE},"
        f"{sql_string(expression(case))},{start},{end},{DAY}) "
        "SETTINGS max_threads=2,max_memory_usage=3500000000,"
        "log_queries=1,log_processors_profiles=1 FORMAT JSONEachRow"
    )


def call(engine,case,start,end,qid):
    if engine=="ch":
        ms,raw=post(CH,ch_sql(case,start,end),qid)
        return {"ok":True,"status":200,"ms":ms,"rows":normalize_any(raw)}
    url=VM+"/api/v1/query_range?"+urllib.parse.urlencode({
        "query":expression(case),"start":start,"end":end,
        "step":DAY,"nocache":1,
    })
    t0=time.perf_counter()
    try:
        with urllib.request.urlopen(url,timeout=240) as response:
            code=response.status
            payload=json.loads(response.read())
    except urllib.error.HTTPError as e:
        code=e.code
        payload=json.loads(e.read())
    ms=round((time.perf_counter()-t0)*1000,4)
    if code!=200 or payload.get("status")!="success":
        return {"ok":False,"status":code,"ms":ms,
                "error":payload.get("error","")[:2200],
                "error_type":payload.get("errorType")}
    return {"ok":True,"status":code,"ms":ms,"rows":vm_normalized(payload)}


def create_raw():
    sql(
        f"CREATE TABLE {TABLE} ENGINE=TimeSeries "
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


def cap_tests(start,end):
    output={}
    for case,(selector,groups,n) in CASES.items():
        response=call("vm",case,start,end,f"r16cap_{RUN}_{case}")
        row={"selector":selector,"logical_series_in_fixture":n,
             "success":response["ok"],"status":response["status"],
             "error":response.get("error"),"error_type":response.get("error_type")}
        if response["ok"]:
            row["result_points"]=len(response["rows"])
            if row["result_points"]!=14*groups:
                raise AssertionError(("VM cap accepted wrong result count",case,row))
        output[case]=row
        print("R16_CAP",json.dumps({"case":case,**row},
                                  separators=(",",":")),flush=True)
    if not output["stage"]["success"]:
        raise AssertionError(("Below-cap stage unexpectedly failed",output["stage"]))
    for key in ("project","whole"):
        row=output[key]
        if (row["success"] or row["status"]!=422 or
            "165000" not in (row["error"] or "") or
            "exceeds" not in (row["error"] or "")):
            raise AssertionError(("Expected 165k VM cap not confirmed",key,row))
    return output


def wave(pool,engine,n,index,start,end,reference,prefix):
    barrier=threading.Barrier(n+1,timeout=30)
    def runner(i):
        barrier.wait()
        first=time.perf_counter()
        qid=f"{prefix}_{index}_{i}"
        data=call(engine,"stage",start,end,qid)
        last=time.perf_counter()
        if not data["ok"]:
            raise AssertionError(("Error is not performance data",data))
        rows=data["rows"]
        if len(rows)!=14 or {ts for _,ts in rows}!={start+j*DAY for j in range(14)}:
            raise AssertionError(("Incorrect stage grid",engine,len(rows)))
        assert_same(reference,rows,"stage14d",engine)
        return {"qid":qid,"ms":data["ms"],"start":first,"end":last,
                "warmup":index<WARMUP}
    fut=[pool.submit(runner,i) for i in range(n)]
    barrier.wait()
    got=[f.result(timeout=360) for f in fut]
    return {"warmup":index<WARMUP,
            "duration_s":round(max(x["end"] for x in got)-min(x["start"] for x in got),6),
            "trials":got}


def phase(engine,n,index,start,end,pids,reference):
    pid=pids[engine]
    prefix=f"r16_{RUN}_stage_{n}_{index}_{engine}"
    report={"engine":engine,"clients":n,"index":index,"trials":[],"waves":[]}
    rss0=rss_bytes(pid)
    cpu0=cpu1=None
    sched0=sched1=None
    with concurrent.futures.ThreadPoolExecutor(max_workers=n) as pool:
        for i in range(WARMUP+MEASURED):
            if i==WARMUP:
                cpu0=proc_cpu_seconds(pid)
                sched0=cpu_schedstat_snapshot(pid)
            item=wave(pool,engine,n,i,start,end,reference,prefix)
            report["waves"].append(item)
            report["trials"].extend(item["trials"])
        cpu1=proc_cpu_seconds(pid)
        sched1=cpu_schedstat_snapshot(pid)
    rss1=rss_bytes(pid)
    run_ns=sched1["run_ns"]-sched0["run_ns"]
    if run_ns<0:
        raise AssertionError(("Thread schedstat counter decreased",sched0,sched1))
    timed=[t for t in report["trials"] if not t["warmup"]]
    times=[w["duration_s"] for w in report["waves"] if not w["warmup"]]
    x=[t["ms"] for t in timed]
    s={
        "p50_ms":pct(x,.5),"p95_ms":pct(x,.95),"p99_ms":pct(x,.99),
        "qps":round(len(timed)/sum(times),4),
        "process_cpu_ms_per_request":round((cpu1-cpu0)*1000/len(timed),4),
        "schedstat_cpu_ms_per_request":round(run_ns/1e6/len(timed),4),
        "requests":len(timed),
        "rss_before_bytes":rss0.get("VmRSS"),
        "rss_after_bytes":rss1.get("VmRSS"),
        "process_lifetime_rss_hwm_bytes":rss1.get("VmHWM"),
    }
    if engine=="ch":
        logs=complete_logs(prefix,{t["qid"] for t in report["trials"]})
        arr=[logs[t["qid"]] for t in timed]
        s["ch_queryfinish_cpu_ms"]=round(statistics.mean(
            q.get("ProfileEvents",{}).get("OSCPUVirtualTimeMicroseconds",0)/1000
            for q in arr),4)
        s["ch_read_rows"]=statistics.mean(q["read_rows"] for q in arr)
        s["ch_peak_query_memory_bytes"]=max(q["memory_usage"] for q in arr)
    report["summary"]=s
    print("R16_STAGE_PHASE",json.dumps({"engine":engine,"clients":n,
        "phase":index,"summary":s},separators=(",",":")),flush=True)
    return report


def paired(phases):
    vm=[x["summary"] for x in phases if x["engine"]=="vm"]
    ch=[x["summary"] for x in phases if x["engine"]=="ch"]
    if len(vm)!=2 or len(ch)!=2:
        raise AssertionError("ABBA phases incomplete")
    out={}
    for key in ("p50_ms","p95_ms","p99_ms","qps","schedstat_cpu_ms_per_request"):
        a=statistics.mean(x[key] for x in vm)
        b=statistics.mean(x[key] for x in ch)
        out[key]={"vm":round(a,4),"ch":round(b,4),
                  "ch_over_vm":round(b/a,5) if a else None,
                  "vm_control_drift":round(abs(vm[0][key]-vm[1][key])/max(abs(a),1e-9),5),
                  "ch_control_drift":round(abs(ch[0][key]-ch[1][key])/max(abs(b),1e-9),5)}
    out["valid"]=all(
        out[key][v+"_control_drift"]<=.25
        for key in ("p50_ms","qps","schedstat_cpu_ms_per_request")
        for v in ("vm","ch")
    )
    return out


def main():
    p=argparse.ArgumentParser()
    for option in ("fixture","manifest","vm-import","ch-pid-file","vm-pid-file",
                   "ch-binary","output"):
        p.add_argument("--"+option,required=True)
    a=p.parse_args()
    report={"question_id":"R16-high-cardinality-165k-ch-vm-native-v1",
            "source_sha":SOURCE_SHA,"fixture_sha":FIXTURE_SHA,
            "stage_abba":{},"errors":[]}
    save(a.output,report)
    try:
        m=json.loads(pathlib.Path(a.manifest).read_text())
        vm=json.loads(pathlib.Path(a.vm_import).read_text())
        if (m.get("sha256")!=FIXTURE_SHA or sha256(a.fixture)!=FIXTURE_SHA or
            m.get("series")!=1330000 or m.get("days")!=14 or
            vm.get("source_sha256")!=FIXTURE_SHA or
            vm.get("series_and_samples")!=1330000 or
            sha256(a.ch_binary)!=BINARY_SHA):
            raise AssertionError("Source/fixture/VM importer/CH binary mismatch")
        report["manifest"]=m
        report["vm_import"]=vm
        start=int(m["end"])-13*DAY
        end=int(m["end"])
        report["vm_cap"]=cap_tests(start,end)
        save(a.output,report)
        # Existing canonical generator populated churn_ts with default LC.
        # Drop before final RAW UUID load to avoid duplicate physical datasets.
        sql("DROP TABLE churn_ts SYNC")
        create_raw()
        report["ch_ingest"]=streamed_insert(TABLE,a.fixture)
        if report["ch_ingest"]["rows"]!=1330000:
            raise AssertionError("Incomplete RAW UUID input")
        report["ch_optimize"]=optimize_table(TABLE)
        if any(isinstance(x,dict) for x in report["ch_optimize"].values()):
            raise AssertionError(("Bad optimize status",report["ch_optimize"]))
        wait_no_merges()
        physical_before=physical(TABLE)
        report["physical_before"]=physical_before
        for layer in ("tags","samples","timeranges"):
            row=physical_before[layer]
            if row["parts"]["part_count"]!=1:
                raise AssertionError(("Not a frozen one-part table",layer,row))
            if "Tuple(UInt64,UUID)" not in row["id_column"].replace(" ",""):
                raise AssertionError(("Wrong RAW UUID type",layer,row))
        samples=int(sql(
            "SELECT sum(length(samples)) FROM "+inner(TABLE,"samples")+" FORMAT TSV"
        ).strip())
        if samples!=1330000:
            raise AssertionError(("Incorrect physical samples count",samples))
        report["sample_points"]=samples
        reference={}
        for case,(_,groups,_) in CASES.items():
            got=call("ch",case,start,end,f"r16_ch_oracle_{RUN}_{case}")
            if not got["ok"] or len(got["rows"])!=groups*14:
                raise AssertionError(("Native CH oracle failed",case,got.get("status"),
                                      len(got.get("rows",{})),groups*14))
            reference[case]=got["rows"]
            report.setdefault("ch_oracles",{})[case]={"points":len(got["rows"])}
        vm_stage=call("vm","stage",start,end,f"r16_vm_oracle_{RUN}_stage")
        if not vm_stage["ok"]:
            raise AssertionError(("Below-cap VM stage oracle failed",vm_stage))
        report["cross_engine_oracle"]=assert_same(
            reference["stage"],vm_stage["rows"],"stage14d","cross-engine"
        )
        pids={"vm":int(pathlib.Path(a.vm_pid_file).read_text()),
              "ch":server_status_pid(a.ch_pid_file)}
        report["pids"]=pids
        save(a.output,report)
        for n in (1,4,8):
            phases=[]
            rec={"phases":phases}
            report["stage_abba"][str(n)]=rec
            for order,engine in enumerate(("vm","ch","ch","vm")):
                record=phase(engine,n,order,start,end,pids,reference["stage"])
                phases.append(record)
                save(a.output,report)
            rec["summary"]=paired(phases)
            if not rec["summary"]["valid"]:
                report["errors"].append({"clients":n,"control_drift":rec["summary"]})
            print("R16_STAGE_COMPARE",json.dumps(
                {"clients":n,"summary":rec["summary"]},separators=(",",":")),flush=True)
            save(a.output,report)
        report["physical_after"]=physical(TABLE)
        if report["physical_after"]!=physical_before:
            raise AssertionError("Physical ClickHouse parts changed during queries")
        report["passed"]=not report["errors"]
    except Exception as exc:
        report["errors"].append({
            "fatal":str(exc)[:2000],"traceback":traceback.format_exc()[-4200:]
        })
        report["passed"]=False
    finally:
        save(a.output,report)
        compact={
            "passed":report["passed"],"errors":report["errors"],
            "vm_cap":report.get("vm_cap"),
            "cross_engine_oracle":report.get("cross_engine_oracle"),
            "ch_oracles":report.get("ch_oracles"),
            "stage":{k:v.get("summary") for k,v in report["stage_abba"].items()},
            "source_sha":SOURCE_SHA,"fixture_sha":FIXTURE_SHA,
        }
        save(str(pathlib.Path(a.output).with_suffix(".compact.json")),compact)
        print("R16_DONE",json.dumps(compact,separators=(",",":")),flush=True)
    if not report["passed"]:
        raise SystemExit(1)


if __name__=="__main__":
    main()
