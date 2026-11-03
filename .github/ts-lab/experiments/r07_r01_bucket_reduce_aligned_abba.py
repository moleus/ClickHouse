#!/usr/bin/env python3
"""R07: fused per-bucket (sum,count) vs exact stale-safe arrayJoin SQL and native.

Held-out aligned stock O1 original TAGS/SAMPLES with 2.488m R01 gauges.
Scientific gates: full ID, exact bit predicate, 18 native tick parities,
6-phase native/point/bucket/bucket/point/native ABBA, physical parts frozen.
No C++ change, sidecar, indexes, production or auto-scheduled workflows.
"""
import argparse
import json
import os
import pathlib
import statistics
import sys
import traceback

sys.path[:0]=[".github/ts-lab/experiments",".github/ts-lab-r01",
             ".github/ts-lab-perf",".github/ts-lab-bench",".github/ts-lab-cpu"]
import r07_r01_stale_filter_cost_abba as cost
base=cost.base
from bench_granularity_v11 import sql,inner,streamed_insert,optimize_table,wait_no_merges
from bench_bucket_cxx_abca import post,normalize_any,assert_same,pct
from bench_filtered_selector_v4_sql import sql_string
from bench_native_multiclient_stock_20261103 import (
    complete_logs,physical,sha256,save,RSSMonitor,server_status_pid,
)
from generate_k8s_churn_fixture import BASE,DAY,expected_counts

HOST="http://127.0.0.1:18191"
RUN=os.getenv("GITHUB_RUN_ID","local")
STALE_BITS=0x7ff0000000000002
ORDER=("native","point_sql","bucket_sql","bucket_sql","point_sql","native")
CASES=("pod_broad_1d","pod_broad_3d","node_broad_3d","burst15_1d")
WARMUPS=2
MEASURED=4
EVAL=BASE+3*DAY

def bucket_sql_query(case,end):
    _,_,group,_,days=base.CASES[case]
    start=end-days*DAY
    samples=inner(base.TABLE,"samples")
    tags=inner(base.TABLE,"tags")
    where=base.tags_condition(case)
    lo=base.quoted_time(start)
    hi=base.quoted_time(end)
    bucket_lo=base.quoted_time((start//3600)*3600)
    source=(
        "SELECT id,arrayFilter(p -> tupleElement(p,1)>"
        "toDateTime64("+sql_string(lo)+",3,'UTC') "
        "AND tupleElement(p,1)<=toDateTime64("+sql_string(hi)+",3,'UTC') "
        "AND reinterpretAsUInt64(tupleElement(p,2))!="+str(STALE_BITS)+
        ",samples) AS filtered "
        "FROM "+samples+" WHERE bucket>=toDateTime64("+sql_string(bucket_lo)+",3,'UTC') "
        "AND bucket<=toDateTime64("+sql_string(hi)+",3,'UTC') "
        "AND id IN (SELECT id FROM "+tags+" WHERE "+where+")"
    )
    per_id=(
        "SELECT id,sum(arraySum(arrayMap(p -> tupleElement(p,2),filtered))) "
        "/sum(length(filtered)) AS avg_value "
        "FROM ("+source+") GROUP BY id HAVING sum(length(filtered))>0"
    )
    return (
        "SELECT matched.grp AS grp,avg(per_id.avg_value) AS value "
        "FROM ("+per_id+") AS per_id "
        "INNER JOIN (SELECT DISTINCT id,tags["+sql_string(group)+"] AS grp "
        "FROM "+tags+" WHERE "+where+") AS matched ON per_id.id=matched.id "
        "GROUP BY grp ORDER BY grp "
        "SETTINGS max_threads=4,max_memory_usage=3500000000,"
        "log_queries=1,log_processors_profiles=1,"
        "query_profiler_cpu_time_period_ns=1000000 "
        "FORMAT JSONEachRow"
    )

def statement(case,variant,end):
    if variant=="native":return base.native_query(case,end)
    if variant=="point_sql":return cost.safe_query(case,end)
    if variant=="bucket_sql":return bucket_sql_query(case,end)
    raise AssertionError(("Unknown mode",variant))

def grid_parity():
    proofs={}
    for case in base.CASES:
        proofs[case]={}
        for end in (BASE+DAY,BASE+2*DAY,EVAL):
            want=base.oracle(case,end)
            q=statement(case,"bucket_sql",end)
            _,raw=post(HOST,q,f"r07bucketalignedgrid_{RUN}_{case}_{end}")
            got=base.normalize_sql(raw,case,end)
            proofs[case][str(end)]=assert_same(want,got,case,f"bucket_{end}")
    return proofs

def phase(case,variant,order,pid,expected):
    q=statement(case,variant,EVAL)
    prefix=f"r07bucketalignedperf_{RUN}_{case}_{order}_{variant}_"
    item={"variant":variant,"order":order,"trials":[]}
    with RSSMonitor(pid) as observer:
        for i in range(WARMUPS+MEASURED):
            qid=prefix+str(i)
            ms,raw=post(HOST,q,qid)
            got=normalize_any(raw) if variant=="native" else base.normalize_sql(raw,case,EVAL)
            assert_same(expected,got,case,f"{variant}_{order}_{i}")
            item["trials"].append({"qid":qid,"ms":ms,"warmup":i<WARMUPS})
    item["rss"]=observer.report()
    if item["rss"]["observer_overhead_fraction"]>.02:
        raise AssertionError(("RSS observer too expensive",item["rss"]))
    logs=complete_logs(prefix,{x["qid"] for x in item["trials"]})
    for trial in item["trials"]:
        row=logs[trial["qid"]]
        ev=row.get("ProfileEvents",{})
        trial.update({
            "cpu_ms":ev.get("OSCPUVirtualTimeMicroseconds",0)/1000,
            "marks":ev.get("SelectedMarks",0),
            "read_rows":row["read_rows"],"read_bytes":row["read_bytes"],
            "peak_memory_bytes":row["memory_usage"],
            "server_ms":row["query_duration_ms"],
        })
    measured=item["trials"][WARMUPS:]
    med=lambda key:statistics.median(x[key] for x in measured)
    item["summary"]={
        "p50_ms":pct([x["ms"] for x in measured],.5),
        "p95_ms":pct([x["ms"] for x in measured],.95),
        "cpu_ms":med("cpu_ms"),"marks":med("marks"),
        "read_rows":med("read_rows"),"read_bytes":med("read_bytes"),
        "peak_memory_bytes":max(x["peak_memory_bytes"] for x in measured),
        "observed_process_rss_bytes":item["rss"]["rss_observed_max_bytes"],
    }
    print("R07_BUCKET_ALIGNED_PHASE",json.dumps({"case":case,"variant":variant,
        "summary":item["summary"]},separators=(",",":")),flush=True)
    return item

def compare(case,pid,reference):
    phases=[phase(case,variant,i,pid,reference) for i,variant in enumerate(ORDER)]
    kinds=("native","point_sql","bucket_sql")
    groups={k:[p["summary"] for p in phases if p["variant"]==k] for k in kinds}
    mean=lambda subset,key:statistics.mean(x[key] for x in subset)
    summary={}
    drifts={}
    for k,subset in groups.items():
        drifts[k]=abs(subset[0]["p50_ms"]-subset[1]["p50_ms"])/mean(subset,"p50_ms")
        for key in ("p50_ms","p95_ms","cpu_ms","marks","read_rows","read_bytes","peak_memory_bytes"):
            summary[k+"_"+key]=mean(subset,key)
    summary["control_drifts"]=drifts
    summary["valid_control"]=all(x<=.25 for x in drifts.values())
    summary["native_over_bucket_wall_ratio"]=summary["native_p50_ms"]/summary["bucket_sql_p50_ms"]
    summary["point_over_bucket_wall_ratio"]=summary["point_sql_p50_ms"]/summary["bucket_sql_p50_ms"]
    summary["point_over_bucket_cpu_ratio"]=summary["point_sql_cpu_ms"]/summary["bucket_sql_cpu_ms"]
    profiles=base.profile_operators({
        "native":phases[0]["trials"][WARMUPS]["qid"],
        "point_sql":phases[1]["trials"][WARMUPS]["qid"],
        "bucket_sql":phases[2]["trials"][WARMUPS]["qid"],
    })
    return {"phases":phases,"summary":summary,"processor_profiles":profiles,
            "queries":{k:statement(case,k,EVAL) for k in kinds}}

def main():
    ap=argparse.ArgumentParser()
    ap.add_argument("--fixture",required=True)
    ap.add_argument("--manifest",required=True)
    ap.add_argument("--pid-file",required=True)
    ap.add_argument("--output",required=True)
    args=ap.parse_args()
    base.TABLE="tslab_r07_r01_bucket_fused_aligned"
    base.FIXTURE_SHA="ea9a8d43c9e58ed916f1bf43949556d8ed18169ddd14033c293af4cd54c9881d"
    report={"question_id":"R07","status":"STARTED","passed":False,
            "cases":{},"errors":[],"source_sha":base.SOURCE_SHA,
            "binary_sha256":base.BINARY_SHA,"fixture_sha256":base.FIXTURE_SHA,
            "stale_marker":hex(STALE_BITS)}
    save(args.output,report)
    try:
        manifest=json.loads(pathlib.Path(args.manifest).read_text())
        rows,points=expected_counts(3,400)
        if (manifest.get("sha256")!=base.FIXTURE_SHA or
            sha256(args.fixture)!=base.FIXTURE_SHA or
            manifest.get("series")!=28896 or manifest.get("samples")!=2488320 or
            manifest.get("rows_by_tier")!=rows or manifest.get("samples_by_tier")!=points or
            manifest.get("stagger_policy") is not None):
            raise AssertionError("Wrong pinned R01 synthetic fixture")
        report["manifest"]=manifest
        report["ddl"]=base.ddl()
        report["ingest"]=streamed_insert(base.TABLE,args.fixture)
        if report["ingest"]["rows"]!=28896:raise AssertionError("Missing series after insert")
        report["optimize"]=optimize_table(base.TABLE)
        if any(isinstance(v,dict) for v in report["optimize"].values()):
            raise AssertionError("Incomplete stable parts")
        wait_no_merges()
        report["physical_before"]=physical(base.TABLE)
        if int(report["physical_before"]["samples"]["parts"]["rows"])!=86688:
            raise AssertionError("Held-out aligned R01 must have 86,688 sample bucket rows")
        report["grid_parity"]=grid_parity()
        if sum(len(x) for x in report["grid_parity"].values())!=18:
            raise AssertionError("Missing independent 18 native-grid checks")
        save(args.output,report)
        pid=server_status_pid(args.pid_file)
        for case in CASES:
            report["cases"][case]=compare(case,pid,base.oracle(case,EVAL))
            save(args.output,report)
        report["physical_after"]=physical(base.TABLE)
        if report["physical_before"]!=report["physical_after"]:
            raise AssertionError("Unexpected physical parts drift")
        report["passed"]=all(x["summary"]["valid_control"] for x in report["cases"].values())
        report["status"]="PASS_FUSED_BUCKET_SQL_ABBA" if report["passed"] else "INVALID_AA_DRIFT"
    except Exception as exc:
        report["status"]="FAILED"
        report["errors"].append({"error":str(exc)[:2600],"trace":traceback.format_exc()[-5100:]})
    finally:
        save(args.output,report)
        compact={"passed":report["passed"],"status":report["status"],
                 "errors":report["errors"],
                 "grid_checks":sum(len(x) for x in report.get("grid_parity",{}).values()),
                 "cases":{k:v["summary"] for k,v in report["cases"].items()},
                 "physical_before":report.get("physical_before"),
                 "physical_after":report.get("physical_after")}
        save(str(pathlib.Path(args.output).with_suffix(".compact.json")),compact)
        print("R07_BUCKET_ALIGNED_ABBA_DONE",json.dumps(compact,separators=(",",":")),flush=True)
    if not report["passed"]:raise SystemExit(1)

if __name__=="__main__":
    main()
