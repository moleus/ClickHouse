#!/usr/bin/env python3
"""R10 next gate: native short-lookback TimeSeries 900/3600/10800s bucket pruning.

Same stock O1 RAW UUID schema/binary and exact 3d/400-slot mixed-cadence
R01 fixture. Compare 900 and 10800 seconds against 3600 by full native
PromQL A/B/B/A at equal evaluation timestamps near bucket boundaries,
with exact label/timestamp/numeric parity. Query logs CPU, read_rows, marks,
compressed IO, RSS observer cost and query memory; no C++ compilation.
"""
import argparse
import json
import os
import pathlib
import statistics
import sys
import time
import traceback

sys.path[:0] = [
    ".github/ts-lab-r10", ".github/ts-lab-r01",
    ".github/ts-lab-perf", ".github/ts-lab-bench", ".github/ts-lab-cpu",
]
from bench_granularity_v11 import sql, inner, streamed_insert, optimize_table, wait_no_merges
from bench_bucket_cxx_abca import assert_same, normalize_any
from bench_jenkins_querypath import post, pct
from bench_filtered_selector_v4_sql import sql_string
from bench_native_multiclient_stock_20261103 import (
    physical, sha256, save, server_status_pid, RSSMonitor, complete_logs
)
from bench_bucket_id_factorial import create_table, table, physical_audit, FIXTURE_SHA
from generate_k8s_churn_fixture import BASE, LIFE, expected_counts

STEPS = (900, 3600, 10800)
HOST = "http://127.0.0.1:18191"
# Each timestamp lies within the 3-day reproducible fixture. 8-hour offsets
# sit near an hourly boundary; 9h05m is immediately after 3h-node turnover.
# Each test selects the real, independent native PromQL range-vector path.
CASES = {
    "within_hour_15m": {"metric": "tslab_pod_cpu_ratio", "selector": '{namespace="team-000"}',
                         "group": "namespace", "offset": 8*3600+50*60,
                         "lookback": "15m", "expected_groups": 1},
    "hour_cross_15m": {"metric": "tslab_pod_cpu_ratio", "selector": '{namespace="team-000"}',
                        "group": "namespace", "offset": 8*3600+5*60,
                        "lookback": "15m", "expected_groups": 1},
    "life_cross_15m": {"metric": "tslab_pod_cpu_ratio", "selector": '{namespace="team-000"}',
                        "group": "namespace", "offset": 9*3600+5*60,
                        "lookback": "15m", "expected_groups": 1},
    "namespace_1h": {"metric": "tslab_pod_cpu_ratio", "selector": '{namespace="team-000"}',
                      "group": "namespace", "offset": 8*3600+40*60,
                      "lookback": "1h", "expected_groups": 1},
    "namespace_3h": {"metric": "tslab_pod_cpu_ratio", "selector": '{namespace="team-000"}',
                      "group": "namespace", "offset": 8*3600+40*60,
                      "lookback": "3h", "expected_groups": 1},
    "nodes_15m": {"metric": "tslab_node_cpu_ratio", "selector": "",
                   "group": "project", "offset": 8*3600+5*60,
                   "lookback": "15m", "expected_groups": 8},
}
WARMUPS = 2
MEASURED = 6


def query(table_name, case):
    cfg = CASES[case]
    stamp = BASE + cfg["offset"]
    expr = (f"avg by({cfg['group']})(avg_over_time("
            f"{cfg['metric']}{cfg['selector']}[{cfg['lookback']}]))")
    return (
        f"SELECT * FROM prometheusQueryRange({table_name},{sql_string(expr)},"
        f"{stamp},{stamp},86400) SETTINGS max_threads=4,"
        "max_memory_usage=3000000000,log_queries=1,"
        "log_processors_profiles=1,query_profiler_cpu_time_period_ns=1000000 "
        "FORMAT JSONEachRow"
    )


def oracle(case):
    timestamp = BASE + CASES[case]["offset"]
    identifier = f"r10shortoracle_{case}_{os.getenv('GITHUB_RUN_ID','local')}"
    _,raw = post(HOST,query(table("raw",3600),case),identifier)
    parsed = normalize_any(raw)
    expected = CASES[case]["expected_groups"]
    if len(parsed) != expected or {t for _labels,t in parsed} != {timestamp}:
        raise AssertionError((case,"Wrong nonempty native output grid",len(parsed),expected))
    return parsed


def phase_run(pair, case, step, order, pid, reference):
    t=table("raw",step)
    statement=query(t,case)
    prefix=f"r10short_{pair}_{case}_{step}_{order}_{os.getenv('GITHUB_RUN_ID','local')}_"
    phase={"step":step,"order":order,"table":t,"query":statement,"trials":[]}
    with RSSMonitor(pid) as observer:
        for repeat in range(WARMUPS+MEASURED):
            qid=prefix+str(repeat)
            ms,body=post(HOST,statement,qid)
            observed=normalize_any(body)
            assert_same(reference,observed,case,"short-window")
            phase["trials"].append({"qid":qid,"client_ms":ms,
                                    "warmup":repeat<WARMUPS})
    phase["rss"]=observer.report()
    if phase["rss"]["observer_overhead_fraction"]>.02:
        raise AssertionError("RSS observer exceeded 2% of window")
    logs=complete_logs(prefix,{x["qid"] for x in phase["trials"]})
    for trial in phase["trials"]:
        row=logs[trial["qid"]]
        ev=row.get("ProfileEvents",{})
        trial.update({
            "cpu_ms":round(ev.get("OSCPUVirtualTimeMicroseconds",0)/1000,3),
            "runqueue_wait_ms":round(ev.get("OSCPUWaitMicroseconds",0)/1000,3),
            "selected_marks":ev.get("SelectedMarks"),
            "compressed_read_bytes":ev.get("ReadCompressedBytes"),
            "read_rows":row["read_rows"],"read_bytes":row["read_bytes"],
            "query_memory_bytes":row["memory_usage"],
        })
    measured=phase["trials"][WARMUPS:]
    phase["metrics"]={
        "p50_ms":pct([x["client_ms"] for x in measured],.5),
        "p95_ms":pct([x["client_ms"] for x in measured],.95),
        "cpu_ms":round(statistics.median(x["cpu_ms"] for x in measured),3),
        "runqueue_wait_ms":round(statistics.median(
            x["runqueue_wait_ms"] for x in measured),3),
        "read_rows":statistics.median(x["read_rows"] for x in measured),
        "read_bytes":statistics.median(x["read_bytes"] for x in measured),
        "compressed_read_bytes":statistics.median(
            x["compressed_read_bytes"] or 0 for x in measured),
        "selected_marks":statistics.median(
            x["selected_marks"] or 0 for x in measured),
        "query_memory_max_bytes":max(x["query_memory_bytes"] for x in measured),
        "rss_observed_peak_bytes":phase["rss"]["rss_observed_max_bytes"],
    }
    print("R10_SHORT_PHASE",json.dumps({
        "pair":pair,"case":case,"step":step,"order":order,
        "metrics":phase["metrics"]},separators=(",",":")),flush=True)
    return phase


def compare(case, candidate, pid, reference):
    pair=f"{case}_{candidate}"
    phases=[
        phase_run(pair,case,step,order,pid,reference)
        for order,step in enumerate((3600,candidate,candidate,3600))
    ]
    base=[p["metrics"] for p in phases if p["step"]==3600]
    alt=[p["metrics"] for p in phases if p["step"]==candidate]
    mean=lambda ls,key:statistics.mean(x[key] for x in ls)
    p50b=mean(base,"p50_ms")
    p50a=mean(alt,"p50_ms")
    drift=abs(base[0]["p50_ms"]-base[1]["p50_ms"])/p50b
    summary={
        "case":case,"baseline_seconds":3600,"candidate_seconds":candidate,
        "baseline_p50_ms":round(p50b,3),
        "candidate_p50_ms":round(p50a,3),
        "candidate_vs_baseline_p50_ratio":round(p50a/p50b,5),
        "baseline_p95_ms":round(mean(base,"p95_ms"),3),
        "candidate_p95_ms":round(mean(alt,"p95_ms"),3),
        "baseline_cpu_ms":round(mean(base,"cpu_ms"),3),
        "candidate_cpu_ms":round(mean(alt,"cpu_ms"),3),
        "baseline_read_rows":round(mean(base,"read_rows"),2),
        "candidate_read_rows":round(mean(alt,"read_rows"),2),
        "baseline_compressed_bytes":round(mean(base,"compressed_read_bytes"),2),
        "candidate_compressed_bytes":round(mean(alt,"compressed_read_bytes"),2),
        "control_drift":round(drift,5),
        "valid":drift<=.25
    }
    print("R10_SHORT_COMPARE",json.dumps(summary,separators=(",",":")),flush=True)
    return {"summary":summary,"phases":phases}


def main():
    parser=argparse.ArgumentParser()
    parser.add_argument("--fixture",required=True)
    parser.add_argument("--manifest",required=True)
    parser.add_argument("--pid-file",required=True)
    parser.add_argument("--output",required=True)
    parser.add_argument("--cases",nargs="+",choices=CASES,default=list(CASES))
    args=parser.parse_args()
    report={"question_id":"R10-bucket-id-factorial-v1",
            "dependent_gate":"short-lookback native time pruning",
            "fixture_sha256":FIXTURE_SHA,"cases":{},"errors":[]}
    save(args.output,report)
    try:
        m=json.loads(pathlib.Path(args.manifest).read_text())
        n,s=expected_counts(3,400)
        if (sha256(args.fixture)!=FIXTURE_SHA or m["sha256"]!=FIXTURE_SHA
            or m["series"]!=sum(n.values()) or m["samples"]!=sum(s.values())
            or m["days"]!=3 or m["node_slots"]!=400):
            raise AssertionError("Mismatched frozen R01 fixture")
        report["fixture"]=m
        pid=server_status_pid(args.pid_file)
        report["server_pid"]=pid
        for step in STEPS:
            name=table("raw",step)
            create_table("raw",step)
            inserted=streamed_insert(name,args.fixture)
            if inserted["rows"]!=m["series"]:
                raise AssertionError((name,"wrong distinct series ingest",inserted))
            report.setdefault("ingest",{})[name]=inserted
            report.setdefault("optimize",{})[name]=optimize_table(name)
            if any(isinstance(value,dict) for value in report["optimize"][name].values()):
                raise AssertionError((name,"incomplete optimize",report["optimize"][name]))
        wait_no_merges()
        report["physical_before"]={}
        for step in STEPS:
            name=table("raw",step)
            audit=physical_audit(
                name,m["series"],m["samples"],m["series"]*LIFE//step,"raw"
            )
            report["physical_before"][name]=audit
        digests={v["identity"]["digest"] for v in report["physical_before"].values()}
        if len(digests)!=1:raise AssertionError("Nonidentical ID hash digest")
        save(args.output,report)
        for case in args.cases:
            expected=oracle(case)
            results={}
            report["cases"][case]=results
            for step in (900,10800):
                result=compare(case,step,pid,expected)
                results[str(step)]=result
                if not result["summary"]["valid"]:
                    report["errors"].append({
                        "case":case,"step":step,
                        "control_drift":result["summary"]["control_drift"]
                    })
                save(args.output,report)
        report["physical_after"]={name:physical(name) for name in report["physical_before"]}
        if report["physical_after"]!={name:a["physical"] for name,a in report["physical_before"].items()}:
            raise AssertionError("Physical parts changed during read-only queries")
        report["passed"]=not report["errors"]
    except Exception as e:
        report["errors"].append({"fatal":str(e)[:2200],
                                  "traceback":traceback.format_exc()[-3600:]})
        report["passed"]=False
    finally:
        save(args.output,report)
        compact={
            "passed":report["passed"],"errors":report["errors"],
            "cases":{case:{step:result["summary"] for step,result in groups.items()}
                      for case,groups in report["cases"].items()},
            "physical":report.get("physical_before"),
            "fixture":report.get("fixture")
        }
        save(str(pathlib.Path(args.output).with_suffix(".compact.json")),compact)
        print("R10_SHORT_DONE",json.dumps(compact,separators=(",",":")),flush=True)
    if not report["passed"]:raise SystemExit(1)


if __name__=="__main__":
    main()
