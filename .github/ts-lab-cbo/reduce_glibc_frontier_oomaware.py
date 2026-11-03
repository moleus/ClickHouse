#!/usr/bin/env python3
"""Reduce eight-phase glibc native TimeSeries evidence with expected OOM censoring.

Unlike the original strict reducer, a default-mode Code241 early OOM
does not mean 3 additional queries exist. Verify all observed 101 IDs
and exact stopping point; never impute aborted samples or give default
a fictitious paired performance baseline. Use only the same-runner,
full 12x7d + 14d candidate pairs for exploratory Pareto analysis.
"""
import argparse
import json
import pathlib
import statistics
import sys

SHA="8f3a36846f80d6be5a8604d142e934399f2687094766c57bf105e43977f52858"
MODES=("default","arena4","arena8","trim","trim","arena8","arena4","default")
ENVS={
    "default":{},
    "arena4":{"MALLOC_ARENA_MAX":"4"},
    "arena8":{"MALLOC_ARENA_MAX":"8"},
    "trim":{"MALLOC_TRIM_THRESHOLD_":"131072"},
}
OOM_WORDS=("Code: 241","MEMORY_LIMIT_EXCEEDED")


def median(v):
    return statistics.median(v)


def rss_mib(step):
    value=step.get("process_memory_after",{}).get("status",{}).get("VmRSS")
    if not isinstance(value,str):
        raise AssertionError("Missing process RSS after query")
    size,unit=value.split()
    if unit!="kB":
        raise AssertionError("Unverified RSS unit")
    return int(size)/1024


def is_oom(step):
    message=step.get("error","")
    return all(word in message for word in OOM_WORDS)


def full_phase_metrics(steps):
    if len(steps)!=13 or any(not x.get("ok") for x in steps):
        return None
    seven=steps[:12]
    if any(x.get("days")!=7 or x.get("points")!=266 for x in seven):
        raise AssertionError("Wrong 7d full-grid results")
    if steps[-1].get("days")!=14 or steps[-1].get("points")!=532:
        raise AssertionError("Wrong 14d full-grid result")
    timed=seven[2:]
    for item in timed:
        q=item.get("querylog",{})
        if not all(k in q and isinstance(q[k],(int,float))
                   for k in ("duration_ms","cpu_ms","peak_query_bytes")):
            raise AssertionError("QueryFinish CPU/wall/memory missing")
    return {
        "wall_7d_ms":round(median([x["querylog"]["duration_ms"] for x in timed]),3),
        "cpu_7d_ms":round(median([x["querylog"]["cpu_ms"] for x in timed]),3),
        "rss_after12_mib":round(rss_mib(seven[-1]),3),
        "peak_query_mib":round(median([x["querylog"]["peak_query_bytes"] for x in timed])/1048576,3),
        "rss_after14_mib":round(rss_mib(steps[-1]),3),
        "ok14":True,
    }


def reduce(root):
    evidence={"schema_version":1,"study":"same-runner 8-phase glibc frontier with OOM-aware reduction",
              "fixture_sha256":SHA,"phases":[],"paired_candidates":{},
              "errors":[],"warnings":[],"candidate_pareto_exploratory":[]}
    ids=set()
    for i,mode in enumerate(MODES):
        path=root/f"allocator-frontier-phase{i}-{mode}"/"report.json"
        if not path.is_file():
            evidence["errors"].append(f"Missing {path}")
            continue
        data=json.loads(path.read_text())
        errors=[]
        if data.get("mode")!=mode or data.get("phase")!=str(i):
            errors.append("mode or phase identity mismatch")
        if data.get("fixture_sha256")!=SHA or data.get("series")!=1330000:
            errors.append("fixture hash or 1.33m distinct IDs mismatch")
        if data.get("server_start",{}).get("allocator_env")!=ENVS[mode]:
            errors.append("allocator env mismatch")
        options=data.get("build_use_jemalloc",[])
        if not options or any(str(v.get("value","")).strip() in ("1","ON","true") for v in options):
            errors.append("This is not verified glibc-only source binary")
        if not data.get("completed"):
            errors.append("Phase not marked completed")
        steps=data.get("steps",[])
        if len(steps)>13:
            errors.append("Too many queries")
        failed=[]
        for j,s in enumerate(steps):
            qid=s.get("query_id")
            if not qid or qid in ids:
                errors.append(f"missing or duplicate query_id at index{j}")
            ids.add(qid)
            if s.get("days")!=(7 if j<12 else 14):
                errors.append(f"wrong query window index {j}")
            if s.get("ok"):
                if s.get("points")!=(266 if j<12 else 532):
                    errors.append(f"wrong successful output cardinality index{j}")
                q=s.get("querylog",{})
                if not q or not q.get("duration_ms") or not q.get("cpu_ms"):
                    errors.append(f"missing successful QueryFinish index{j}")
            else:
                if mode!="default" or not is_oom(s):
                    errors.append(f"unexpected non-OOM failure index{j}")
                failed.append(j)
        # Once OOM appears, script should stop; no later attempted queries.
        if failed and (len(failed)!=1 or failed[0]!=len(steps)-1):
            errors.append("Observed queries after a failed query")
        if mode=="default":
            if not failed:
                errors.append("Expected default mode OOM not observed")
            if failed and failed[0] not in (9,12):
                errors.append(f"Unexpected default OOM query index {failed[0]}")
        elif failed or len(steps)!=13 or data.get("passed") is not True:
            errors.append("Candidate did not complete all 12+1 requests")
        status={
            "phase":i,"mode":mode,"recorded_requests":len(steps),
            "success_7d":sum(s.get("ok",False) for s in steps if s.get("days")==7),
            "attempted_14d":any(s.get("days")==14 for s in steps),
            "success_14d":any(s.get("days")==14 and s.get("ok") for s in steps),
            "oom_index":failed[0] if failed else None,
            "phase_valid":not errors,
            "phase_errors":errors
        }
        if not errors and not failed:
            try:status.update(full_phase_metrics(steps))
            except Exception as e:
                status["phase_errors"].append(str(e))
                status["phase_valid"]=False
        evidence["phases"].append(status)
        evidence["errors"].extend(f"phase{i}: {e}" for e in status["phase_errors"])
    evidence["recorded_unique_query_ids"]=len(ids)
    evidence["expected_if_no_oom"]=8*13
    evidence["omitted_due_terminal_oom"]=8*13-len(ids)
    if len(evidence["phases"])!=8 or len(ids)!=101:
        evidence["errors"].append("Artifact does not match observed 8-phase/101-query execution")
    if not evidence["errors"]:
        for mode in ("arena4","arena8","trim"):
            rows=[v for v in evidence["phases"] if v["mode"]==mode]
            if len(rows)!=2 or not all(x.get("success_14d") for x in rows):
                evidence["errors"].append(f"{mode} has no complete paired phases")
                continue
            result={"phases":[v["phase"] for v in rows],"success_14d":2}
            for metric in ("wall_7d_ms","cpu_7d_ms","rss_after12_mib",
                           "peak_query_mib","rss_after14_mib"):
                x,y=(v[metric] for v in rows)
                avg=statistics.mean((x,y))
                drift=abs(x-y)/max(avg,1e-12)
                result[metric]=round(avg,3)
                result[metric+"_drift"]=round(drift,5)
            result["rss_repeatable_25pct"]=result["rss_after12_mib_drift"]<=.25
            evidence["paired_candidates"][mode]=result
        metrics=("wall_7d_ms","cpu_7d_ms","rss_after12_mib")
        for mode,rec in evidence["paired_candidates"].items():
            dominated=any(other!=mode and
                all(c[m]<=rec[m] for m in metrics) and
                any(c[m]<rec[m] for m in metrics)
                for other,c in evidence["paired_candidates"].items())
            rec["mean_pareto_dominated"]=dominated
            if not dominated:evidence["candidate_pareto_exploratory"].append(mode)
        if "arena8" in evidence["paired_candidates"] and not evidence["paired_candidates"]["arena8"]["rss_repeatable_25pct"]:
            evidence["warnings"].append("arena8 RSS drift >25%; averaged RSS not stable")
    evidence["default_performance_control_valid"]=False
    evidence["warnings"].append(
        "Default phase0 stopped on its tenth 7-day request and cannot contribute a complete 12x7d performance median; no causal speedup relative to default.")
    evidence["glibc_diagnostic_valid"]=not evidence["errors"] and (
        sum(p["success_14d"] for p in evidence["phases"])==6 and
        sum(p["oom_index"] is not None for p in evidence["phases"])==2)
    return evidence


def main():
    p=argparse.ArgumentParser()
    p.add_argument("--root",required=True,type=pathlib.Path)
    p.add_argument("--output",required=True,type=pathlib.Path)
    args=p.parse_args()
    result=reduce(args.root)
    args.output.parent.mkdir(parents=True,exist_ok=True)
    args.output.write_text(json.dumps(result,indent=2,sort_keys=True)+"\n")
    print("GLIBC_OOM_AWARE",json.dumps({
        "valid":result["glibc_diagnostic_valid"],
        "query_ids":result["recorded_unique_query_ids"],
        "omitted":result["omitted_due_terminal_oom"],
        "default_control_valid":result["default_performance_control_valid"],
        "pareto":result["candidate_pareto_exploratory"],
        "errors":result["errors"],
    },separators=(",",":")),flush=True)
    return 0 if result["glibc_diagnostic_valid"] else 1


if __name__=="__main__":
    sys.exit(main())
