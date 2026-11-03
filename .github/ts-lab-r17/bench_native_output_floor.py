#!/usr/bin/env python3
"""R17: native TimeSeries FORMAT JSONEachRow vs FORMAT Null CPU-cost A/B/B/A.

Only use output-format variants to bound output-dependent CPU. FORMAT Null
does not prove full PromQL correctness; all JSON phases are checked against
the same nonempty native reference, including labels, times and values.
Require unchanged rows and marks or classify an execution-path confound.
No VM comparison or production optimization is claimed by this script.
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
    ".github/ts-lab-r17",
    ".github/ts-lab-r03",
    ".github/ts-lab-r01",
    ".github/ts-lab-perf",
    ".github/ts-lab-bench",
    ".github/ts-lab-cpu",
]
from bench_granularity_v11 import sql, streamed_insert, optimize_table, wait_no_merges
from bench_bucket_cxx_abca import post, normalize_any, assert_same
from bench_native_multiclient_stock_20261103 import (
    complete_logs, physical, sha256, save, server_status_pid, rss_bytes
)
from bench_cpu_knobs import capture_profiles
from bench_jenkins_querypath import pct
from bench_k8s_mixed_native import CASES, DAY
from bench_native_vm_concurrency import CH_TABLE, create_table, query_expr
from generate_k8s_churn_fixture import BASE, expected_counts
from bench_filtered_selector_v4_sql import sql_string

SOURCE_SHA = "52529f0c711566d696917678bf15e7068772fdde"
BINARY_SHA = "f0a2fa1f814c58853f43423705cac10ab9df3bfe5887e27ebe9620ba7cb9660e"
FIXTURE_SHA = "ea9a8d43c9e58ed916f1bf43949556d8ed18169ddd14033c293af4cd54c9881d"
RUN = os.getenv("GITHUB_RUN_ID", "manual")
HOST = "http://127.0.0.1:18191"
CASES_TO_RUN = ("node_broad", "pod_namespace", "pod_broad", "burst15")
WARMUPS = 2
MEASURED = 24
ABBA = ("json", "null", "null", "json")


def query(case, end, fmt):
    start = end - 2 * DAY
    return (
        f"SELECT * FROM prometheusQueryRange({CH_TABLE},"
        f"{sql_string(query_expr(case))},{start},{end},{DAY}) "
        f"SETTINGS max_threads=2,max_memory_usage=3500000000,"
        "log_queries=1,log_processors_profiles=1,"
        "query_profiler_cpu_time_period_ns=1000000 "
        f"FORMAT {'JSONEachRow' if fmt == 'json' else 'Null'}"
    )


def phase(case, order, fmt, end, reference, expected, ticks, pid):
    prefix = f"r17_{RUN}_{case}_{order}_{fmt}_"
    statement = query(case, end, fmt)
    record = {
        "case": case, "order": order, "format": fmt,
        "query": statement, "trials": [],
        "process_before": rss_bytes(pid)
    }
    for i in range(WARMUPS + MEASURED):
        qid = f"{prefix}{i}"
        elapsed, body = post(HOST, statement, qid)
        if fmt == "json":
            parsed = normalize_any(body)
            if len(parsed) != expected or {t for _, t in parsed} != ticks:
                raise AssertionError(("JSON output cardinality/grid mismatch",case,i,len(parsed),expected))
            assert_same(reference, parsed, case, f"r17_json_{order}_{i}")
        elif body:
            raise AssertionError(("FORMAT Null returned a nonempty HTTP body",case,len(body)))
        record["trials"].append({
            "qid": qid, "warmup": i < WARMUPS,
            "client_ms": elapsed, "response_bytes": len(body)
        })
    record["process_after"] = rss_bytes(pid)
    ids = {row["qid"] for row in record["trials"]}
    logs = complete_logs(prefix, ids)
    for trial in record["trials"]:
        entry = logs[trial["qid"]]
        ev = entry.get("ProfileEvents", {})
        if "OSCPUVirtualTimeMicroseconds" not in ev or not entry["query_duration_ms"]:
            raise AssertionError(("Missing valid QueryFinish CPU/wall",trial["qid"]))
        trial.update({
            "server_ms": entry["query_duration_ms"],
            "cpu_ms": round(ev["OSCPUVirtualTimeMicroseconds"] / 1000, 3),
            "runnable_ms": round(ev.get("OSCPUWaitMicroseconds",0)/1000, 3),
            "read_rows": entry["read_rows"],
            "read_bytes": entry["read_bytes"],
            "selected_marks": ev.get("SelectedMarks"),
            "compressed_read_bytes": ev.get("ReadCompressedBytes"),
            "peak_query_memory_bytes": entry["memory_usage"],
        })
    samples = record["trials"][WARMUPS:]
    record["metrics"] = {
        "client_p50_ms": pct([z["client_ms"] for z in samples], 0.5),
        "client_p95_ms": pct([z["client_ms"] for z in samples], 0.95),
        "server_p50_ms": pct([z["server_ms"] for z in samples], 0.5),
        "cpu_p50_ms": pct([z["cpu_ms"] for z in samples], 0.5),
        "runnable_p50_ms": pct([z["runnable_ms"] for z in samples], 0.5),
        "read_rows_median": statistics.median(z["read_rows"] for z in samples),
        "read_bytes_median": statistics.median(z["read_bytes"] for z in samples),
        "marks_median": statistics.median((z["selected_marks"] or 0) for z in samples),
        "compressed_read_bytes_median": statistics.median(
            (z["compressed_read_bytes"] or 0) for z in samples
        ),
        "memory_max_bytes": max(z["peak_query_memory_bytes"] for z in samples),
        "response_bytes_median": statistics.median(z["response_bytes"] for z in samples),
        "requests_measured": len(samples),
    }
    record["representative_qid"] = samples[0]["qid"]
    print("R17_FORMAT_PHASE",json.dumps({
        "case":case,"order":order,"format":fmt,"metrics":record["metrics"]
    }, separators=(",", ":")),flush=True)
    return record


def compare(phases):
    a = [p["metrics"] for p in phases if p["format"]=="json"]
    b = [p["metrics"] for p in phases if p["format"]=="null"]
    assert len(a) == len(b) == 2
    mean = lambda x,k: statistics.mean(z[k] for z in x)
    baseline = mean(a,"client_p50_ms")
    candidate = mean(b,"client_p50_ms")
    baseline_cpu = mean(a,"cpu_p50_ms")
    candidate_cpu = mean(b,"cpu_p50_ms")
    wall_drift = abs(a[0]["client_p50_ms"]-a[1]["client_p50_ms"])/baseline
    cpu_drift = abs(a[0]["cpu_p50_ms"]-a[1]["cpu_p50_ms"])/baseline_cpu
    rows = {p["metrics"]["read_rows_median"] for p in phases}
    marks = {p["metrics"]["marks_median"] for p in phases}
    bytes_read = {p["metrics"]["compressed_read_bytes_median"] for p in phases}
    # ReadCompressedBytes is affected by compressed-cache state even with
    # identical selected parts. Do not turn cache variance into a plan failure.
    physical_same = len(rows)==len(marks)==1
    if not physical_same:
        raise AssertionError(("Output format changed query selected rows/marks",sorted(rows),sorted(marks)))
    return {
        "json_client_p50_ms":round(baseline,3),
        "null_client_p50_ms":round(candidate,3),
        "json_server_p50_ms":round(mean(a,"server_p50_ms"),3),
        "null_server_p50_ms":round(mean(b,"server_p50_ms"),3),
        "json_cpu_p50_ms":round(baseline_cpu,3),
        "null_cpu_p50_ms":round(candidate_cpu,3),
        "cpu_format_dependent_diff_ms":round(baseline_cpu-candidate_cpu,3),
        "cpu_format_dependent_fraction":round((baseline_cpu-candidate_cpu)/baseline_cpu,5),
        "wall_format_dependent_diff_ms":round(baseline-candidate,3),
        "output_bytes_json":round(mean(a,"response_bytes_median"),1),
        "output_bytes_null":round(mean(b,"response_bytes_median"),1),
        "read_rows":next(iter(rows)),
        "selected_marks":next(iter(marks)),
        "json_compressed_bytes":round(mean(a,"compressed_read_bytes_median"),2),
        "null_compressed_bytes":round(mean(b,"compressed_read_bytes_median"),2),
        "baseline_wall_drift":round(wall_drift,5),
        "baseline_cpu_drift":round(cpu_drift,5),
        "valid":wall_drift <= 0.25 and cpu_drift <= 0.25,
        "interpretation":"Format-dependent bound, not pure JSON serializer cost or global ClickHouse/VM speedup"
    }


def main():
    parser=argparse.ArgumentParser()
    parser.add_argument("--fixture",required=True)
    parser.add_argument("--manifest",required=True)
    parser.add_argument("--pid-file",required=True)
    parser.add_argument("--output",required=True)
    parser.add_argument("--cases",nargs="+",choices=CASES_TO_RUN,default=list(CASES_TO_RUN))
    args=parser.parse_args()
    report={
        "question_id":"R17-native-output-cost-cpu-floor-v1",
        "source_sha":SOURCE_SHA,"binary_sha256_expected":BINARY_SHA,
        "fixture_sha256":FIXTURE_SHA,"cases":{},"errors":[],
        "caveat":"FORMAT Null changes result output: validate JSON phases separately, never treat Null as full PromQL parity",
    }
    save(args.output,report)
    try:
        m=json.loads(pathlib.Path(args.manifest).read_text())
        rows,samples=expected_counts(3,400)
        if (m["sha256"]!=FIXTURE_SHA or sha256(args.fixture)!=FIXTURE_SHA
            or m["series"]!=sum(rows.values()) or m["samples"]!=sum(samples.values())
            or m["days"]!=3 or m["node_slots"]!=400 or m["base"]!=BASE):
            raise AssertionError("Wrong synthetic 400-slot fixture or SHA/distribution")
        report["manifest"]=m
        create_table()
        report["ingest"]=streamed_insert(CH_TABLE,args.fixture)
        if report["ingest"]["rows"]!=m["series"]:
            raise AssertionError("Incomplete source fixture ingest")
        report["optimize"]=optimize_table(CH_TABLE)
        if any(isinstance(v,dict) for v in report["optimize"].values()):
            raise AssertionError("OPTIMIZE did not finish")
        wait_no_merges()
        before=physical(CH_TABLE)
        report["physical_before"]=before
        if any(p["parts"]["part_count"]!=1 for p in before.values()):
            raise AssertionError("Unfrozen inner parts")
        for item in before.values():
            if "Tuple(UInt64, UUID)" not in item["id_column"]:
                raise AssertionError(("Wrong RAW UUID baseline ID",item["id_column"]))
        pid=server_status_pid(args.pid_file)
        report["server_pid"]=pid
        save(args.output,report)
        end=int(m["end"])
        ticks={end-i*DAY for i in range(3)}
        for case in args.cases:
            eid=f"r17oracle_{RUN}_{case}"
            _elapsed,body=post(HOST,query(case,end,"json"),eid)
            oracle=normalize_any(body)
            expected=3*CASES[case][3]
            if len(oracle)!=expected or {stamp for _,stamp in oracle}!=ticks:
                raise AssertionError(("Native oracle grid or output empty",case,len(oracle),expected))
            state={"phases":[],"expected_points":expected}
            report["cases"][case]=state
            for order,fmt in enumerate(ABBA):
                ph=phase(case,order,fmt,end,oracle,expected,ticks,pid)
                state["phases"].append(ph)
                save(args.output,report)
            state["summary"]=compare(state["phases"])
            try:
                qids=[state["phases"][i]["representative_qid"] for i in (0,1)]
                state["representative_processor_and_trace_profiles"]=capture_profiles(qids)
            except Exception as e:
                state["profile_error"]=str(e)[:1600]
            if not state["summary"]["valid"]:
                report["errors"].append({"case":case,"invalid_baseline_drift":state["summary"]})
            print("R17_FORMAT_COMPARE",json.dumps({"case":case,**state["summary"]},
                separators=(",",":")),flush=True)
            save(args.output,report)
        report["physical_after"]=physical(CH_TABLE)
        if report["physical_after"]!=before:
            raise AssertionError("Physical parts changed during read-only tests")
        report["passed"]=not report["errors"]
    except Exception as e:
        report["errors"].append({"fatal":str(e)[:2600],
                                 "traceback":traceback.format_exc()[-4700:]})
        report["passed"]=False
    finally:
        save(args.output,report)
        compact={
            "question_id":report["question_id"],"passed":report["passed"],
            "errors":report["errors"],"source_sha":SOURCE_SHA,
            "binary_sha256_expected":BINARY_SHA,"fixture_sha256":FIXTURE_SHA,
            "cases":{k:v.get("summary") for k,v in report["cases"].items()},
            "physical_before":report.get("physical_before"),
            "physical_after":report.get("physical_after"),
        }
        save(str(pathlib.Path(args.output).with_suffix(".compact.json")),compact)
        print("R17_FORMAT_DONE",json.dumps(compact,separators=(",",":")),flush=True)
    if not report["passed"]:raise SystemExit(1)


if __name__=="__main__":
    main()
