#!/usr/bin/env python3
"""R31 candidate-restricted IN-set against best ANY on canonical 1.33m/14d.

No C++ source change or native PromQL performance claim. The candidate
reuses the exact left TAGS predicate as an extra right TimeRanges ID IN
pre-filter. This may be slower due to the extra TAGS pass.
"""
import argparse
import hashlib
import json
import os
import pathlib
import statistics
import sys

sys.path[:0]=[".github/ts-lab-r31",".github/ts-lab-r30",".github/ts-lab-r06",
              ".github/ts-lab-perf",".github/ts-lab-bench"]
from candidate_restricted_in import restrict_time_ranges_in_to_candidates, diagnostic_alias_with_nested_tags
from bench_large_membership_in import (
    FIXTURE_SHA,SOURCE,CASES,DRIFT_CAP,SETTINGS,TABLE,DAY,inspect_parts,
    native_query, type_safe_fingerprint, sampled_output_digest, fetch_one
)
from bench_membership_in import convert_join_to_in,diagnostic_alias
from bench_timeranges_any_sql import request,quoted,rewritten as any_join,read_query_logs,save
from probe_nested_selector_plan import capture,extract_ids_subquery
from bench_bucket_cxx_abca import normalize_any

ORDER=("any","in","restricted","restricted","in","any")


def query_one(stmt,qid):
    data=[json.loads(line) for line in request(stmt+" FORMAT JSONEachRow",qid,timeout=300).splitlines() if line]
    if len(data)!=1:raise ValueError(f"Unexpected output count={len(data)}")
    return data[0]


def compare_three(ids,case,runid):
    candidates={"any":any_join(ids),
                "in":convert_join_to_in(ids)}
    candidates["restricted"]=restrict_time_ranges_in_to_candidates(candidates["in"])
    values={}
    for mode,stmt in candidates.items():
        if mode=="restricted":
            named=diagnostic_alias_with_nested_tags(stmt)
            result=fetch_one(
                "SELECT count() AS rows,uniqExact(r31_id) AS ids,"
                "sum(sipHash64(r31_id)) AS hash_sum,"
                "sum(sipHash64(r31_id, 'r31-independent')) AS hash_sum_alt "
                f"FROM ({named}) SETTINGS {SETTINGS}",
                f"r31large_fp_{case}_{mode}_{runid}")
        else:
            result=type_safe_fingerprint(stmt,case,mode,runid)
        values[mode]=result
    for field in ("ids","hash_sum","hash_sum_alt"):
        hashes={name:str(v[field]) for name,v in values.items()}
        if len(set(hashes.values()))!=1:
            raise ValueError(f"{case}: three-way fingerprint mismatch {field}: {hashes}")
    return candidates,values


def bench(candidates,case,runid):
    phases=[]
    qids=[]
    for phase,mode in enumerate(ORDER):
        trials=[]
        named=(diagnostic_alias_with_nested_tags(candidates[mode])
               if mode=="restricted" else diagnostic_alias(candidates[mode]))
        sql="SELECT count() AS n,uniqExact(r31_id) AS u FROM ("+named+") SETTINGS "+SETTINGS
        for i in range(7):
            qid=f"r31cand_{case}_{phase}_{mode}_{i}_{runid}"
            result=query_one(sql,qid)
            if int(result["u"])<=0:raise ValueError("No ID output")
            trials.append({"query_id":qid,"warmup":i<3,"id_count":int(result["u"]),
                           "physical_output_rows":int(result["n"])})
            qids.append(qid)
        phases.append({"phase":phase,"mode":mode,"trials":trials})
    logs=read_query_logs(qids)
    for phase in phases:
        measured=[]
        for t in phase["trials"]:
            r=logs[t["query_id"]]
            t.update({"wall_ms":r["query_duration_ms"],
                      "cpu_ms":(r.get("ProfileEvents") or {}).get("OSCPUVirtualTimeMicroseconds",0)/1000,
                      "peak_memory_bytes":r["memory_usage"],
                      "read_rows":r["read_rows"],"read_bytes":r["read_bytes"]})
            if not t["warmup"]:measured.append(t)
        phase["median"]={k:statistics.median(t[k] for t in measured) for k in
                         ("wall_ms","cpu_ms","peak_memory_bytes","read_rows","read_bytes")}
    summary={}
    for mode in ("any","in","restricted"):
        pp=[v["median"] for v in phases if v["mode"]==mode]
        agg={k:statistics.mean(v[k] for v in pp) for k in pp[0]}
        agg["control_drift"]=abs(pp[0]["wall_ms"]-pp[1]["wall_ms"])/max(1,agg["wall_ms"])
        summary[mode]=agg
    return {"phases":phases,"summary":summary,
            "drift_passed":all(x["control_drift"]<=DRIFT_CAP for x in summary.values())}


def main():
    p=argparse.ArgumentParser()
    p.add_argument("--manifest",required=True)
    p.add_argument("--server-log",required=True)
    p.add_argument("--out",required=True)
    args=p.parse_args()
    meta=json.loads(pathlib.Path(args.manifest).read_text())
    if meta.get("sha256")!=FIXTURE_SHA or meta.get("series")!=1330000 or meta.get("days")!=14:
        raise ValueError("Canonical fixture mismatch")
    out=pathlib.Path(args.out)
    out.mkdir(parents=True,exist_ok=True)
    runid=os.getenv("GITHUB_RUN_ID","local")
    report={"question":"R31","kind":"CANDIDATE_RESTRICTED_TIME_RANGES_IN",
            "status":"INCONCLUSIVE","errors":[],"source_sha":SOURCE,
            "fixture_sha":FIXTURE_SHA,"cases":{},"cpp_modified":False}
    save(out/"report.json",report)
    try:
        report["frozen_parts"]=inspect_parts()
        for case in ("stage14","project14","project1"):
            matcher,days=CASES[case]
            q=native_query(meta["end"],matcher,days)
            offset=pathlib.Path(args.server_log).stat().st_size
            original=normalize_any(request(q,f"r31cand_orig_{case}_{runid}",timeout=300))
            if not original:raise ValueError("Empty original native output")
            generated=capture(args.server_log,offset)
            ids=extract_ids_subquery(generated)
            queries,fingerprints=compare_three(ids,case,runid)
            sample_sha={}
            # All three SQL paths must return exactly the same first 64 sorted
            # (ID, full samples) rows, with two full-set hash fingerprints.
            for mode,subquery in queries.items():
                outer=generated.replace(ids,subquery,1)
                if generated.count(ids)!=1:raise ValueError("Generated subquery unexpectedly repeated")
                sample_sha[mode]=sampled_output_digest(outer,case,mode,runid)
            if len({v["sha256"] for v in sample_sha.values()})!=1:
                raise ValueError(f"{case}: mismatched first64 SAMPLES hashes")
            entry={"native_points":len(original),
                   "generated_selector_sha256":hashlib.sha256(generated.encode()).hexdigest(),
                   "query_sql":queries,
                   "fingerprints":fingerprints,
                   "samples_first64":sample_sha,
                   "benchmark":bench(queries,case,runid)}
            report["cases"][case]=entry
            save(out/"report.json",report)
            print("R31_CANDIDATE_CASE",case,json.dumps({
                "f":fingerprints,"benchmark":entry["benchmark"]["summary"],
                "drift_passed":entry["benchmark"]["drift_passed"]}),flush=True)
        report["status"]="PASS_DIAGNOSTIC_SQL_ONLY" if all(
            x["benchmark"]["drift_passed"] for x in report["cases"].values()) else "INCONCLUSIVE_DRIFT"
    except Exception as e:
        report["status"]="INCONCLUSIVE_OR_REJECT"
        report["errors"].append(str(e))
    finally:
        save(out/"report.json",report)
        print("R31_CANDIDATE_DONE",json.dumps({"status":report["status"],
            "errors":report["errors"],"cases":list(report["cases"])}),flush=True)
    if report["status"]!="PASS_DIAGNOSTIC_SQL_ONLY":
        raise SystemExit(1)


if __name__=="__main__":
    main()
