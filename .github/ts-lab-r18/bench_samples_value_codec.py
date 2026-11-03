#!/usr/bin/env python3
"""R18: SHA-pinned native v11 TimeSeries SAMPLES value codec A/B/B/A.

Compare *only* the samples.value subcolumn codec:
stock ALP,ZSTD(3) versus opt-in ZSTD(1) or LZ4. The same source,
O1 executable, RAW UUID, timestamp codec, 3600s buckets and complete
synthetic input are held fixed. Check effective inner SHOW CREATE DDL,
actual physical parts, logical IDs, total sample elements, complete
native PromQL labels/timestamps/values, QueryFinish CPU, read marks and
memory, sampled CPU stacks and same-runner baseline drift.

Changing codec can change compressed bytes and adaptive mark layout.
Do not require the same marks or physical read rows: *measure* them.
Write/merge CPU is process-wide with bounded before/after snapshots;
never equate process CPU with ingest-only CPU or steady-state throughput.
"""
import argparse
import json
import os
import pathlib
import re
import statistics
import sys
import time
import traceback

sys.path[:0] = [
    ".github/ts-lab-r18", ".github/ts-lab-r01",
    ".github/ts-lab-r03", ".github/ts-lab-perf",
    ".github/ts-lab-bench", ".github/ts-lab-cpu"
]
from bench_granularity_v11 import (
    sql, inner, streamed_insert, optimize_table, wait_no_merges
)
from bench_bucket_cxx_abca import post, normalize_any, assert_same
from bench_filtered_selector_v4_sql import sql_string
from bench_native_multiclient_stock_20261103 import (
    physical, complete_logs, sha256, save, server_status_pid, rss_bytes
)
from bench_cpu_knobs import capture_profiles
from bench_jenkins_querypath import pct
from bench_k8s_mixed_native import CASES, DAY, jsonrows
from generate_k8s_churn_fixture import expected_counts, BASE

HOST = "http://127.0.0.1:18191"
SOURCE_SHA = "52529f0c711566d696917678bf15e7068772fdde"
BINARY_SHA = "f0a2fa1f814c58853f43423705cac10ab9df3bfe5887e27ebe9620ba7cb9660e"
FIXTURE_SHA = "ea9a8d43c9e58ed916f1bf43949556d8ed18169ddd14033c293af4cd54c9881d"
KINDS = ("alp", "zstd1", "lz4")
TARGETS = ("zstd1", "lz4")
CASES_TO_RUN = ("node_broad", "pod_namespace", "pod_broad", "burst15")
WARMUPS = 2
MEASURED = 8
RUN = os.getenv("GITHUB_RUN_ID", "manual")
TABLE_PREFIX = "tslab_r18_"


def table(kind):
    return TABLE_PREFIX + kind


def process_cpu_seconds(pid):
    """One low-frequency snapshot of process+all current TIDs, not on-query polling."""
    total = 0
    available = 0
    path = pathlib.Path("/proc") / str(pid) / "task"
    for entry in path.iterdir():
        try:
            total += int((entry / "schedstat").read_text().split()[0])
            available += 1
        except (OSError, ValueError, IndexError):
            continue
    if not available:
        raise RuntimeError("No process schedstat available for workload CPU control")
    return total / 1e9


def create_table(kind):
    name = table(kind)
    samples_codec = {
        "alp": None,
        "zstd1": "CODEC samples.value (ZSTD(1))",
        "lz4": "CODEC samples.value (LZ4)",
    }[kind]
    samples_sql = (
        "SAMPLES INNER COLUMNS (id Tuple(UInt64,UUID)) "
        if samples_codec is None else
        "SAMPLES INNER COLUMNS ("
        "id Tuple(UInt64,UUID),"
        "samples SimpleAggregateFunction(timeSeriesGroupArray,"
        "Array(Tuple(timestamp DateTime64(3), value Float64))),"
        "CODEC samples.timestamp (Delta,T64,ZSTD(3)),"
        f"{samples_codec}) "
    )
    ddl = (
        f"CREATE TABLE {name} ENGINE=TimeSeries "
        "SETTINGS recent_samples_ttl_seconds=0,samples_bucket_step_seconds=3600 "
        + samples_sql +
        "TAGS INNER COLUMNS ("
        "id Tuple(UInt64,UUID) DEFAULT tuple(sipHash64(metric_name),"
        "reinterpretAsUUID(sipHash128(tags))),"
        "metric_name LowCardinality(String),"
        "tags Map(LowCardinality(String),String),"
        "INDEX tags_idx tags TYPE text(tokenizer = 'keyValuePairs') "
        "GRANULARITY 100000000) "
        "TIME RANGES INNER COLUMNS (id Tuple(UInt64,UUID))"
    )
    sql(ddl)
    effective = sql("SHOW CREATE TABLE " + inner(name, "samples") + " FORMAT TSVRaw")
    # SHOW CREATE normalizes names with backticks and expands Delta to Delta(8).
    # Compare effective codec semantics, not superficial SQL rendering.
    normal = re.sub(r"\s+", "", effective).replace("`", "").lower()
    if kind == "alp":
        if "codecsamples.value(alp,zstd(3))" not in normal:
            raise AssertionError(("Default value codec not confirmed",kind,effective[:2800]))
    else:
        target = {
            "zstd1": "codecsamples.value(zstd(1))",
            "lz4": "codecsamples.value(lz4)"
        }[kind]
        if target not in normal:
            raise AssertionError(("Value subcolumn CODEC no-op or invalid",kind,effective[:2800]))
        if "codecsamples.value(alp" in normal:
            raise AssertionError(("ALP still enabled for override",kind,effective[:2800]))
    if not any(codec in normal for codec in (
        "codecsamples.timestamp(delta,t64,zstd(3))",
        "codecsamples.timestamp(delta(8),t64,zstd(3))",
    )):
        raise AssertionError(("Timestamp codec changed unexpectedly",kind,effective[:2800]))
    print("R18_CODEC_DDL",json.dumps({
        "kind":kind,"table":name,"effective_value":samples_codec or "ALP,ZSTD(3)",
        "create_ddl":effective[:4800]
    },separators=(",",":")),flush=True)
    return {"input_ddl":ddl,"effective_samples_ddl":effective}


def audit(name,manifest):
    p=physical(name)
    for layer in ("tags","timeranges","samples"):
        if p[layer]["parts"]["part_count"]!=1:
            raise AssertionError((name,"Not one active part",layer,p[layer]))
        if "Tuple(UInt64, UUID)" not in p[layer]["id_column"]:
            raise AssertionError((name,"Wrong ID type",layer,p[layer]))
    checks=jsonrows(
        "SELECT count() AS bucket_rows,sum(length(samples)) AS sample_points "
        "FROM "+inner(name,"samples")
    )[0]
    expected_buckets=int(manifest.get("bucket_rows_by_step",{}).get(
        "3600",manifest["series"]*3))
    if (int(checks["bucket_rows"])!=expected_buckets
        or int(checks["sample_points"])!=manifest["samples"]
        or int(p["samples"]["parts"]["rows"])!=expected_buckets
        or int(p["tags"]["parts"]["rows"])!=manifest["series"]):
        raise AssertionError((name,"Incomplete point/bucket count",checks,p))
    digest=jsonrows(
        "SELECT count() AS id_count,sum(sipHash64(toString(id))) AS digest "
        "FROM "+inner(name,"tags")
    )[0]
    if int(digest["id_count"])!=manifest["series"]:
        raise AssertionError((name,"Wrong tag identity count",digest))
    return {"physical":p,"samples":checks,"tags_ids":digest}


def query(case,end,kind):
    metric,selector,group,_=CASES[case]
    expr=f"avg by({group})(avg_over_time({metric}{selector}[1d]))"
    return(
        f"SELECT * FROM prometheusQueryRange({table(kind)},"
        f"{sql_string(expr)},{end-2*DAY},{end},{DAY}) "
        "SETTINGS max_threads=2,max_memory_usage=3500000000,"
        "log_queries=1,log_processors_profiles=1,"
        "query_profiler_cpu_time_period_ns=1000000 "
        "FORMAT JSONEachRow"
    )


def run_phase(case,kind,order,end,ref,expected,ticks,pid):
    stmt=query(case,end,kind)
    prefix=f"r18_{RUN}_{case}_{kind}_{order}_"
    state={
        "case":case,"kind":kind,"order":order,"query":stmt,
        "process_rss_before":rss_bytes(pid),"trials":[]
    }
    for i in range(WARMUPS+MEASURED):
        qid=f"{prefix}{i}"
        client_ms,body=post(HOST,stmt,qid)
        parsed=normalize_any(body)
        if len(parsed)!=expected or {stamp for _,stamp in parsed}!=ticks:
            raise AssertionError(("Bad full native response",case,kind,i,len(parsed),expected))
        assert_same(ref,parsed,case,f"r18_{kind}_{order}_{i}")
        state["trials"].append({
            "qid":qid,"client_ms":client_ms,"warmup":i<WARMUPS,
            "output_bytes":len(body)
        })
    state["process_rss_after"]=rss_bytes(pid)
    logged=complete_logs(prefix,{x["qid"] for x in state["trials"]})
    for tr in state["trials"]:
        row=logged[tr["qid"]]
        events=row.get("ProfileEvents",{})
        if "OSCPUVirtualTimeMicroseconds" not in events:
            raise AssertionError(("Missing QueryFinish CPU",tr["qid"]))
        tr.update({
            "server_ms":row["query_duration_ms"],
            "cpu_ms":round(events["OSCPUVirtualTimeMicroseconds"]/1000,3),
            "runqueue_ms":round(events.get("OSCPUWaitMicroseconds",0)/1000,3),
            "read_rows":row["read_rows"],
            "read_bytes":row["read_bytes"],
            "selected_marks":events.get("SelectedMarks"),
            "read_compressed_bytes":events.get("ReadCompressedBytes"),
            "peak_query_memory_bytes":row["memory_usage"]
        })
    measured=state["trials"][WARMUPS:]
    state["metrics"]={
        "p50_ms":pct([z["client_ms"] for z in measured],.5),
        "p95_ms":pct([z["client_ms"] for z in measured],.95),
        "cpu_p50_ms":pct([z["cpu_ms"] for z in measured],.5),
        "server_p50_ms":pct([z["server_ms"] for z in measured],.5),
        "runqueue_p50_ms":pct([z["runqueue_ms"] for z in measured],.5),
        "read_rows_p50":pct([z["read_rows"] for z in measured],.5),
        "selected_marks_p50":pct([z["selected_marks"] or 0 for z in measured],.5),
        "compressed_bytes_p50":pct([z["read_compressed_bytes"] or 0 for z in measured],.5),
        "peak_query_memory_bytes":max(z["peak_query_memory_bytes"] for z in measured),
        "output_bytes_median":statistics.median(z["output_bytes"] for z in measured),
    }
    state["representative_qid"]=measured[0]["qid"]
    print("R18_CODEC_PHASE",json.dumps({
        "case":case,"kind":kind,"order":order,"metrics":state["metrics"]
    },separators=(",",":")),flush=True)
    return state


def compare(phases,base,candidate):
    a=[x["metrics"] for x in phases if x["kind"]==base]
    b=[x["metrics"] for x in phases if x["kind"]==candidate]
    if len(a)!=len(b) or len(a)!=2:
        raise AssertionError("Incomplete A/B/B/A phase group")
    mean=lambda arr,key:statistics.mean(x[key] for x in arr)
    old=mean(a,"p50_ms")
    new=mean(b,"p50_ms")
    oldcpu=mean(a,"cpu_p50_ms")
    newcpu=mean(b,"cpu_p50_ms")
    control_drift=abs(a[0]["p50_ms"]-a[1]["p50_ms"])/old
    cpu_drift=abs(a[0]["cpu_p50_ms"]-a[1]["cpu_p50_ms"])/oldcpu
    return {
        "baseline":base,"candidate":candidate,
        "baseline_p50_ms":round(old,3),"candidate_p50_ms":round(new,3),
        "latency_ratio_candidate_over_base":round(new/old,5),
        "baseline_p95_ms":round(mean(a,"p95_ms"),3),
        "candidate_p95_ms":round(mean(b,"p95_ms"),3),
        "baseline_cpu_ms":round(oldcpu,3),"candidate_cpu_ms":round(newcpu,3),
        "cpu_ratio_candidate_over_base":round(newcpu/oldcpu,5),
        "baseline_read_rows":round(mean(a,"read_rows_p50"),1),
        "candidate_read_rows":round(mean(b,"read_rows_p50"),1),
        "baseline_selected_marks":round(mean(a,"selected_marks_p50"),1),
        "candidate_selected_marks":round(mean(b,"selected_marks_p50"),1),
        "baseline_compressed_read_bytes":round(mean(a,"compressed_bytes_p50"),1),
        "candidate_compressed_read_bytes":round(mean(b,"compressed_bytes_p50"),1),
        "baseline_query_memory_max":max(x["peak_query_memory_bytes"] for x in a),
        "candidate_query_memory_max":max(x["peak_query_memory_bytes"] for x in b),
        "control_drift_p50":round(control_drift,5),
        "control_drift_cpu":round(cpu_drift,5),
        "valid":control_drift<=.25 and cpu_drift<=.25
    }


def main():
    global FIXTURE_SHA, TABLE_PREFIX, TARGETS, KINDS, RUN
    parser=argparse.ArgumentParser()
    parser.add_argument("--fixture",required=True)
    parser.add_argument("--manifest",required=True)
    parser.add_argument("--pid-file",required=True)
    parser.add_argument("--output",required=True)
    parser.add_argument("--cases",nargs="+",choices=CASES_TO_RUN,default=list(CASES_TO_RUN))
    parser.add_argument("--fixture-sha256",default=FIXTURE_SHA)
    parser.add_argument("--table-prefix",default=TABLE_PREFIX)
    parser.add_argument("--candidate-codecs",nargs="+",choices=TARGETS,default=list(TARGETS))
    parser.add_argument("--run-suffix",default="")
    args=parser.parse_args()
    if not re.fullmatch(r"[a-z][a-z0-9_]{4,45}_",args.table_prefix):
        raise ValueError("Unsafe table prefix")
    if not re.fullmatch(r"[0-9a-f]{64}",args.fixture_sha256):
        raise ValueError("Fixture must have full sha256")
    if len(set(args.candidate_codecs))!=len(args.candidate_codecs):
        raise ValueError("Duplicate candidate codec")
    if not re.fullmatch(r"[a-z0-9_]{0,30}",args.run_suffix):
        raise ValueError("Unsafe run suffix")
    FIXTURE_SHA=args.fixture_sha256
    TABLE_PREFIX=args.table_prefix
    TARGETS=tuple(args.candidate_codecs)
    KINDS=("alp",)+TARGETS
    RUN=RUN+("_"+args.run_suffix if args.run_suffix else "")
    out={
        "question_id":"R18-timeseries-samples-value-codec-v1",
        "source_sha":SOURCE_SHA,
        "expected_binary_sha256":BINARY_SHA,
        "expected_fixture_sha256":FIXTURE_SHA,
        "table_prefix":TABLE_PREFIX,"candidate_codecs":TARGETS,
        "codec_DDL":{},"ingest":{},"optimize":{},
        "audits":{},"cases":{},"errors":[]
    }
    save(args.output,out)
    try:
        m=json.loads(pathlib.Path(args.manifest).read_text())
        series,samples=expected_counts(3,400)
        if (sha256(args.fixture)!=FIXTURE_SHA or m.get("sha256")!=FIXTURE_SHA
            or m.get("series")!=sum(series.values())
            or m.get("samples")!=sum(samples.values())
            or m.get("days")!=3 or m.get("node_slots")!=400 or m.get("base")!=BASE):
            raise AssertionError("Wrong pinned fixture identity/distribution")
        out["manifest"]=m
        pid=server_status_pid(args.pid_file)
        out["server_pid"]=pid
        for kind in KINDS:
            name=table(kind)
            out["codec_DDL"][kind]=create_table(kind)
            start=time.perf_counter()
            cpu0=process_cpu_seconds(pid)
            result=streamed_insert(name,args.fixture)
            cpu1=process_cpu_seconds(pid)
            if result["rows"]!=m["series"]:
                raise AssertionError(("Incomplete ingestion",name,result))
            out["ingest"][kind]={
                **result,"wall_s":round(time.perf_counter()-start,4),
                "whole_server_cpu_s":round(cpu1-cpu0,4)
            }
            start=time.perf_counter()
            cpu0=process_cpu_seconds(pid)
            opt=optimize_table(name)
            cpu1=process_cpu_seconds(pid)
            out["optimize"][kind]={
                "steps":opt,"wall_s":round(time.perf_counter()-start,4),
                "whole_server_cpu_s":round(cpu1-cpu0,4)
            }
            if any(isinstance(v,dict) for v in opt.values()):
                raise AssertionError(("Failed OPTIMIZE",kind,opt))
            print("R18_CODEC_INGEST",json.dumps({
                "kind":kind,"insert":out["ingest"][kind],"optimize":out["optimize"][kind]
            },separators=(",",":")),flush=True)
            save(args.output,out)
        wait_no_merges()
        for kind in KINDS:
            out["audits"][kind]=audit(table(kind),m)
        digests={str(v["tags_ids"]["digest"]) for v in out["audits"].values()}
        if len(digests)!=1:
            raise AssertionError(("Unequal canonical tag id hash",out["audits"]))
        out["physical_before"]={k:v["physical"] for k,v in out["audits"].items()}
        save(args.output,out)
        end=m["end"]
        ticks={end-i*DAY for i in range(m["days"])}
        for case in args.cases:
            response_id=f"r18_oracle_{RUN}_{case}"
            _lat,body=post(HOST,query(case,end,"alp"),response_id)
            ref=normalize_any(body)
            expected=m["days"]*CASES[case][3]
            if len(ref)!=expected or {ts for _,ts in ref}!=ticks:
                raise AssertionError(("Native reference empty/wrong grid",case,len(ref),expected))
            out["cases"][case]={}
            for candidate in TARGETS:
                pair={"phases":[],"expected_points":expected}
                out["cases"][case][candidate]=pair
                for order,kind in enumerate(("alp",candidate,candidate,"alp")):
                    phase=run_phase(case,kind,order,end,ref,expected,ticks,pid)
                    pair["phases"].append(phase)
                    save(args.output,out)
                pair["summary"]=compare(pair["phases"],"alp",candidate)
                if not pair["summary"]["valid"]:
                    out["errors"].append({"case":case,"candidate":candidate,
                                          "control_drift":pair["summary"]})
                try:
                    qids=[pair["phases"][i]["representative_qid"] for i in (0,1)]
                    pair["sampled_cpu_and_processors"]=capture_profiles(qids)
                except Exception as e:
                    pair["profile_error"]=str(e)[:1600]
                print("R18_CODEC_COMPARE",json.dumps({
                    "case":case,"candidate":candidate,**pair["summary"]
                },separators=(",",":")),flush=True)
                save(args.output,out)
        out["physical_after"]={k:physical(table(k)) for k in KINDS}
        if out["physical_before"]!=out["physical_after"]:
            raise AssertionError("Physical data parts changed during read-only measurements")
        out["passed"]=not out["errors"]
    except Exception as e:
        out["errors"].append({
            "fatal":str(e)[:2800],"traceback":traceback.format_exc()[-4400:]
        })
        out["passed"]=False
    finally:
        save(args.output,out)
        compact={
            "question_id":out["question_id"],"passed":out["passed"],
            "errors":out["errors"],"manifest":out.get("manifest"),
            "codec_DDL":out["codec_DDL"],"ingest":out["ingest"],
            "optimize":out["optimize"],"audits":out["audits"],
            "cases":{k:{candidate:val.get("summary") for candidate,val in d.items()}
                     for k,d in out["cases"].items()},
            "physical_before":out.get("physical_before"),
            "physical_after":out.get("physical_after")
        }
        save(str(pathlib.Path(args.output).with_suffix(".compact.json")),compact)
        print("R18_CODEC_DONE",json.dumps({
            "passed":out["passed"],"errors":out["errors"],
            "cases":compact["cases"]
        },separators=(",",":")),flush=True)
    if not out["passed"]:
        raise SystemExit(1)


if __name__=="__main__":
    main()
