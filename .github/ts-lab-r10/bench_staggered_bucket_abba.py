#!/usr/bin/env python3
"""R10: native TimeSeries fixed 3h lifetime, staggered-start bucket sensitivity.

A synthetic fixture with unchanged 28,896 IDs and 2,488,320 original
samples now offsets each node-slot lifecycle by (slot*317)%10800 seconds.
Compare exactly two RAW_UUID TimeSeries storage schemas:
SAMPLES bucket 3600 versus 10800 seconds, same unmodified pinned stock
LLVM22 O1 binary, same tag hash, same data, full native PromQL parity.
A/B/B/A matched on same runner, 2 warmups +6 measured query trials.
No new C++ compilation, no production/master writes.
"""
import argparse
import json
import os
import pathlib
import statistics
import sys
import traceback

sys.path[:0]=[
    ".github/ts-lab-r10",".github/ts-lab-r01",".github/ts-lab-perf",
    ".github/ts-lab-bench",".github/ts-lab-cpu"
]
from bench_granularity_v11 import streamed_insert,optimize_table,wait_no_merges
from bench_bucket_cxx_abca import post,normalize_any
from bench_native_multiclient_stock_20261103 import physical,sha256,save,server_status_pid
from bench_k8s_mixed_native import CASES,query,run_phase,profile_rows,DAY,BINARY_SHA,SOURCE_SHA
from bench_bucket_id_factorial import physical_audit
from generate_k8s_churn_fixture import BASE, LIFE, expected_counts

HOST="http://127.0.0.1:18191"
STEPS=(3600,10800)
TABLES={3600:"tslab_r10_stagger_raw_b3600",10800:"tslab_r10_stagger_raw_b10800"}
EXPECTED_SHIFT_SHA="ae85496efcab3417634ac812cbd50a40c4eea297a397b50792efd024838e1b9a"


def create_table(step):
    name=TABLES[step]
    from bench_granularity_v11 import sql
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


def compare(case,days,end,pid,reference):
    phases=[]
    for order,step in enumerate((3600,10800,10800,3600)):
        t=TABLES[step]
        result=run_phase(
            case,days,end,t,order,pid,reference,
            variant_label=f"r10stagger_{step}_{case}"
        )
        phases.append(result)
    before=[x["summary"] for x in phases if x["variant"].startswith("r10stagger_3600_")]
    after=[x["summary"] for x in phases if x["variant"].startswith("r10stagger_10800_")]
    if len(before)!=2 or len(after)!=2:
        raise AssertionError("Incomplete phase groups")
    avg=lambda rows,key:statistics.mean(v[key] for v in rows)
    base=avg(before,"p50_ms")
    cand=avg(after,"p50_ms")
    drift=abs(before[0]["p50_ms"]-before[1]["p50_ms"])/base
    summary={
        "baseline_step":3600,"candidate_step":10800,
        "baseline_p50_ms":round(base,3),"candidate_p50_ms":round(cand,3),
        "candidate_ratio":round(cand/base,4),
        "baseline_p95_ms":round(avg(before,"p95_ms"),3),
        "candidate_p95_ms":round(avg(after,"p95_ms"),3),
        "baseline_cpu_ms":round(avg(before,"cpu_ms"),3),
        "candidate_cpu_ms":round(avg(after,"cpu_ms"),3),
        "baseline_read_rows":round(avg(before,"median_read_rows"),2),
        "candidate_read_rows":round(avg(after,"median_read_rows"),2),
        "baseline_read_compressed_bytes":round(avg(before,"median_compressed_bytes"),2),
        "candidate_read_compressed_bytes":round(avg(after,"median_compressed_bytes"),2),
        "baseline_peak_query_memory_bytes":max(v["query_memory_max_bytes"] for v in before),
        "candidate_peak_query_memory_bytes":max(v["query_memory_max_bytes"] for v in after),
        "control_p50_drift":round(drift,5),
        "valid_control":drift<=.25
    }
    print("R10_STAGGER_COMPARE",json.dumps(
        {"case":case,"summary":summary},separators=(",",":")),flush=True)
    return {
        "summary":summary,"phases":phases,
        "representative_profiles":profile_rows([phases[0]["profile_qid"],phases[1]["profile_qid"]])
    }


def main():
    p=argparse.ArgumentParser()
    p.add_argument("--fixture",required=True)
    p.add_argument("--manifest",required=True)
    p.add_argument("--pid-file",required=True)
    p.add_argument("--binary",required=True)
    p.add_argument("--output",required=True)
    p.add_argument("--cases",nargs="+",choices=CASES,default=list(CASES))
    args=p.parse_args()
    result={
        "question_id":"R10-bucket-id-factorial-v1",
        "dependent_gate":"start-time phase alignment with 3h node lifetime",
        "source_sha":SOURCE_SHA,"binary_sha256":BINARY_SHA,
        "fixture_sha256":EXPECTED_SHIFT_SHA,
        "variants":TABLES,"comparisons":{},"errors":[]
    }
    save(args.output,result)
    try:
        manifest=json.loads(pathlib.Path(args.manifest).read_text())
        expected_series,expected_samples=expected_counts(3,400)
        counts={"3600":113760,"10800":57120,"900":368544}
        if (sha256(args.fixture)!=EXPECTED_SHIFT_SHA or
            manifest.get("sha256")!=EXPECTED_SHIFT_SHA or
            sha256(args.binary)!=BINARY_SHA or
            manifest.get("stagger_policy")!="node start = (slot * 317) % 10800 seconds" or
            manifest.get("bucket_rows_by_step")!=counts or
            manifest.get("series")!=sum(expected_series.values()) or
            manifest.get("samples")!=sum(expected_samples.values()) or
            manifest.get("days")!=3 or manifest.get("node_slots")!=400 or
            manifest.get("base")!=BASE):
            raise AssertionError("Stagger source hash/clock/distribution mismatch")
        result["manifest"]=manifest
        result["server_pid"]=server_status_pid(args.pid_file)
        result["ingest"]={}
        result["optimize"]={}
        for step in STEPS:
            name=TABLES[step]
            create_table(step)
            inserted=streamed_insert(name,args.fixture)
            result["ingest"][name]=inserted
            if inserted["rows"]!=manifest["series"]:
                raise AssertionError(("Incomplete input rows",name,inserted))
            result["optimize"][name]=optimize_table(name)
            if any(isinstance(x,dict) for x in result["optimize"][name].values()):
                raise AssertionError(("OPTIMIZE failed",name,result["optimize"][name]))
            save(args.output,result)
        wait_no_merges()
        result["physical_before"]={}
        result["audit"]={}
        for step in STEPS:
            name=TABLES[step]
            report=physical_audit(
                name,manifest["series"],manifest["samples"],
                manifest["bucket_rows_by_step"][str(step)],"raw")
            result["physical_before"][name]=report["physical"]
            result["audit"][name]=report
        if len({v["identity"]["digest"] for v in result["audit"].values()})!=1:
            raise AssertionError("Logical IDs change with bucket step")
        save(args.output,result)
        days=int(manifest["days"])
        end=int(manifest["end"])
        expected_ticks={end-2*DAY,end-DAY,end}
        for case in args.cases:
            _,raw=post(
                HOST,
                query(TABLES[3600],case,end-2*DAY,end),
                f"r10stagger_oracle_{os.getenv('GITHUB_RUN_ID','local')}_{case}"
            )
            ref=normalize_any(raw)
            expected_points=days*CASES[case][3]
            if len(ref)!=expected_points or {t for _,t in ref}!=expected_ticks:
                raise AssertionError(("Oracle nonempty grid",case,len(ref),expected_points))
            comparison=compare(case,days,end,result["server_pid"],ref)
            result["comparisons"][case]=comparison
            if not comparison["summary"]["valid_control"]:
                result["errors"].append({
                    "case":case,"invalid_control":comparison["summary"]["control_p50_drift"]
                })
            save(args.output,result)
        result["physical_after"]={name:physical(name) for name in TABLES.values()}
        if result["physical_after"]!=result["physical_before"]:
            raise AssertionError("Physical parts changed during read-only control phases")
        result["passed"]=not result["errors"]
    except Exception as e:
        result["errors"].append({"fatal":str(e)[:2500],"traceback":traceback.format_exc()[-4500:]})
        result["passed"]=False
    finally:
        save(args.output,result)
        compact={
            "passed":result["passed"],"errors":result["errors"],
            "fixture":result.get("manifest"),"audit":result.get("audit"),
            "comparisons":{k:v["summary"] for k,v in result["comparisons"].items()}
        }
        save(str(pathlib.Path(args.output).with_suffix(".compact.json")),compact)
        print("R10_STAGGER_DONE",json.dumps({
            "passed":compact["passed"],"errors":compact["errors"],
            "comparisons":compact["comparisons"],
            "physical_buckets":{k:v["samples"]["bucket_rows"]
                for k,v in compact.get("audit",{}).items()}
        },separators=(",",":")),flush=True)
    if not result["passed"]:raise SystemExit(1)


if __name__=="__main__":
    main()
