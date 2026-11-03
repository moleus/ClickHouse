#!/usr/bin/env python3
"""R10 matched 2x2 native PromQL TimeSeries bucket vs node-start alignment.

Same stock O1 binary and the *two* deterministic 400-slot 3d fixtures:
aligned (original) versus staggered node-starts (same tags/values, shifted
timestamps). Each fixture has two RAW UUID TimeSeries tables, bucket
3600/10800. Full native parity is checked *within* each fixture, never
against a different timestamp distribution.

Four fresh ClickHouse server processes in outer order
aligned/stagger/stagger/aligned. Within each fresh process and each of
four selectors run bucket 3600/10800/10800/3600 ABBA; 2 warmups +6
measured queries per phase, exact labels/time/values, CPU and RSS.
Compare within-fixture candidate/base ratios; also report panel-level
control drift and repeated-panel stability. No C++ rebuild, no production.
"""
import argparse
import json
import os
import pathlib
import statistics
import subprocess
import sys
import traceback

sys.path[:0]=[
    ".github/ts-lab-r10",".github/ts-lab-r01",
    ".github/ts-lab-perf",".github/ts-lab-bench",".github/ts-lab-cpu"
]
from bench_granularity_v11 import sql,inner,streamed_insert,optimize_table,wait_no_merges
from bench_bucket_cxx_abca import post,normalize_any
from bench_native_multiclient_stock_20261103 import physical,sha256,save,server_status_pid
from bench_k8s_mixed_native import CASES,query,run_phase,DAY,BINARY_SHA,SOURCE_SHA
from bench_bucket_id_factorial import physical_audit
from generate_k8s_churn_fixture import BASE,LIFE,expected_counts

SHA={"aligned":"ea9a8d43c9e58ed916f1bf43949556d8ed18169ddd14033c293af4cd54c9881d",
     "stagger":"ae85496efcab3417634ac812cbd50a40c4eea297a397b50792efd024838e1b9a"}
EXPECTED_BUCKETS={"aligned":{3600:86688,10800:28896},
                  "stagger":{3600:113760,10800:57120}}
STEP_ORDER=(3600,10800,10800,3600)
OUTER=("aligned","stagger","stagger","aligned")
CASES_DEFAULT=("pod_namespace","burst15","pod_broad","node_broad")
HOST="http://127.0.0.1:18191"
RUN=os.getenv("GITHUB_RUN_ID","local")


def table(variant,step):
    return f"tslab_r10_factor_{variant}_b{step}"


def ddl(name,step):
    sql(
        f"CREATE TABLE {name} ENGINE=TimeSeries "
        f"SETTINGS recent_samples_ttl_seconds=0,samples_bucket_step_seconds={step} "
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


def get_reference(name,case,end,tag):
    begin=end-2*DAY
    _,body=post(
        HOST,query(name,case,begin,end),
        f"r10interaction_oracle_{RUN}_{tag}_{case}"
    )
    rows=normalize_any(body)
    expected=3*CASES[case][3]
    ticks={begin,begin+DAY,end}
    if len(rows)!=expected or {t for _,t in rows}!=ticks:
        raise AssertionError(("Oracle output cardinality/time",name,case,len(rows),expected))
    return rows


def run_inner(case,variant,outer_i,pid,end,reference,output,record):
    phases=[]
    for order,step in enumerate(STEP_ORDER):
        name=table(variant,step)
        phase=run_phase(
            case,3,end,name,order,pid,reference,
            variant_label=f"interaction_{variant}_panel{outer_i}_b{step}"
        )
        phases.append(phase)
        record["phases"]=phases
        save(output,record)
    a=[v["summary"] for v in phases if "_b3600" in v["variant"]]
    b=[v["summary"] for v in phases if "_b10800" in v["variant"]]
    if len(a)!=2 or len(b)!=2:
        raise AssertionError(("Incomplete nested ABBA",variant,case))
    avg=lambda values,key:statistics.mean(x[key] for x in values)
    b50=avg(a,"p50_ms")
    c50=avg(b,"p50_ms")
    drift=abs(a[0]["p50_ms"]-a[1]["p50_ms"])/b50
    cpu_b=avg(a,"cpu_ms");cpu_c=avg(b,"cpu_ms")
    summary={
        "variant":variant,"case":case,"panel_index":outer_i,
        "baseline_p50_ms":round(b50,3),
        "candidate_p50_ms":round(c50,3),
        "candidate_p50_div_baseline":round(c50/b50,5),
        "baseline_p95_ms":round(avg(a,"p95_ms"),3),
        "candidate_p95_ms":round(avg(b,"p95_ms"),3),
        "baseline_cpu_ms":round(cpu_b,3),
        "candidate_cpu_ms":round(cpu_c,3),
        "candidate_cpu_div_baseline":round(cpu_c/cpu_b,5),
        "baseline_read_rows":round(avg(a,"median_read_rows"),3),
        "candidate_read_rows":round(avg(b,"median_read_rows"),3),
        "baseline_read_bytes":round(avg(a,"median_read_bytes"),3),
        "candidate_read_bytes":round(avg(b,"median_read_bytes"),3),
        "baseline_read_compressed_bytes":round(avg(a,"median_compressed_bytes"),3),
        "candidate_read_compressed_bytes":round(avg(b,"median_compressed_bytes"),3),
        "baseline_max_query_memory_bytes":max(q["query_memory_max_bytes"] for q in a),
        "candidate_max_query_memory_bytes":max(q["query_memory_max_bytes"] for q in b),
        "baseline_control_drift":round(drift,5),
        "valid_control":drift<=.25
    }
    print("R10_INTERACTION_INNER",json.dumps(summary,separators=(",",":")),flush=True)
    record["summary"]=summary
    save(output,record)
    return summary


def summarize_repeated(panels):
    result={}
    for case in CASES_DEFAULT:
        variants={}
        for name in ("aligned","stagger"):
            rows=[p["cases"][case]["summary"] for p in panels if p["variant"]==name]
            if len(rows)!=2:raise AssertionError(("Missing repeated fixture",case,name))
            effect=statistics.mean(r["candidate_p50_div_baseline"] for r in rows)
            effect_cpu=statistics.mean(r["candidate_cpu_div_baseline"] for r in rows)
            drift=abs(rows[0]["candidate_p50_div_baseline"]-rows[1]["candidate_p50_div_baseline"])/effect
            variants[name]={
                "bucket_ratio_mean":round(effect,5),
                "cpu_ratio_mean":round(effect_cpu,5),
                "bucket_effect_repeat_drift":round(drift,5),
                "valid_repeat_drift":drift<=.25,
                "panels":[r["panel_index"] for r in rows],
            }
        result[case]={
            "aligned":variants["aligned"],
            "stagger":variants["stagger"],
            "interaction_ratio_stagger_div_aligned":round(
                variants["stagger"]["bucket_ratio_mean"]/
                variants["aligned"]["bucket_ratio_mean"],5
            )
        }
        print("R10_INTERACTION_COMPARISON",json.dumps({
            "case":case,**result[case]
        },separators=(",",":")),flush=True)
    return result


def main():
    parser=argparse.ArgumentParser()
    for k in ("binary","restart-script","pid-file","aligned-fixture",
              "stagger-fixture","aligned-manifest","stagger-manifest","output"):
        parser.add_argument("--"+k,required=True)
    args=parser.parse_args()
    report={
        "question_id":"R10-bucket-id-factorial-v1",
        "dependency":"aligned versus staggered start 2x2 interaction",
        "source_sha":SOURCE_SHA,"binary_sha256":BINARY_SHA,
        "fixed_sha256":SHA,"panels":[],"errors":[]
    }
    save(args.output,report)
    try:
        if sha256(args.binary)!=BINARY_SHA:
            raise AssertionError("Unverified stock O1 binary")
        files={"aligned":args.aligned_fixture,"stagger":args.stagger_fixture}
        mpaths={"aligned":args.aligned_manifest,"stagger":args.stagger_manifest}
        manifest={v:json.loads(pathlib.Path(mpaths[v]).read_text()) for v in SHA}
        rows,points=expected_counts(3,400)
        for variant in SHA:
            m=manifest[variant]
            if (sha256(files[variant])!=SHA[variant] or
                m["sha256"]!=SHA[variant] or
                m["series"]!=sum(rows.values()) or
                m["samples"]!=sum(points.values()) or
                m["node_slots"]!=400 or m["days"]!=3 or m["base"]!=BASE):
                raise AssertionError(("Bad fixture hash/count",variant,m))
            if variant=="stagger" and {
                int(k):v for k,v in m.get("bucket_rows_by_step",{}).items()
            }!={900:368544,3600:113760,10800:57120}:
                raise AssertionError("Bad predicted staggered bucket row counts")
        report["manifest"]=manifest
        for variant in SHA:
            for step in (3600,10800):
                name=table(variant,step)
                ddl(name,step)
                inserted=streamed_insert(name,files[variant])
                optimized=optimize_table(name)
                if (inserted["rows"]!=28896 or
                    any(isinstance(x,dict) for x in optimized.values())):
                    raise AssertionError(("Import/merge failed",name,inserted,optimized))
                report.setdefault("ingest",{})[name]=inserted
                report.setdefault("optimize",{})[name]=optimized
                save(args.output,report)
        wait_no_merges()
        report["audit"]={}
        report["physical_before"]={}
        for variant in SHA:
            for step in (3600,10800):
                name=table(variant,step)
                item=physical_audit(name,28896,2488320,
                                    EXPECTED_BUCKETS[variant][step],"raw")
                report["audit"][name]=item
                report["physical_before"][name]=item["physical"]
        if len({v["identity"]["digest"] for v in report["audit"].values()})!=1:
            raise AssertionError("Different TimeSeries tags/ID sets across fixtures")
        save(args.output,report)
        reference={}
        for variant in SHA:
            end=int(manifest[variant]["end"])
            for case in CASES_DEFAULT:
                reference[(variant,case)]=get_reference(
                    table(variant,3600),case,end,variant
                )
        for outer_i,variant in enumerate(OUTER):
            panel={"outer_index":outer_i,"variant":variant,"cases":{},
                   "status":"started"}
            report["panels"].append(panel)
            save(args.output,report)
            subprocess.run(
                [args.restart_script,args.binary,f"r10interaction_{outer_i}_{variant}"],
                timeout=260,check=True
            )
            pid=server_status_pid(args.pid_file)
            wait_no_merges()
            if {name:physical(name) for name in report["physical_before"]}!=report["physical_before"]:
                raise AssertionError(("Physical part mutation after restart",outer_i))
            panel["server_pid"]=pid
            for case in CASES_DEFAULT:
                rec={"phases":[],"status":"started"}
                panel["cases"][case]=rec
                run_inner(
                    case,variant,outer_i,pid,int(manifest[variant]["end"]),
                    reference[(variant,case)],args.output,rec
                )
                if not rec["summary"]["valid_control"]:
                    report["errors"].append({
                        "case":case,"variant":variant,"panel":outer_i,
                        "control_drift":rec["summary"]["baseline_control_drift"]
                    })
                rec["status"]="pass"
                save(args.output,report)
            panel["status"]="pass"
        report["interaction_summary"]=summarize_repeated(report["panels"])
        for case,rec in report["interaction_summary"].items():
            for variant in ("aligned","stagger"):
                if not rec[variant]["valid_repeat_drift"]:
                    report["errors"].append({
                        "case":case,"variant":variant,
                        "repeat_drift":rec[variant]["bucket_effect_repeat_drift"]
                    })
        report["physical_after"]={name:physical(name) for name in report["physical_before"]}
        if report["physical_after"]!=report["physical_before"]:
            raise AssertionError("Read-only test changed physical part layout")
        report["passed"]=not report["errors"]
    except Exception as e:
        report["errors"].append({
            "fatal":str(e)[:2400],"traceback":traceback.format_exc()[-4700:]
        })
        report["passed"]=False
    finally:
        save(args.output,report)
        compact={
            "passed":report["passed"],"errors":report["errors"],
            "fixture_sha256":SHA,
            "interaction_summary":report.get("interaction_summary"),
            "panel_summaries":[{
                "outer_index":p["outer_index"],"variant":p["variant"],
                "cases":{c:r.get("summary") for c,r in p["cases"].items()}
            }for p in report["panels"]],
            "physical":report.get("physical_before")
        }
        save(str(pathlib.Path(args.output).with_suffix(".compact.json")),compact)
        print("R10_INTERACTION_DONE",json.dumps(compact,separators=(",",":")),flush=True)
    if not report["passed"]:raise SystemExit(1)


if __name__=="__main__":
    main()
