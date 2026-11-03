#!/usr/bin/env python3
"""R31 safety falsifier on canonical 1.33m/14d Jenkins workload.

Same O1 lab binary and completely frozen v11 source table. For stage/project
selectors, compare ALL/ANY/IN ID memberships and full source SAMPLES outputs,
then CPU/wall/tracked query peak in balanced A/B/C/C/B/A.

No C++ patch or full modified-native benchmark. If an IN-set exceeds the memory
limit or OOMs, preserve the negative result instead of calling it success.
"""
import argparse
import hashlib
import json
import os
import pathlib
import statistics
import sys
import time

sys.path[:0]=[".github/ts-lab-r31",".github/ts-lab-r30",".github/ts-lab-r06",
              ".github/ts-lab-perf",".github/ts-lab-bench",".github/ts-lab-cpu"]
from bench_membership_in import convert_join_to_in, diagnostic_alias, MODE_ORDER
from bench_timeranges_any_sql import (
    request, quoted, rewritten as any_join, read_query_logs, save
)
from probe_nested_selector_plan import capture, extract_ids_subquery
from bench_granularity_v11 import inner
from bench_bucket_cxx_abca import normalize_any

SOURCE="52529f0c711566d696917678bf15e7068772fdde"
FIXTURE_SHA="8f3a36846f80d6be5a8604d142e934399f2687094766c57bf105e43977f52858"
METRIC="jenkins_ci_95k_daily"
TABLE="churn_ts"
DAY=86400
CASES={"stage14":('stage="stage-0007"',14),
       "project14":('project="project-00"',14),
       "project1":('project="project-00"',1)}
SETTINGS=("max_threads=4,max_memory_usage=1800000000,"
          "join_algorithm='full_sorting_merge',optimize_read_in_order=1,"
          "optimize_use_projections=0,log_queries=1")
DRIFT_CAP=0.25


def sql(stmt,qid=None,timeout=300):
    return request(stmt,qid,timeout)


def fetch_one(stmt,qid=None):
    raw=sql(stmt+" FORMAT JSONEachRow",qid)
    values=[json.loads(line) for line in raw.splitlines() if line]
    if len(values)!=1: raise ValueError(f"Expected one result row, got {len(values)}")
    return values[0]


def type_safe_fingerprint(ids,case,mode,runid):
    named=diagnostic_alias(ids)
    # Fingerprints only; full exact ID sets may be prohibitively costly on
    # 1.33m series. Track exact NDV and two noncryptographic aggregate digests.
    return fetch_one(
        "SELECT count() AS rows,uniqExact(r31_id) AS ids,"
        "sum(sipHash64(r31_id)) AS hash_sum,"
        "sum(cityHash64(toString(r31_id))) AS city_sum "
        f"FROM ({named}) SETTINGS {SETTINGS}",
        f"r31large_fp_{case}_{mode}_{runid}"
    )


def sampled_output_digest(generated,case,mode,runid):
    # Explicitly cap SAMPLES work to a fixed first 64 ids, not an oracle
    # for all selected IDs. Full modified-native oracle remains required.
    raw=sql("SELECT id,samples FROM ("+generated+") ORDER BY id LIMIT 64 "
            f"SETTINGS {SETTINGS} FORMAT JSONEachRow",
            f"r31large_sample_{case}_{mode}_{runid}",timeout=300)
    if not raw: raise ValueError("Empty sampled SAMPLES output")
    return {"rows":len(raw.splitlines()),"sha256":hashlib.sha256(raw.encode()).hexdigest()}


def inspect_parts():
    result={}
    for kind in ("tags","timeranges","samples"):
        target=inner(TABLE,kind)
        raw=target.split(chr(96))[1]
        stat=fetch_one("SELECT count() AS part_count,sum(rows) AS rows,"
                       "sum(marks) AS marks,sum(bytes_on_disk) AS bytes "
                       "FROM system.parts WHERE active AND database='default' "
                       f"AND table={quoted(raw)}")
        result[kind]={"target":target,**stat}
        if int(stat["part_count"])!=1:
            raise ValueError(f"Inner {kind} is not one frozen part: {stat}")
    return result


def native_query(finish,match,days):
    start=finish-(days-1)*DAY
    expr=f'avg by(stage)(avg_over_time({METRIC}{{{match}}}[1d]))'
    return (f"SELECT * FROM prometheusQueryRange({TABLE},{quoted(expr)},{start},"
            f"{finish},86400) SETTINGS {SETTINGS} FORMAT JSONEachRow")


def paired_bench(variants,case,runid):
    phases=[]
    ids=[]
    for step,mode in enumerate(MODE_ORDER):
        query=diagnostic_alias(variants[mode])
        trials=[]
        for i in range(7):
            qid=f"r31large_{case}_{mode}_{step}_{i}_{runid}"
            rec=fetch_one("SELECT count() AS n,uniqExact(r31_id) AS u "
                          f"FROM ({query}) SETTINGS {SETTINGS}",qid)
            trials.append({"query_id":qid,"rows":int(rec["n"]),
                           "ids":int(rec["u"]),"warmup":i<3})
            ids.append(qid)
        phases.append({"phase":step,"mode":mode,"trials":trials})
    logs=read_query_logs(ids)
    for p in phases:
        valid=[]
        for t in p["trials"]:
            l=logs[t["query_id"]]
            evt=l.get("ProfileEvents") or {}
            t.update({"wall_ms":int(l["query_duration_ms"]),
                      "cpu_ms":float(evt.get("OSCPUVirtualTimeMicroseconds",0))/1000,
                      "query_peak_bytes":int(l["memory_usage"]),
                      "read_rows":int(l["read_rows"]),
                      "read_bytes":int(l["read_bytes"])})
            if not t["warmup"]: valid.append(t)
        p["median"]={key:statistics.median(x[key] for x in valid)
                     for key in ("wall_ms","cpu_ms","query_peak_bytes","read_rows","read_bytes")}
    stats={}
    for mode in ("all","any","in"):
        medians=[p["median"] for p in phases if p["mode"]==mode]
        stats[mode]={k:statistics.mean(x[k] for x in medians) for k in medians[0]}
        stats[mode]["control_drift"]=abs(medians[0]["wall_ms"]-medians[1]["wall_ms"])/max(1,stats[mode]["wall_ms"])
    return {"summary":stats,"phases":phases,
            "valid_timing":all(v["control_drift"]<=DRIFT_CAP for v in stats.values())}


def main():
    p=argparse.ArgumentParser()
    p.add_argument("--manifest",required=True)
    p.add_argument("--server-log",required=True)
    p.add_argument("--out",required=True)
    args=p.parse_args()
    folder=pathlib.Path(args.out)
    folder.mkdir(parents=True,exist_ok=True)
    manifest=json.loads(pathlib.Path(args.manifest).read_text())
    if manifest["sha256"]!=FIXTURE_SHA or manifest["series"]!=1330000 or manifest["days"]!=14:
        raise ValueError("Canonical fixture identity mismatch")
    end=int(manifest["end"])
    runid=os.environ.get("GITHUB_RUN_ID","local")
    report={"question":"R31","status":"INCONCLUSIVE","errors":[],
            "source_sha":SOURCE,"fixture_manifest":manifest,
            "native_cpp_patched":False,"cases":{}}
    save(folder/"report.json",report)
    try:
        report["parts"]=inspect_parts()
        report["runner_log_query_timeout_sec"]=300
        for case,(matcher,days) in CASES.items():
            native=native_query(end,matcher,days)
            offset=pathlib.Path(args.server_log).stat().st_size
            raw=sql(native,f"r31large_native_{case}_{runid}",timeout=300)
            values=normalize_any(raw)
            if not values:
                raise ValueError(f"Empty stock native result for {case}")
            generated=capture(args.server_log,offset)
            ids=extract_ids_subquery(generated)
            versions={"all":ids,"any":any_join(ids),"in":convert_join_to_in(ids)}
            record={"native_query":native,"native_points":len(values),
                    "native_hash":hashlib.sha256(raw.encode()).hexdigest(),
                    "generated_sha":hashlib.sha256(generated.encode()).hexdigest(),
                    "ids_queries":versions,"versions":{}}
            report["cases"][case]=record
            for mode,query in versions.items():
                rec=type_safe_fingerprint(query,case,mode,runid)
                selector=generated.replace(ids,query,1)
                if generated.count(ids)!=1: raise ValueError("Unexpected native nested ID SQL not unique")
                rec["first_64_samples"]=sampled_output_digest(selector,case,mode,runid)
                record["versions"][mode]=rec
                save(folder/"report.json",report)
            for field in ("ids","hash_sum","city_sum"):
                values={mode:x[field] for mode,x in record["versions"].items()}
                if len(set(str(v) for v in values.values()))!=1:
                    raise ValueError(f"{case} mismatched {field}: {values}")
            first={x["first_64_samples"]["sha256"] for x in record["versions"].values()}
            if len(first)!=1: raise ValueError(f"{case} first 64 complete SAMPLES rows differ")
            record["benchmark"]=paired_bench(versions,case,runid)
            print("R31_LARGE_RESULT",case,
                  json.dumps({"native_points":record["native_points"],
                              "fingerprints":record["versions"],
                              "bench":record["benchmark"]["summary"],
                              "valid_timing":record["benchmark"]["valid_timing"]}),flush=True)
            save(folder/"report.json",report)
        report["status"]="PASS_DIAGNOSTIC_SQL_ONLY" if all(
            record["benchmark"]["valid_timing"] for record in report["cases"].values()
        ) else "INCONCLUSIVE_DRIFT"
    except Exception as exc:
        report["status"]="INCONCLUSIVE_OR_REJECT"
        report["errors"].append(str(exc))
    finally:
        save(folder/"report.json",report)
        print("R31_LARGE_DONE",json.dumps({
            "status":report["status"],"errors":report["errors"],
            "cases":list(report["cases"]),"part_counts":{
                k:v.get("part_count") for k,v in report.get("parts",{}).items()}
        }),flush=True)
    if report["status"]!="PASS_DIAGNOSTIC_SQL_ONLY":
        raise SystemExit(1)


if __name__=="__main__":
    main()
