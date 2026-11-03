#!/usr/bin/env python3
"""R18: 2x2 value codec x node-start alignment under 1/4/8 native clients.

Intervention A: only sample timestamps shifted by R10's deterministic
(slot*317)%10800s start offset; identical TAGS, Float64 values and
number of points. Intervention B: only SAMPLES.value codec ALP+ZSTD3
vs ZSTD1, with 1h bucket, all RAW_UUID ID layers, pinned stock O1.

Run full native PromQL JSON output oracle separately per alignment;
never assert equal evaluated output across two different timestamps.
A/B/B/A inner codec phases on one runner and four outer align/stagger/
stagger/align panels; 2 warmups + 6 measured waves at 1/4/8 clients.
All 3 selectors and each client level must pass drift and correctness.
No new C++ build, no VM, no claim about production compiler/allocator.
"""
import argparse
import concurrent.futures
import hashlib
import json
import os
import pathlib
import statistics
import sys
import threading
import time
import traceback

sys.path[:0]=[
    ".github/ts-lab-r18", ".github/ts-lab-r10",
    ".github/ts-lab-r03", ".github/ts-lab-r01",
    ".github/ts-lab-perf", ".github/ts-lab-bench",
    ".github/ts-lab-cpu"
]
import bench_samples_value_codec as base
from bench_granularity_v11 import sql, streamed_insert, optimize_table, wait_no_merges
from bench_bucket_cxx_abca import post, normalize_any, assert_same
from bench_native_multiclient_stock_20261103 import (
    physical, complete_logs, sha256, save, server_status_pid, rss_bytes
)
from bench_k8s_mixed_native import CASES, DAY
from bench_native_vm_concurrency import proc_cpu_seconds, cpu_schedstat_snapshot
from generate_k8s_churn_fixture import BASE, expected_counts
from bench_jenkins_querypath import pct

SOURCE_SHA="52529f0c711566d696917678bf15e7068772fdde"
BINARY_SHA="f0a2fa1f814c58853f43423705cac10ab9df3bfe5887e27ebe9620ba7cb9660e"
SHA={
    "aligned":"ea9a8d43c9e58ed916f1bf43949556d8ed18169ddd14033c293af4cd54c9881d",
    "stagger":"ae85496efcab3417634ac812cbd50a40c4eea297a397b50792efd024838e1b9a",
}
BUCKETS={"aligned":86688,"stagger":113760}
KINDS=("alp","zstd1")
ALIGNMENT_ORDER=("aligned","stagger","stagger","aligned")
CODEC_ORDER=("alp","zstd1","zstd1","alp")
CASES_TO_RUN=("pod_namespace","burst15","node_broad")
CLIENTS=(1,4,8)
WARMUP_WAVES=2
MEASURED_WAVES=6
RUN=os.getenv("GITHUB_RUN_ID","local")
HOST="http://127.0.0.1:18191"


def name(align,kind):
    return f"tslab_r18_{align}_{kind}"


def query(align,kind,case,end):
    base.TABLE_PREFIX=f"tslab_r18_{align}_"
    return base.query(case,end,kind)


def check_fixture_pair(path0,path1,expected_series,expected_points):
    """Independent full value/label identity proof; timestamps alone shift."""
    matched=changed=points=0
    h=hashlib.sha256()
    with open(path0,"rb") as src0,open(path1,"rb") as src1:
        for row_id,(a,b) in enumerate(zip(src0,src1,strict=True)):
            l=json.loads(a)
            r=json.loads(b)
            if (l["metric_name"]!=r["metric_name"] or l["tags"]!=r["tags"]
                or len(l["samples"])!=len(r["samples"])):
                raise AssertionError(("Different logical identity or point count",row_id))
            for (t0,v0),(t1,v1) in zip(l["samples"],r["samples"],strict=True):
                if v0!=v1:
                    raise AssertionError(("Value changed with node clock offset",row_id))
                changed+=int(t0!=t1)
                points+=1
            h.update(json.dumps([l["metric_name"],l["tags"],
                [v for _,v in l["samples"]]],sort_keys=True,
                separators=(",",":")).encode()+b"\n")
            matched+=1
    if matched!=expected_series or points!=expected_points or changed<points*.5:
        raise AssertionError(("Unequal labels/values or not enough clock offset",matched,points,changed))
    return {"logical_series":matched,"sample_points":points,
            "timestamps_shifted":changed,
            "identical_label_value_digest":h.hexdigest()}


def oracle(align,case,end,run):
    qid=f"r18c_oracle_{RUN}_{align}_{case}_{run}"
    _wall,body=post(HOST,query(align,"alp",case,end),qid)
    rows=normalize_any(body)
    expected=3*CASES[case][3]
    ticks={end-(2-i)*DAY for i in range(3)}
    if len(rows)!=expected or {t for _,t in rows}!=ticks:
        raise AssertionError(("Invalid native oracle",align,case,len(rows),expected))
    return rows,ticks,expected


def do_wave(pool,n,stmt,prefix,index,reference,ticks,expected,case):
    barrier=threading.Barrier(n+1,timeout=35)
    def request(i):
        barrier.wait()
        start=time.perf_counter()
        qid=f"{prefix}_{index}_{i}"
        ms,body=post(HOST,stmt,qid)
        end=time.perf_counter()
        got=normalize_any(body)
        if len(got)!=expected or {t for _,t in got}!=ticks:
            raise AssertionError(("Wrong native output cardinality/time",qid,len(got),expected))
        assert_same(reference,got,case,qid)
        return {"qid":qid,"start":start,"end":end,
                "client_ms":ms,"wave":index,"warmup":index<WARMUP_WAVES,
                "output_bytes":len(body)}
    jobs=[pool.submit(request,i) for i in range(n)]
    barrier.wait()
    samples=[x.result(timeout=260) for x in jobs]
    span=max(x["end"] for x in samples)-min(x["start"] for x in samples)
    return {"wave":index,"duration_s":round(span,6),
            "warmup":index<WARMUP_WAVES,"trials":samples}


def phase(align,kind,case,n,panel_index,order,server_pid,ref,ticks,expected,end):
    q=query(align,kind,case,end)
    prefix=f"r18c_{RUN}_{align}_{case}_{n}_p{panel_index}_i{order}_{kind}"
    record={
        "alignment":align,"kind":kind,"case":case,"clients":n,
        "panel_index":panel_index,"phase":order,"query":q,
        "before_rss":rss_bytes(server_pid),"waves":[],
        "trials":[],"server_pid":server_pid
    }
    snap0=snap1=None
    cpu0=cpu1=None
    with concurrent.futures.ThreadPoolExecutor(max_workers=n) as pool:
        for wave_index in range(WARMUP_WAVES+MEASURED_WAVES):
            if wave_index==WARMUP_WAVES:
                cpu0=proc_cpu_seconds(server_pid)
                snap0=cpu_schedstat_snapshot(server_pid)
            wave=do_wave(pool,n,q,prefix,wave_index,ref,ticks,expected,case)
            record["waves"].append(wave)
            record["trials"].extend(wave["trials"])
        cpu1=proc_cpu_seconds(server_pid)
        snap1=cpu_schedstat_snapshot(server_pid)
    record["after_rss"]=rss_bytes(server_pid)
    measured=[x for x in record["trials"] if not x["warmup"]]
    durations=[x["duration_s"] for x in record["waves"] if not x["warmup"]]
    if len(measured)!=MEASURED_WAVES*n:
        raise AssertionError(("Missing measured native trials",align,kind,case,n,len(measured)))
    logs=complete_logs(prefix,{x["qid"] for x in record["trials"]})
    for sample in record["trials"]:
        item=logs[sample["qid"]]
        ev=item.get("ProfileEvents",{})
        if (item.get("type")!="QueryFinish" or
            "OSCPUVirtualTimeMicroseconds" not in ev):
            raise AssertionError(("Missing valid QueryFinish",sample["qid"]))
        sample.update({
            "cpu_ms":round(ev["OSCPUVirtualTimeMicroseconds"]/1000,4),
            "query_server_ms":item["query_duration_ms"],
            "read_rows":item["read_rows"],
            "read_bytes":item["read_bytes"],
            "selected_marks":ev.get("SelectedMarks"),
            "read_compressed_bytes":ev.get("ReadCompressedBytes"),
            "runqueue_ms":round(ev.get("OSCPUWaitMicroseconds",0)/1000,4),
            "query_memory_bytes":item["memory_usage"]
        })
    time_cpu=cpu1-cpu0
    sched_cpu_ns=snap1["run_ns"]-snap0["run_ns"]
    if time_cpu<=0:
        raise AssertionError(("Zero/negative process CPU",align,kind,case,n))
    record["summary"]={
        "p50_ms":pct([x["client_ms"] for x in measured],.5),
        "p95_ms":pct([x["client_ms"] for x in measured],.95),
        "p99_ms":pct([x["client_ms"] for x in measured],.99),
        "qps":round(len(measured)/sum(durations),4),
        "cpu_queryfinish_mean_ms":round(statistics.mean(x["cpu_ms"] for x in measured),4),
        "cpu_queryfinish_p50_ms":pct([x["cpu_ms"] for x in measured],.5),
        "process_cpu_ms_per_request":round(1000*time_cpu/len(measured),4),
        "schedstat_cpu_ms_per_request":round(sched_cpu_ns/1e6/len(measured),4),
        "schedstat_cpu_truncated":sched_cpu_ns<0,
        "cpu_runqueue_mean_ms":round(statistics.mean(x["runqueue_ms"] for x in measured),3),
        "read_rows_p50":pct([x["read_rows"] for x in measured],.5),
        "selected_marks_p50":pct([x["selected_marks"] or 0 for x in measured],.5),
        "read_compressed_bytes_p50":pct([x["read_compressed_bytes"] or 0 for x in measured],.5),
        "query_memory_max_bytes":max(x["query_memory_bytes"] for x in measured),
        "process_rss_after_bytes":record["after_rss"].get("VmRSS"),
        "process_lifetime_hwm_after_bytes":record["after_rss"].get("VmHWM"),
        "measured_requests":len(measured),
        "measured_waves":MEASURED_WAVES
    }
    print("R18C_PHASE",json.dumps({
        "alignment":align,"kind":kind,"case":case,"clients":n,
        "panel":panel_index,"phase":order,"summary":record["summary"]
    },separators=(",",":")),flush=True)
    return record


def summarize(phases):
    b=[p["summary"] for p in phases if p["kind"]=="alp"]
    z=[p["summary"] for p in phases if p["kind"]=="zstd1"]
    if len(b)!=2 or len(z)!=2:
        raise AssertionError("Incomplete ALP/ZSTD/ZSTD/ALP inner panel")
    mean=lambda entries,key:statistics.mean(x[key] for x in entries)
    out={}
    keys=("p50_ms","p95_ms","p99_ms","qps","cpu_queryfinish_mean_ms",
          "process_cpu_ms_per_request","cpu_runqueue_mean_ms",
          "read_rows_p50","selected_marks_p50","read_compressed_bytes_p50",
          "query_memory_max_bytes")
    for key in keys:
        a=mean(b,key);c=mean(z,key)
        drift_b=abs(b[0][key]-b[1][key])/max(abs(a),1e-8)
        drift_c=abs(z[0][key]-z[1][key])/max(abs(c),1e-8)
        out[key]={
            "alp":round(a,5),"zstd1":round(c,5),
            "zstd1_over_alp":round(c/a,6) if a else None,
            "alp_control_drift":round(drift_b,5),
            "zstd1_control_drift":round(drift_c,5)
        }
    out["valid"]=all(
        out[key][kind+"_control_drift"]<=.25
        for key in ("p50_ms","cpu_queryfinish_mean_ms","qps")
        for kind in KINDS
    )
    out["note"]="Read rows/marks may differ across aligned vs stagger, must be equal between codecs for each alignment; CPU runs separate from background process CPU"
    if (out["read_rows_p50"]["alp"]!=out["read_rows_p50"]["zstd1"] or
        out["selected_marks_p50"]["alp"]!=out["selected_marks_p50"]["zstd1"]):
        raise AssertionError(("CODEC changed selected rows/marks in same alignment",out))
    return out


def main():
    p=argparse.ArgumentParser()
    for k in ("aligned-fixture","aligned-manifest","stagger-fixture",
              "stagger-manifest","pid-file","output"):
        p.add_argument("--"+k,required=True)
    p.add_argument("--cases",nargs="+",choices=CASES_TO_RUN,default=list(CASES_TO_RUN))
    p.add_argument("--clients",nargs="+",type=int,choices=CLIENTS,default=list(CLIENTS))
    p.add_argument("--binary")
    p.add_argument("--expected-binary-sha256",default=BINARY_SHA)
    args=p.parse_args()
    if args.binary and sha256(args.binary)!=args.expected_binary_sha256:
        raise AssertionError("Executable differs from explicit pinned SHA256")
    res={
        "question_id":"R18-timeseries-samples-value-codec-v1",
        "dependent_gate":"staggered 3h nodes and 1/4/8 concurrent native clients",
        "source_sha":SOURCE_SHA,"binary_sha256":args.expected_binary_sha256,
        "fixture_sha256":SHA,"settings":{"max_threads_per_query":2,"threads_per_runner":4,
         "codec_order":CODEC_ORDER,"outer_alignment_order":ALIGNMENT_ORDER,
         "warmup_waves":WARMUP_WAVES,"measured_waves":MEASURED_WAVES},
        "fixtures":{},"ingest":{},"optimize":{},"audits":{},
        "panels":{},"reduced":{},"errors":[],"passed":False
    }
    save(args.output,res)
    try:
        manifests={}
        expected_rows,expected_samples=expected_counts(3,400)
        inputs={
            "aligned":(args.aligned_fixture,args.aligned_manifest),
            "stagger":(args.stagger_fixture,args.stagger_manifest)
        }
        for alignment,(fixture,mfile) in inputs.items():
            m=json.loads(pathlib.Path(mfile).read_text())
            if (m["sha256"]!=SHA[alignment] or sha256(fixture)!=SHA[alignment]
                or m["series"]!=sum(expected_rows.values())
                or m["samples"]!=sum(expected_samples.values())
                or m["days"]!=3 or m["node_slots"]!=400 or m["base"]!=BASE):
                raise AssertionError(("Wrong source manifest or SHA",alignment,m))
            if alignment=="stagger" and (
                m.get("stagger_policy")!="node start = (slot * 317) % 10800 seconds"
                or m.get("bucket_rows_by_step",{}).get("3600")!=BUCKETS[alignment]):
                raise AssertionError(("Wrong stagger policy or bucket math",m))
            manifests[alignment]=m
            res["fixtures"][alignment]=m
        res["fixture_pair_audit"]=check_fixture_pair(
            args.aligned_fixture,args.stagger_fixture,
            manifests["aligned"]["series"],manifests["aligned"]["samples"]
        )
        pid=server_status_pid(args.pid_file)
        res["server_pid"]=pid
        # All four physical alternatives coexist in one server; after
        # OPTIMIZE and STOP MERGES no data mutation occurs during queries.
        for alignment,(fixture,_mfile) in inputs.items():
            base.TABLE_PREFIX=f"tslab_r18_{alignment}_"
            for kind in KINDS:
                ddl=base.create_table(kind)
                start=time.perf_counter()
                rows=streamed_insert(name(alignment,kind),fixture)
                ingest_time=round(time.perf_counter()-start,3)
                if rows["rows"]!=manifests[alignment]["series"]:
                    raise AssertionError(("Incomplete insert",alignment,kind,rows))
                opt=optimize_table(name(alignment,kind))
                if any(isinstance(v,dict) for v in opt.values()):
                    raise AssertionError(("Failed physical OPTIMIZE FINAL",alignment,kind,opt))
                res["ingest"][name(alignment,kind)]={"ddl":ddl,"in":rows,"wall_s":ingest_time}
                res["optimize"][name(alignment,kind)]=opt
                save(args.output,res)
        wait_no_merges()
        for alignment in SHA:
            base.TABLE_PREFIX=f"tslab_r18_{alignment}_"
            for kind in KINDS:
                res["audits"][name(alignment,kind)]=base.audit(
                    name(alignment,kind),manifests[alignment])
                if (int(res["audits"][name(alignment,kind)]["samples"]["bucket_rows"])
                    !=BUCKETS[alignment]):
                    raise AssertionError(("Wrong true bucket count",alignment,kind))
        if len({str(v["tags_ids"]["digest"]) for v in res["audits"].values()})!=1:
            raise AssertionError("Logical tags ID digest varies across timestamp and codec")
        res["physical_before"]={k:v["physical"] for k,v in res["audits"].items()}
        save(args.output,res)
        for case in args.cases:
            refs={}
            for alignment in SHA:
                refs[alignment]=oracle(alignment,case,manifests[alignment]["end"],"initial")
            for n in args.clients:
                panels=[]
                key=f"{case}__clients{n}"
                res["panels"][key]=panels
                for outer,alignment in enumerate(ALIGNMENT_ORDER):
                    ref,ticks,expected=refs[alignment]
                    output={"alignment":alignment,"outer_panel":outer,"case":case,
                            "clients":n,"phases":[]}
                    panels.append(output)
                    for order,kind in enumerate(CODEC_ORDER):
                        record=phase(alignment,kind,case,n,outer,order,pid,
                            ref,ticks,expected,manifests[alignment]["end"])
                        output["phases"].append(record)
                        save(args.output,res)
                    output["summary"]=summarize(output["phases"])
                    if not output["summary"]["valid"]:
                        res["errors"].append({
                            "invalid_panel_drift":key,"outer":outer,
                            "alignment":alignment,
                            "p50":output["summary"]["p50_ms"],
                            "cpu":output["summary"]["cpu_queryfinish_mean_ms"]
                        })
                    print("R18C_PANEL",json.dumps({
                        "case":case,"clients":n,"outer":outer,
                        "alignment":alignment,"p50":output["summary"]["p50_ms"],
                        "cpu":output["summary"]["cpu_queryfinish_mean_ms"],
                        "qps":output["summary"]["qps"],
                        "valid":output["summary"]["valid"]
                    },separators=(",",":")),flush=True)
                    save(args.output,res)
                interactions={}
                for alignment in SHA:
                    pair=[p["summary"] for p in panels if p["alignment"]==alignment]
                    if len(pair)!=2:
                        raise AssertionError(("Unbalanced outer factor",alignment,key))
                    for metric in ("p50_ms","cpu_queryfinish_mean_ms","qps"):
                        vals=[x[metric]["zstd1_over_alp"] for x in pair]
                        mean_ratio=statistics.mean(vals)
                        drift=abs(vals[0]-vals[1])/mean_ratio
                        interactions.setdefault(alignment,{})[metric]={
                            "mean_codec_ratio":round(mean_ratio,6),
                            "repeat_codec_effect_drift":round(drift,6),
                            "valid_repeat":drift<=.25
                        }
                for metric in ("p50_ms","cpu_queryfinish_mean_ms","qps"):
                    aa=interactions["aligned"][metric]["mean_codec_ratio"]
                    bb=interactions["stagger"][metric]["mean_codec_ratio"]
                    interactions.setdefault("cross_alignment_interaction",{})[metric]={
                        "aligned_codec_ratio":aa,"stagger_codec_ratio":bb,
                        "stagger_div_aligned_ratio":round(bb/aa,5)
                    }
                res["reduced"][key]=interactions
                if not all(
                    interactions[alignment][metric]["valid_repeat"]
                    for alignment in SHA
                    for metric in ("p50_ms","cpu_queryfinish_mean_ms","qps")
                ):
                    res["errors"].append({"invalid_repeat_effect_drift":key,
                                          "data":interactions})
                print("R18C_INTERACTION",json.dumps({
                    "case":case,"clients":n,"interactions":interactions
                },separators=(",",":")),flush=True)
                save(args.output,res)
        res["physical_after"]={k:physical(k) for k in res["audits"]}
        if res["physical_before"]!=res["physical_after"]:
            raise AssertionError("Physical parts changed during A/B/B/A")
        res["passed"]=not res["errors"]
    except Exception as e:
        res["errors"].append({
            "fatal":str(e)[:2000],
            "traceback":traceback.format_exc()[-3800:]
        })
        res["passed"]=False
    finally:
        save(args.output,res)
        condensed={
            "passed":res["passed"],"errors":res["errors"],
            "source_sha":SOURCE_SHA,"binary_sha256":args.expected_binary_sha256,
            "fixture_sha256":SHA,"fixture_pair_audit":res.get("fixture_pair_audit"),
            "storage":{k:v.get("physical",{}).get("samples")
                for k,v in res["audits"].items()},
            "interactions":res["reduced"],
            "physical_unchanged":res.get("physical_before")==res.get("physical_after")
                if res.get("physical_after") else None
        }
        save(str(pathlib.Path(args.output).with_suffix(".compact.json")),condensed)
        print("R18C_DONE",json.dumps(condensed,separators=(",",":")),flush=True)
    if not res["passed"]:
        raise SystemExit(1)


if __name__=="__main__":
    main()
