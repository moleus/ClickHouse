#!/usr/bin/env python3
"""R18: orthogonal ALP transform x ZSTD level on unchanged TimeSeries values.

Full 2x2 factorial: (ALP or no ALP) x (ZSTD level 1 or 3).
One pinned fixture, 4 explicit identical v11 TimeSeries inner schemas,
only the SAMPLES.value codec differs. Native full-output and SHA gates,
four separate paired ABBA tests per selector, CPU/latency and exact parts.
Run two input distributions on separate independent CI runners.
This corrects the previous R18 two-variable ALP,ZSTD3 vs ZSTD1 confound.
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

sys.path[:0]=[
    ".github/ts-lab-r18", ".github/ts-lab-r01", ".github/ts-lab-r03",
    ".github/ts-lab-perf", ".github/ts-lab-bench", ".github/ts-lab-cpu"
]
import bench_samples_value_codec as bench
from bench_granularity_v11 import sql, streamed_insert, optimize_table, wait_no_merges, inner
from bench_bucket_cxx_abca import post, normalize_any
from bench_native_multiclient_stock_20261103 import (
    physical, sha256, save, server_status_pid
)
from bench_k8s_mixed_native import CASES,DAY
from bench_jenkins_querypath import pct
from bench_cpu_knobs import capture_profiles
from bench_native_vm_concurrency import proc_cpu_seconds
from generate_k8s_churn_fixture import expected_counts,BASE

CODECS={
    "alp_z3":"ALP,ZSTD(3)",
    "alp_z1":"ALP,ZSTD(1)",
    "raw_z3":"ZSTD(3)",
    "raw_z1":"ZSTD(1)",
}
KINDS=tuple(CODECS)
FACTORS={
    "alp_at_z3":("alp_z3","raw_z3"),
    "alp_at_z1":("alp_z1","raw_z1"),
    "zstd_level_with_alp":("alp_z3","alp_z1"),
    "zstd_level_without_alp":("raw_z3","raw_z1")
}
CASES=("pod_namespace","node_broad","burst15")
SOURCE_SHA="52529f0c711566d696917678bf15e7068772fdde"
BINARY_SHA="f0a2fa1f814c58853f43423705cac10ab9df3bfe5887e27ebe9620ba7cb9660e"
SHA={
    "original":"ea9a8d43c9e58ed916f1bf43949556d8ed18169ddd14033c293af4cd54c9881d",
    "entropy":"4b81b190cbcff7af58682e8c023cebfad22e3bb98afb591d8b2d9928eade9d57"
}
RUN=os.getenv("GITHUB_RUN_ID","manual")
HOST="http://127.0.0.1:18191"
PREFIX="tslab_r18_factorial_"


def name(kind):
    return PREFIX+kind


def create(kind):
    n=name(kind)
    codec=CODECS[kind]
    q=(
        f"CREATE TABLE {n} ENGINE=TimeSeries "
        "SETTINGS recent_samples_ttl_seconds=0,"
        "samples_bucket_step_seconds=3600 "
        "SAMPLES INNER COLUMNS ("
        "id Tuple(UInt64,UUID),"
        "samples SimpleAggregateFunction(timeSeriesGroupArray,"
        "Array(Tuple(timestamp DateTime64(3), value Float64))),"
        "CODEC samples.timestamp (Delta,T64,ZSTD(3)),"
        f"CODEC samples.value ({codec})) "
        "TAGS INNER COLUMNS ("
        "id Tuple(UInt64,UUID) DEFAULT tuple(sipHash64(metric_name),"
        "reinterpretAsUUID(sipHash128(tags))),"
        "metric_name LowCardinality(String),"
        "tags Map(LowCardinality(String),String),"
        "INDEX tags_idx tags TYPE text(tokenizer = 'keyValuePairs') "
        "GRANULARITY 100000000) "
        "TIME RANGES INNER COLUMNS (id Tuple(UInt64,UUID))"
    )
    sql(q)
    ddl=sql("SHOW CREATE TABLE "+inner(n,"samples")+" FORMAT TSVRaw")
    normalized=re.sub(r"\s+","",ddl.replace(chr(96),"")).lower()
    wanted="codecsamples.value("+codec.replace(" ","").lower()+")"
    if (wanted not in normalized or
        "codecsamples.timestamp(delta(8),t64,zstd(3))" not in normalized):
        raise AssertionError(("Effective value/timestamp codec wrong",kind,ddl[:2800]))
    # Isolate the factor at physical schema level, not just logical CREATE.
    lines=[
        ("CREATE TABLE INNER" if line.startswith("CREATE TABLE ") else
         "    CODEC samples.value (FACTOR)" if
         "CODEC samples.value" in line.replace(chr(96),"") else line)
        for line in ddl.splitlines()
    ]
    return {"input_ddl":q,"actual_samples_ddl":ddl,
            "canonical_without_value_codec":"\n".join(lines)}


def fixture_gate(path,manifest_path,kind):
    m=json.loads(pathlib.Path(manifest_path).read_text())
    expected_rows,expected_points=expected_counts(3,400)
    if (m.get("sha256")!=SHA[kind] or sha256(path)!=SHA[kind]
        or m.get("series")!=sum(expected_rows.values())
        or m.get("samples")!=sum(expected_points.values())
        or m.get("days")!=3 or m.get("node_slots")!=400
        or m.get("base")!=BASE):
        raise AssertionError(("Incorrect source fixture SHA/shape",kind,m))
    if kind=="entropy" and (
        m.get("parent_fixture_sha256")!=SHA["original"] or
        m.get("value_pattern")!="entropy"
    ):
        raise AssertionError("Entropy must derive strictly from R01 source")
    return m


def write_and_optimize(fixture,manifest,pid,res):
    fingerprints=set()
    part_audits={}
    for kind in KINDS:
        schema=create(kind)
        res["schemas"][kind]=schema
        fingerprints.add(schema["canonical_without_value_codec"])
        start=time.perf_counter()
        cpu0=proc_cpu_seconds(pid)
        ingest=streamed_insert(name(kind),fixture)
        cpu1=proc_cpu_seconds(pid)
        if ingest["rows"]!=manifest["series"]:
            raise AssertionError(("Incomplete sample ingest",kind,ingest))
        res["writes"][kind]={
            "ingest":ingest,"ingest_wall_s":round(time.perf_counter()-start,3),
            "unpaired_process_cpu_s":round(cpu1-cpu0,3)
        }
        t0=time.perf_counter()
        opt=optimize_table(name(kind))
        res["writes"][kind]["optimize_wall_s"]=round(time.perf_counter()-t0,3)
        res["writes"][kind]["optimize"]=opt
        if any(isinstance(z,dict) for z in opt.values()):
            raise AssertionError(("OPTIMIZE not successful",kind,opt))
        save(res["path"],res)
    if len(fingerprints)!=1:
        raise AssertionError(("DDL outside SAMPLES.value differs between factors",[
            (k,v["canonical_without_value_codec"][:2800]) for k,v in res["schemas"].items()
        ]))
    wait_no_merges()
    all_digests=set()
    for kind in KINDS:
        n=name(kind)
        part_audits[kind]=bench.audit(n,manifest)
        all_digests.add(str(part_audits[kind]["tags_ids"]["digest"]))
    if len(all_digests)!=1:
        raise AssertionError(("Different id/labels hash across codecs",all_digests))
    for layer in ("tags","timeranges","samples"):
        physical_rows={x["physical"][layer]["parts"]["rows"]
            for x in part_audits.values()}
        marks={x["physical"][layer]["parts"]["marks"]
            for x in part_audits.values()}
        if len(physical_rows)!=1 or len(marks)!=1:
            raise AssertionError(("Codec changed row or mark counts",layer,
                                  physical_rows,marks))
    res["audits"]=part_audits
    res["physical_before"]={k:v["physical"] for k,v in part_audits.items()}
    size={
        k: {
            "samples_bytes":int(v["physical"]["samples"]["parts"]["bytes"]),
            "total_inner_bytes":sum(int(v["physical"][part]["parts"]["bytes"])
                                    for part in ("tags","timeranges","samples")),
        }
        for k,v in part_audits.items()
    }
    res["storage"]=size
    storage_ratio=lambda a,b:{ "baseline_bytes":size[a]["samples_bytes"],
        "candidate_bytes":size[b]["samples_bytes"],
        "candidate_over_baseline":round(
            size[b]["samples_bytes"]/size[a]["samples_bytes"],6)
    }
    res["storage_factorial"]={
        fac:storage_ratio(*pair) for fac,pair in FACTORS.items()
    }
    # In a multiplicative 2x2, interaction measures whether removing ALP
    # changes its storage effect depending on the outer ZSTD level.
    r1=res["storage_factorial"]["alp_at_z1"]["candidate_over_baseline"]
    r3=res["storage_factorial"]["alp_at_z3"]["candidate_over_baseline"]
    res["storage_factorial"]["interaction_ratio_alp_at_z1_over_alp_at_z3"]=round(
        r1/r3,6
    )
    print("R18F_STORAGE",json.dumps({
        "storage":size,"factors":res["storage_factorial"]
    },separators=(",",":")),flush=True)


def compare_native(fixture_kind,manifest,pid,res,cases):
    bench.TABLE_PREFIX=PREFIX
    bench.WARMUPS=2
    bench.MEASURED=16
    bench.RUN=RUN+"_"+fixture_kind
    end=manifest["end"]
    timestamps={end-i*DAY for i in range(3)}
    for case in cases:
        _ms,body=post(
            HOST,bench.query(case,end,"alp_z3"),
            f"r18f_oracle_{RUN}_{fixture_kind}_{case}"
        )
        ref=normalize_any(body)
        expected=3*CASES[case][3]
        if len(ref)!=expected or {t for _,t in ref}!=timestamps:
            raise AssertionError(("Invalid original native PromQL oracle",case,len(ref),expected))
        res["read"][case]={}
        for factor,(a,b) in FACTORS.items():
            phases=[]
            rec={"baseline":a,"candidate":b,"phases":phases}
            res["read"][case][factor]=rec
            for order,kind in enumerate((a,b,b,a)):
                ph=bench.run_phase(
                    case,kind,factor+"_"+str(order),end,ref,
                    expected,timestamps,pid
                )
                phases.append(ph)
                save(res["path"],res)
            summary=bench.compare(phases,a,b)
            rec["summary"]=summary
            if not summary["valid"]:
                res["errors"].append({"case":case,"factor":factor,
                                      "invalid_abba_drift":summary})
            try:
                qids=[phases[i]["representative_qid"] for i in (0,1)]
                rec["representative_trace_and_processors"]=capture_profiles(qids)
            except Exception as exc:
                rec["profile_error"]=str(exc)[:1500]
            print("R18F_FACTOR",json.dumps({
                "pattern":fixture_kind,"case":case,"factor":factor,
                "comparison":summary
            },separators=(",",":")),flush=True)
            save(res["path"],res)


def main():
    p=argparse.ArgumentParser()
    p.add_argument("--fixture",required=True)
    p.add_argument("--manifest",required=True)
    p.add_argument("--pattern",choices=tuple(SHA),required=True)
    p.add_argument("--pid-file",required=True)
    p.add_argument("--output",required=True)
    p.add_argument("--cases",choices=CASES,nargs="+",default=list(CASES))
    args=p.parse_args()
    global PREFIX
    PREFIX+=args.pattern+"_"
    res={
        "question_id":"R18-timeseries-samples-value-codec-v1",
        "dependent_gate":"ALP-transform by ZSTD-level 2x2 factorial",
        "run_id":RUN,"pattern":args.pattern,
        "source_sha":SOURCE_SHA,"binary_sha256":BINARY_SHA,
        "value_codec_factors":CODECS,"pairs":FACTORS,
        "schemas":{},"writes":{},"audits":{},"read":{},
        "storage":{},"errors":[],"passed":False,
        "path":args.output
    }
    save(args.output,res)
    try:
        manifest=fixture_gate(args.fixture,args.manifest,args.pattern)
        res["manifest"]=manifest
        pid=server_status_pid(args.pid_file)
        res["pid"]=pid
        write_and_optimize(args.fixture,manifest,pid,res)
        save(args.output,res)
        compare_native(args.pattern,manifest,pid,res,args.cases)
        res["physical_after"]={k:physical(name(k)) for k in KINDS}
        if res["physical_after"]!=res["physical_before"]:
            raise AssertionError("Physical parts mutated during paired native reads")
        res["passed"]=not res["errors"]
    except Exception as e:
        res["errors"].append({
            "fatal":str(e)[:2500],
            "traceback":traceback.format_exc()[-5000:]
        })
        res["passed"]=False
    finally:
        save(args.output,res)
        compact={
            "passed":res["passed"],"pattern":args.pattern,
            "source_sha":SOURCE_SHA,"fixture_sha256":SHA[args.pattern],
            "binary_sha256":BINARY_SHA,"errors":res["errors"],
            "storage":res.get("storage"),
            "storage_factorial":res.get("storage_factorial"),
            "sample_points":res.get("manifest",{}).get("samples"),
            "read":{case:{f:pair.get("summary") for f,pair in factors.items()}
                    for case,factors in res["read"].items()},
            "physical_unchanged":res.get("physical_before")==res.get("physical_after")
                if "physical_after" in res else None
        }
        save(str(pathlib.Path(args.output).with_suffix(".compact.json")),compact)
        print("R18F_DONE",json.dumps(compact,separators=(",",":")),flush=True)
    if not res["passed"]:
        raise SystemExit(1)


if __name__=="__main__":
    main()
