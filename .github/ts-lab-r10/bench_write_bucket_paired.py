#!/usr/bin/env python3
"""R10: fresh-server A/B/B/A 3600/10800s RAW_UUID TimeSeries batch ingest + merge.

Same source-pinned O1 executable and byte-identical synthetic 400-node
3-day fixture. Fresh ClickHouse process per phase, unique output table and
INSERT query IDs, background merges stopped during 400-series batches.
CPU is read from /proc/PID/stat for ALL ClickHouse threads, not query_log
CPU alone. After ingestion explicitly start merges and OPTIMIZE FINAL all
three TimeSeries targets, then stop merges. Capture part_log, query_log,
process CPU, active physical parts/bytes and complete native PromQL parity.
"""
import argparse
import hashlib
import json
import os
import pathlib
import statistics
import subprocess
import sys
import time
import traceback

sys.path[:0] = [
    ".github/ts-lab-r10", ".github/ts-lab-r01",
    ".github/ts-lab-perf", ".github/ts-lab-bench",
    ".github/ts-lab-cpu", ".github/ts-lab/experiments",
]
from bench_granularity_v11 import inner, optimize_table, sql, wait_no_merges
from bench_bucket_cxx_abca import post, normalize_any, assert_same
from bench_native_multiclient_stock_20261103 import (
    RSSMonitor, complete_logs, physical, save, server_status_pid, sha256
)
from bench_k8s_mixed_native import query, DAY
from bench_bucket_id_factorial import physical_audit, FIXTURE_SHA
from generate_k8s_churn_fixture import BASE, LIFE, expected_counts
from r02_small_batch_write_merge import part_log, table_uuid

BINARY_SHA = "f0a2fa1f814c58853f43423705cac10ab9df3bfe5887e27ebe9620ba7cb9660e"
SOURCE_SHA = "52529f0c711566d696917678bf15e7068772fdde"
PHASE_STEPS = (3600, 10800, 10800, 3600)
BATCH_ROWS = 400
EXPECTED_ROWS = 28896
EXPECTED_SAMPLES = 2488320
HOST = "http://127.0.0.1:18191"
RUN = os.getenv("GITHUB_RUN_ID", "local")


def cpu_seconds(pid):
    # /proc/PID/stat is thread-group aggregate for the process leader.
    raw = pathlib.Path(f"/proc/{pid}/stat").read_text()
    fields = raw[raw.rfind(")") + 2:].split()
    return (int(fields[11]) + int(fields[12])) / os.sysconf("SC_CLK_TCK")


def table_name(phase):
    return f"tslab_r10_write_p{phase}"


def create_table(name, step):
    sql(
        f"CREATE TABLE {name} ENGINE=TimeSeries "
        f"SETTINGS recent_samples_ttl_seconds=0,"
        f"samples_bucket_step_seconds={step} "
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


def ensure_no_merges():
    sql("SYSTEM STOP MERGES")
    for attempt in range(36):
        n=int(sql("SELECT count() FROM system.merges FORMAT TSV").strip())
        if n == 0: return
        time.sleep(0.5)
    raise RuntimeError("Merges did not stop within 18 seconds")


def insert_batches(name, path, phase):
    results=[]
    total=0
    digest=hashlib.sha256()
    with open(path, "rb") as fp:
        while True:
            lines=[fp.readline() for _ in range(BATCH_ROWS)]
            lines=[x for x in lines if x]
            if not lines: break
            block=b"".join(lines)
            digest.update(block)
            qid=f"r10write_{RUN}_p{phase}_b{len(results)}"
            statement=(f"INSERT INTO {name} (metric_name,tags,samples) "
                       "FORMAT JSONEachRow\n"+block.decode("utf-8"))
            ms,_=post(HOST,statement,qid)
            results.append({
                "qid":qid,"rows":len(lines),"bytes":len(block),
                "wall_ms":round(ms,3)
            })
            total+=len(lines)
            if len(results)%20==0:
                print("R10_WRITE_BATCH",json.dumps(
                    {"phase":phase,"batches":len(results),"rows":total},
                    separators=(",",":")),flush=True)
    if total!=EXPECTED_ROWS or digest.hexdigest()!=FIXTURE_SHA:
        raise AssertionError(("Input rows or hash drift",total,digest.hexdigest()))
    return results


def snapshot_parts(name):
    return physical(name)


def sum_bytes(physical_result):
    return sum(int(v["parts"]["bytes"]) for v in physical_result.values())


def query_correctness(name, manifest, reference, phase):
    end=int(manifest["end"])
    begin=end-2*DAY
    result={}
    for case,expected in (("node_broad",24),("pod_namespace",3)):
        text=query(name,case,begin,end)
        ms,body=post(HOST,text,f"r10write_{RUN}_p{phase}_native_{case}")
        points=normalize_any(body)
        ticks={begin,begin+DAY,end}
        if len(points)!=expected or {t for _,t in points}!=ticks:
            raise AssertionError((name,case,"Wrong full native point grid",len(points)))
        if case not in reference: reference[case]=points
        else: assert_same(reference[case],points,case,"R10-write")
        result[case]={"points":len(points),"wall_client_ms":round(ms,3),
                      "full_label_time_value_parity":True}
    return result


def summarize_write_logs(batches):
    ids={item["qid"] for item in batches}
    prefix=f"r10write_{RUN}_p"
    records=complete_logs(prefix,ids)
    result={"query_finish_count":len(records),"total_queryfinish_cpu_ms":0.0,
            "total_queryfinish_wall_ms":0.0,"max_query_memory_bytes":0}
    for row in records.values():
        ev=row.get("ProfileEvents",{})
        result["total_queryfinish_cpu_ms"]+=ev.get("OSCPUVirtualTimeMicroseconds",0)/1000
        result["total_queryfinish_wall_ms"]+=row.get("query_duration_ms",0)
        result["max_query_memory_bytes"]=max(
            result["max_query_memory_bytes"],row.get("memory_usage",0))
    result["total_queryfinish_cpu_ms"]=round(result["total_queryfinish_cpu_ms"],3)
    return result


def get_part_merge_log(name):
    data=part_log(HOST, [table_uuid(HOST,name)])
    if not data.get("available"):
        raise AssertionError(("part_log unavailable",data))
    events=[v for v in data["events"] if v.get("event_type")=="MergeParts"]
    if not events:
        raise AssertionError(("No MergeParts events for optimized table",name,data))
    result={"events":events,"merge_event_count":len(events),
            "cpu_ms":round(sum(x.get("ProfileEvents",{}).get(
                "OSCPUVirtualTimeMicroseconds",0) for x in events)/1000,3),
            "duration_ms":sum(x.get("duration_ms",0) for x in events)}
    return result


def run_phase(phase,step,args,reference,manifest):
    name=table_name(phase)
    report={"phase":phase,"step":step,"table":name,"status":"started"}
    save(args.output,args.report)
    subprocess.run(
        [args.restart_script,args.binary,f"r10write_{phase}_{step}"],
        check=True,timeout=260
    )
    pid=server_status_pid(args.pid_file)
    report["server_pid"]=pid
    ensure_no_merges()
    create_table(name,step)
    # In each phase the same 400-series batches are applied to an empty table.
    ensure_no_merges()
    samples={"before_insert":snapshot_parts(name)}
    cpu_before=cpu_seconds(pid)
    before=time.perf_counter()
    with RSSMonitor(pid) as observer:
        batches=insert_batches(name,args.fixture,phase)
        insert_elapsed=time.perf_counter()-before
        cpu_after_insert=cpu_seconds(pid)
        samples["before_merge"]=snapshot_parts(name)
        # Explicitly permit merges only after all batches finish.
        merge_start=time.perf_counter()
        cpu_before_merge=cpu_seconds(pid)
        sql("SYSTEM START MERGES")
        optimized=optimize_table(name)
        if any(isinstance(x,dict) for x in optimized.values()):
            raise AssertionError(("OPTIMIZE FINAL returned a recorded error",optimized))
        ensure_no_merges()
        merge_elapsed=time.perf_counter()-merge_start
        cpu_after_merge=cpu_seconds(pid)
        samples["after_merge"]=snapshot_parts(name)
    rss=observer.report()
    if rss["observer_overhead_fraction"]>0.02:
        raise AssertionError(("RSS observation exceeds 2% wall",rss))
    audit=physical_audit(name,EXPECTED_ROWS,EXPECTED_SAMPLES,
                         EXPECTED_ROWS*LIFE//step,"raw")
    for layer,v in audit["physical"].items():
        if v["parts"]["part_count"]!=1:
            raise AssertionError((name,layer,"Final active part count !=1",v))
    log=summarize_write_logs(batches)
    merges=get_part_merge_log(name)
    native=query_correctness(name,manifest,reference,phase)
    report.update({
        "batches":batches,"batch_count":len(batches),"total_records":sum(b["rows"] for b in batches),
        "batch_size_rows":BATCH_ROWS,
        "total_input_bytes":sum(b["bytes"] for b in batches),
        "ingest_total_wall_s":round(insert_elapsed,4),
        "ingest_client_wall_sum_s":round(sum(b["wall_ms"] for b in batches)/1000,4),
        "ingest_server_process_cpu_s":round(cpu_after_insert-cpu_before,4),
        "merge_wall_s":round(merge_elapsed,4),
        "merge_server_process_cpu_s":round(cpu_after_merge-cpu_before_merge,4),
        "merge_optimized":optimized,"merge_partlog":merges,
        "querylogs":log,"rss":rss,"physical":samples,
        "physical_final_disk_bytes":sum_bytes(audit["physical"]),
        "audit":audit,
        "native":native,"status":"pass",
    })
    print("R10_WRITE_PHASE",json.dumps({
        "phase":phase,"step":step,"status":"pass",
        "insert_cpu_s":report["ingest_server_process_cpu_s"],
        "insert_wall_s":report["ingest_total_wall_s"],
        "merge_cpu_s":report["merge_server_process_cpu_s"],
        "merge_wall_s":report["merge_wall_s"],
        "final_bytes":report["physical_final_disk_bytes"],
        "merge_events":merges["merge_event_count"]
    },separators=(",",":")),flush=True)
    return report


def main():
    ap=argparse.ArgumentParser()
    for key in ("binary","restart-script","pid-file","fixture","manifest","output"):
        ap.add_argument("--"+key,required=True)
    args=ap.parse_args()
    report={
        "question_id":"R10-bucket-id-factorial-v1",
        "study":"fresh-process A/B/B/A raw UUID 400-series batch write and merge",
        "source_sha":SOURCE_SHA,"binary_sha256":BINARY_SHA,
        "fixture_sha256":FIXTURE_SHA,"steps":PHASE_STEPS,
        "phases":[],"errors":[],"passed":False,
    }
    args.report=report
    save(args.output,report)
    try:
        m=json.loads(pathlib.Path(args.manifest).read_text())
        rows,samples=expected_counts(3,400)
        if (sha256(args.fixture)!=FIXTURE_SHA or m.get("sha256")!=FIXTURE_SHA
            or m.get("base")!=BASE or m.get("series")!=sum(rows.values())
            or m.get("samples")!=sum(samples.values())
            or m.get("days")!=3 or m.get("node_slots")!=400
            or sha256(args.binary)!=BINARY_SHA):
            raise AssertionError("Source fixture/binary pinned identity failed")
        report["manifest"]=m
        reference={}
        for phase,step in enumerate(PHASE_STEPS):
            placeholder={"phase":phase,"step":step,"status":"incomplete"}
            report["phases"].append(placeholder)
            save(args.output,report)
            item=run_phase(phase,step,args,reference,m)
            report["phases"][-1]=item
            save(args.output,report)
        a=[p for p in report["phases"] if p["step"]==3600]
        b=[p for p in report["phases"] if p["step"]==10800]
        summary={"comparison":"3600 vs10800 fresh-server A/B/B/A",
                 "phase_order":PHASE_STEPS}
        for key in ("ingest_server_process_cpu_s","ingest_total_wall_s",
                    "merge_server_process_cpu_s","merge_wall_s","physical_final_disk_bytes"):
            left=statistics.mean(p[key] for p in a)
            right=statistics.mean(p[key] for p in b)
            drift=abs(a[0][key]-a[1][key])/max(left,1e-9)
            bdrift=abs(b[0][key]-b[1][key])/max(right,1e-9)
            summary[key]={"baseline_3600":round(left,4),
                          "candidate_10800":round(right,4),
                          "candidate_div_baseline":round(right/max(left,1e-9),4),
                          "baseline_control_drift":round(drift,4),
                          "candidate_control_drift":round(bdrift,4),
                          "valid_control":drift<=.25 and bdrift<=.25}
            if not summary[key]["valid_control"]:
                report["errors"].append({
                    "metric":key,"invalid_abba_drift":summary[key]
                })
        report["summary"]=summary
        report["passed"]=not report["errors"]
    except Exception as e:
        report["errors"].append({
            "fatal":str(e)[:2200],"traceback":traceback.format_exc()[-4300:]
        })
    finally:
        save(args.output,report)
        short={
            "passed":report["passed"],"errors":report["errors"],
            "summary":report.get("summary"),
            "phases":[{
                k:p.get(k) for k in (
                    "phase","step","status","ingest_server_process_cpu_s",
                    "ingest_total_wall_s","merge_server_process_cpu_s",
                    "merge_wall_s","physical_final_disk_bytes","batch_count",
                    "total_input_bytes"
                )} for p in report["phases"]],
        }
        save(str(pathlib.Path(args.output).with_suffix(".compact.json")),short)
        print("R10_WRITE_DONE",json.dumps(short,separators=(",",":")),flush=True)
    if not report["passed"]: raise SystemExit(1)


if __name__=="__main__":
    main()
