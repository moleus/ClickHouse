#!/usr/bin/env python3
"""R07: stock O1 original-TAGS SQL vs native PromQL on frozen staggered R01.

Source-pinned synthetic 400-slot / 3-day 2,488,320-sample fixture.
The ONLY query-path difference is native PromQL vs special-purpose SQL
per-ID avg followed by group avg. No sidecar, C++ build or data mutation
during the ABBA. Result parity and A/A drift are mandatory.
"""
import argparse
import json
import os
import pathlib
import statistics
import sys
import time
import traceback

sys.path[:0]=[
    ".github/ts-lab-r01",".github/ts-lab-perf",
    ".github/ts-lab-bench",".github/ts-lab-cpu",
]
from generate_k8s_churn_fixture import BASE,DAY,expected_counts
from bench_granularity_v11 import sql,inner,streamed_insert,optimize_table,wait_no_merges
from bench_bucket_cxx_abca import post,normalize_any,assert_same,pct
from bench_filtered_selector_v4_sql import sql_string
from bench_native_multiclient_stock_20261103 import (
    complete_logs,physical,sha256,save,RSSMonitor,server_status_pid,
)

TABLE="tslab_r07_r01_raw"
RUN=os.getenv("GITHUB_RUN_ID","local")
HOST="http://127.0.0.1:18191"
SOURCE_SHA="52529f0c711566d696917678bf15e7068772fdde"
BINARY_SHA="f0a2fa1f814c58853f43423705cac10ab9df3bfe5887e27ebe9620ba7cb9660e"
FIXTURE_SHA="ae85496efcab3417634ac812cbd50a40c4eea297a397b50792efd024838e1b9a"
EVAL=BASE+3*DAY
CASES={
    "node_broad_1d":("tslab_node_cpu_ratio","","project",8,1),
    "pod_namespace_1d":("tslab_pod_cpu_ratio",'namespace="team-000"',"namespace",1,1),
    "pod_broad_1d":("tslab_pod_cpu_ratio","","namespace",16,1),
    "burst15_1d":("tslab_pod_cpu_ratio",'scrape="15s"',"namespace",4,1),
    "node_broad_3d":("tslab_node_cpu_ratio","","project",8,3),
    "pod_broad_3d":("tslab_pod_cpu_ratio","","namespace",16,3),
}
ORDER=("native","sql","sql","native")
WARMUPS=2
MEASURED=4

def quoted_time(epoch):
    import datetime as dt
    return dt.datetime.fromtimestamp(epoch,dt.timezone.utc).strftime("%Y-%m-%d %H:%M:%S.000")

def ddl():
    sql(
        f"CREATE TABLE {TABLE} ENGINE=TimeSeries "
        "SETTINGS version=11,recent_samples_ttl_seconds=0,"
        "samples_bucket_step_seconds=3600 "
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
    show=sql("SHOW CREATE TABLE "+inner(TABLE,"samples")+" FORMAT TSVRaw")
    compact="".join(show.split()).lower()
    if "orderby(id,bucket)" not in compact:
        raise AssertionError(("Wrong SAMPLES sorting",show))
    if "toyyyymm(" not in compact:
        raise AssertionError(("Expected R01 default UTC monthly partition",show))
    return show

def selector(case):
    metric,filter_text,_,_,_=CASES[case]
    return metric+("{"+filter_text+"}" if filter_text else "")

def tags_condition(case):
    metric,filter_text,_,_,_=CASES[case]
    x="metric_name="+sql_string(metric)
    if filter_text:
        key,val=filter_text.split("=",1)
        val=val.strip('"')
        x+=" AND tags["+sql_string(key)+"]="+sql_string(val)
    return x

def native_query(case,end):
    metric,_,group,_,days=CASES[case]
    exp=f"avg by({group})(avg_over_time({selector(case)}[{days}d]))"
    return (
        f"SELECT * FROM prometheusQueryRange({TABLE},"
        f"{sql_string(exp)},{end},{end},86400) "
        "SETTINGS max_threads=4,max_memory_usage=3500000000,"
        "log_queries=1,log_processors_profiles=1,"
        "query_profiler_cpu_time_period_ns=1000000 FORMAT JSONEachRow"
    )

def tags_sql_query(case,end):
    _,_,group,_,days=CASES[case]
    start=end-days*DAY
    where=tags_condition(case)
    samples=inner(TABLE,"samples")
    tags=inner(TABLE,"tags")
    bucket_lo=(start//3600)*3600
    # Native TimeSeries v11 SAMPLES contains buckets of Array(Tuple(timestamp,value)).
    # First filter entire buckets for IO, then use PromQL left-open (start,end].
    sample_by_id=(
        "SELECT id,avg(tupleElement(point,2)) AS avg_value "
        "FROM (SELECT id,arrayJoin(samples) AS point FROM "+samples+
        " WHERE bucket>=toDateTime64("+sql_string(quoted_time(bucket_lo))+",3,'UTC') "
        "AND bucket<=toDateTime64("+sql_string(quoted_time(end))+",3,'UTC') "
        "AND id IN (SELECT id FROM "+tags+" WHERE "+where+")) "
        "WHERE tupleElement(point,1)>toDateTime64("+sql_string(quoted_time(start))+",3,'UTC') "
        "AND tupleElement(point,1)<=toDateTime64("+sql_string(quoted_time(end))+",3,'UTC') "
        "GROUP BY id"
    )
    sql_text=(
        "SELECT matched.grp AS grp,avg(value_by_id.avg_value) AS value "
        "FROM ("+sample_by_id+") AS value_by_id "
        "INNER JOIN (SELECT DISTINCT id,tags["+sql_string(group)+"] AS grp "
        "FROM "+tags+" WHERE "+where+") AS matched "
        "ON value_by_id.id=matched.id "
        "GROUP BY grp ORDER BY grp "
        "SETTINGS max_threads=4,max_memory_usage=3500000000,"
        "log_queries=1,log_processors_profiles=1,"
        "query_profiler_cpu_time_period_ns=1000000 "
        "FORMAT JSONEachRow"
    )
    return sql_text

def normalize_sql(raw,case,end):
    _,_,group,_,_=CASES[case]
    output={}
    for line in raw.splitlines():
        if not line.strip():continue
        row=json.loads(line)
        k=(((group,str(row["grp"])),),end)
        if k in output:raise AssertionError(("Duplicate SQL group",k))
        output[k]=float(row["value"])
    return output

def oracle(case,end):
    query=native_query(case,end)
    _,raw=post(HOST,query,f"r07r01_oracle_{RUN}_{case}_{end}")
    found=normalize_any(raw)
    want=CASES[case][3]
    if len(found)!=want or {t for _labels,t in found}!={end}:
        raise AssertionError(("Invalid native output oracle",case,end,len(found),want))
    return found

def parity_check_all_grid():
    """Three fixed evaluation timestamps and six cases; not a performance test."""
    result={}
    for case in CASES:
        result[case]={}
        for end in (BASE+DAY,BASE+2*DAY,EVAL):
            want=oracle(case,end)
            text=tags_sql_query(case,end)
            qid=f"r07r01_grid_{RUN}_{case}_{end}"
            _,raw=post(HOST,text,qid)
            got=normalize_sql(raw,case,end)
            check=assert_same(want,got,case,f"grid{end}")
            result[case][str(end)]={"points":len(want),"parity":check,
                                    "sql":text if end==EVAL else None}
            print("R07_R01_GRID",json.dumps({"case":case,"end":end,"points":len(want),"parity":check},
                                            separators=(",",":")),flush=True)
    return result

def run_phase(case,variant,order,pid,expected):
    q=native_query(case,EVAL) if variant=="native" else tags_sql_query(case,EVAL)
    prefix=f"r07r01_{RUN}_{case}_{order}_{variant}_"
    phase={"variant":variant,"order":order,"case":case,"trials":[]}
    with RSSMonitor(pid) as observer:
        for i in range(WARMUPS+MEASURED):
            qid=prefix+str(i)
            client_ms,raw=post(HOST,q,qid)
            got=normalize_any(raw) if variant=="native" else normalize_sql(raw,case,EVAL)
            assert_same(expected,got,case,f"{variant}_{order}_{i}")
            phase["trials"].append({"qid":qid,"client_ms":client_ms,"warmup":i<WARMUPS})
    phase["rss"]=observer.report()
    if phase["rss"]["observer_overhead_fraction"]>0.02:
        raise AssertionError(("RSS monitor overhead exceeded",phase["rss"]))
    rows=complete_logs(prefix,{x["qid"] for x in phase["trials"]})
    for trial in phase["trials"]:
        row=rows[trial["qid"]]
        ev=row.get("ProfileEvents",{})
        trial.update({
            "server_ms":row["query_duration_ms"],
            "cpu_ms":ev.get("OSCPUVirtualTimeMicroseconds",0)/1000,
            "selected_marks":ev.get("SelectedMarks"),
            "compressed_read_bytes":ev.get("ReadCompressedBytes"),
            "read_rows":row["read_rows"],"read_bytes":row["read_bytes"],
            "peak_query_memory_bytes":row["memory_usage"],
        })
    measured=phase["trials"][WARMUPS:]
    med=lambda k:statistics.median(x[k] for x in measured)
    phase["summary"]={
        "p50_client_ms":pct([x["client_ms"] for x in measured],.5),
        "p95_client_ms":pct([x["client_ms"] for x in measured],.95),
        "cpu_ms":med("cpu_ms"),
        "read_rows":med("read_rows"),"read_bytes":med("read_bytes"),
        "selected_marks":med("selected_marks"),
        "compressed_read_bytes":med("compressed_read_bytes"),
        "max_peak_query_memory_bytes":max(x["peak_query_memory_bytes"] for x in measured),
        "max_observed_process_rss_bytes":phase["rss"]["rss_observed_max_bytes"],
    }
    print("R07_R01_PHASE",json.dumps({"case":case,"variant":variant,"summary":phase["summary"]},
                                    separators=(",",":")),flush=True)
    return phase

def profile_operators(qids):
    sql("SYSTEM FLUSH LOGS")
    records={}
    for which,qid in qids.items():
        query=(
            "SELECT plan_step_name,name,count() AS instances,"
            "sum(elapsed_us) AS work_us,"
            "sum(input_wait_elapsed_us) AS input_wait_us,"
            "sum(output_wait_elapsed_us) AS output_wait_us,"
            "sum(input_rows) AS input_rows,sum(output_rows) AS output_rows "
            "FROM system.processors_profile_log WHERE query_id="+sql_string(qid)+
            " GROUP BY plan_step_name,name ORDER BY work_us DESC LIMIT 30"
            " FORMAT JSONEachRow"
        )
        try:
            records[which]=[json.loads(x) for x in sql(query).splitlines() if x.strip()]
        except Exception as exc:
            records[which]={"error":str(exc)[:1200]}
    return records

def compare(case,pid,reference):
    phases=[run_phase(case,variant,i,pid,reference)
            for i,variant in enumerate(ORDER)]
    a=[p["summary"] for p in phases if p["variant"]=="native"]
    b=[p["summary"] for p in phases if p["variant"]=="sql"]
    avg=lambda x,k:statistics.mean(p[k] for p in x)
    drift=abs(a[0]["p50_client_ms"]-a[1]["p50_client_ms"])/avg(a,"p50_client_ms")
    summary={"valid_control":drift<=.25,"control_drift":drift,
             "native_over_sql_wall_ratio":avg(a,"p50_client_ms")/avg(b,"p50_client_ms")}
    for field in ("p50_client_ms","p95_client_ms","cpu_ms","read_rows","read_bytes",
                  "selected_marks","compressed_read_bytes","max_peak_query_memory_bytes"):
        summary["native_"+field]=avg(a,field)
        summary["sql_"+field]=avg(b,field)
    profiles=profile_operators({
        "native":phases[0]["trials"][WARMUPS]["qid"],
        "sql":phases[1]["trials"][WARMUPS]["qid"],
    })
    return {"phases":phases,"summary":summary,"profiles":profiles,
            "queries":{"native":native_query(case,EVAL),"sql":tags_sql_query(case,EVAL)}}

def main():
    ap=argparse.ArgumentParser()
    ap.add_argument("--fixture",required=True)
    ap.add_argument("--manifest",required=True)
    ap.add_argument("--pid-file",required=True)
    ap.add_argument("--output",required=True)
    ap.add_argument("--case",nargs="*",choices=sorted(CASES),default=list(CASES))
    a=ap.parse_args()
    report={"question_id":"R07","status":"STARTED","source_sha":SOURCE_SHA,
            "stock_binary_sha256":BINARY_SHA,"fixture_sha256":FIXTURE_SHA,
            "errors":[],"cases":{}}
    save(a.output,report)
    try:
        manifest=json.loads(pathlib.Path(a.manifest).read_text())
        counts,points=expected_counts(3,400)
        if (manifest.get("sha256")!=FIXTURE_SHA or sha256(a.fixture)!=FIXTURE_SHA
            or manifest["rows_by_tier"]!=counts or manifest["samples_by_tier"]!=points
            or manifest["series"]!=28896 or manifest["samples"]!=2488320
            or manifest["node_slots"]!=400 or manifest["days"]!=3
            or manifest["base"]!=BASE or manifest["end"]!=EVAL
            or manifest.get("stagger_policy")!="node start = (slot * 317) % 10800 seconds"):
            raise AssertionError("Frozen R01 full fixture identity/cadence mismatch")
        report["manifest"]=manifest
        report["ddl_samples"]=ddl()
        report["ingest"]=streamed_insert(TABLE,a.fixture)
        if report["ingest"]["rows"]!=28896:
            raise AssertionError(("Incomplete R01 ingestion",report["ingest"]))
        report["optimize"]=optimize_table(TABLE)
        if any(isinstance(v,dict) for v in report["optimize"].values()):
            raise AssertionError(("Incomplete optimize",report["optimize"]))
        wait_no_merges()
        report["physical_before"]=physical(TABLE)
        sample_counts=json.loads(sql("SELECT count() AS bucket_rows,"
            "sum(length(samples)) AS sample_points FROM "+inner(TABLE,"samples")+
            " FORMAT JSONEachRow").strip())
        if sample_counts["sample_points"]!=2488320 or sample_counts["bucket_rows"]!=113760:
            raise AssertionError(("Wrong physical sample bucket count",sample_counts))
        report["sample_counts"]=sample_counts
        report["grid_parity"]=parity_check_all_grid()
        save(a.output,report)
        pid=server_status_pid(a.pid_file)
        report["pid"]=pid
        for case in a.case:
            reference=oracle(case,EVAL)
            report["cases"][case]=compare(case,pid,reference)
            save(a.output,report)
        report["physical_after"]=physical(TABLE)
        if report["physical_before"]!=report["physical_after"]:
            raise AssertionError("Physical layout changed during read-only comparison")
        report["passed"]=all(x["summary"]["valid_control"] for x in report["cases"].values())
        report["status"]="PASS" if report["passed"] else "INVALID_AA_DRIFT"
    except Exception as exc:
        report["passed"]=False
        report["status"]="FAILED"
        report["errors"].append({"fatal":str(exc)[:2200],"trace":traceback.format_exc()[-5100:]})
    finally:
        save(a.output,report)
        compact={"passed":report.get("passed"),"status":report["status"],"errors":report["errors"],
                 "fixture_sha256":FIXTURE_SHA,"sample_counts":report.get("sample_counts"),
                 "cases":{k:v["summary"] for k,v in report["cases"].items()},
                 "grid":{k:{t:v["points"] for t,v in case.items()} for k,case in report.get("grid_parity",{}).items()},
                 "physical_before":report.get("physical_before"),
                 "physical_after":report.get("physical_after")}
        save(str(pathlib.Path(a.output).with_suffix(".compact.json")),compact)
        print("R07_R01_NATIVE_SQL_DONE",json.dumps(compact,separators=(",",":")),flush=True)
    if not report["passed"]:raise SystemExit(1)

if __name__=="__main__":
    main()
