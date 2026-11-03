#!/usr/bin/env python3
"""Validate glibc same-runner A/B/B/A across fresh ClickHouse server phases.

A successful GitHub job can still contain an expected 14-day OOM. Query IDs
must be unique across all phases; stale query_log joins invalidate timing.
"""
import argparse,json,pathlib,statistics
SHA="8f3a36846f80d6be5a8604d142e934399f2687094766c57bf105e43977f52858"
def rss_mib(row):
    value=row.get("process_memory_after",{}).get("status",{}).get("VmRSS")
    if not value:return None
    units=value.split()
    if len(units)!=2 or units[1]!="kB":raise AssertionError("Unexpected RSS units: "+repr(value))
    return round(int(units[0])/1024,3)
def median(values):
    if not values:return None
    return round(statistics.median(values),4)
def within(x,y):
    if not x or not y:return None
    return round(x/y,5)
def main():
    p=argparse.ArgumentParser()
    p.add_argument("--root",required=True,help="Unpacked evidence root from a single workflow run")
    p.add_argument("--alternative",choices=("trim","arena2"),required=True)
    p.add_argument("--output",required=True)
    a=p.parse_args()
    root=pathlib.Path(a.root)
    report={"scope":"one runner, exact binary/fixture, sequential A/B/B/A fresh-server phases",
        "alternative":a.alternative,"phases":[],"errors":[],"ratios":{}}
    modes=("default",a.alternative,a.alternative,"default")
    all_ids=set()
    for phase,mode in enumerate(modes):
        path=root/f"{'glibc' if a.alternative=='trim' else 'arena2'}-paired-phase{phase}-{mode}"/"report.json"
        if not path.exists():
            report["errors"].append("Missing phase artifact "+str(path));continue
        data=json.loads(path.read_text())
        if data.get("phase")!=str(phase):
            report["errors"].append(f"Phase {phase} not tagged uniquely: {data.get('phase')}")
        if data.get("mode")!=mode or data.get("fixture_sha256")!=SHA or data.get("series")!=1330000:
            report["errors"].append(f"Phase {phase}: mode or fixture mismatch")
        env=data.get("server_start",{}).get("allocator_env",{})
        expected={} if mode=="default" else {"MALLOC_ARENA_MAX":"2"} if mode=="arena2" else {"MALLOC_TRIM_THRESHOLD_":"131072"}
        if env!=expected:report["errors"].append(f"Phase {phase}: allocator env mismatch {env}")
        steps=data.get("steps",[])
        if len(steps)!=13 or any(x["days"]!=7 for x in steps[:12]) or steps[-1]["days"]!=14:
            report["errors"].append(f"Phase {phase}: missing expected 12 x 7d and 14d")
            continue
        seven=steps[:12];last=steps[-1]
        if not all(x.get("ok") for x in seven):
            report["errors"].append(f"Phase {phase}: failed 7d queries")
        for x in steps:
            qid=x.get("query_id")
            if not qid or qid in all_ids:
                report["errors"].append(f"Repeated or missing query id {qid}")
            all_ids.add(qid)
            if x["days"]==7 and x.get("ok") and not x.get("querylog",{}).get("duration_ms"):
                report["errors"].append(f"Missing query log for {qid}")
        if not last.get("ok"):
            error=last.get("error","")
            if "MEMORY_LIMIT_EXCEEDED" not in error and "memory limit exceeded" not in error.lower():
                report["errors"].append(f"Phase {phase}: unexpected 14d failure {error[:150]}")
        walls=[x.get("querylog",{}).get("duration_ms") for x in seven[2:]]
        cpus=[x.get("querylog",{}).get("cpu_ms") for x in seven[2:]]
        if any(x is None for x in walls+cpus):
            report["errors"].append(f"Phase {phase}: missing CPU/wall metrics")
        rss_after=[rss_mib(x) for x in seven]
        curr={"phase":phase,"mode":mode,"fixture_sha256":data["fixture_sha256"],
            "server_allocator_env":env,
            "all_seven_day_queries_ok":all(x.get("ok") for x in seven),
            "probe14_ok":last.get("ok"),
            "rss_after_7d_mib":rss_after,
            "rss_after_14d_mib":rss_mib(last),
            "wall_7d_median_ms":median([x for x in walls if x is not None]),
            "cpu_7d_median_ms":median([x for x in cpus if x is not None]),
            "wall_14d_ms":last.get("querylog",{}).get("duration_ms"),
            "query_peak_14d_bytes":last.get("querylog",{}).get("peak_query_bytes")}
        report["phases"].append(curr)
    if len(report["phases"])==4 and not report["errors"]:
        base=[x for x in report["phases"] if x["mode"]=="default"]
        alt=[x for x in report["phases"] if x["mode"]==a.alternative]
        for key in ("wall_7d_median_ms","cpu_7d_median_ms"):
            b=statistics.mean(x[key] for x in base)
            v=statistics.mean(x[key] for x in alt)
            drift=abs(base[0][key]-base[1][key])/b
            report["ratios"][key]={"default":round(b,3),"alternative":round(v,3),
                 "default_over_alternative":within(b,v),
                 "baseline_drift":round(drift,4),"valid_drift":drift<=0.30}
            if drift>0.30:report["errors"].append("Unstable default "+key)
        b=statistics.mean(x["rss_after_7d_mib"][-1] for x in base)
        v=statistics.mean(x["rss_after_7d_mib"][-1] for x in alt)
        report["ratios"]["rss_after_12_queries"]={
            "default_mib":round(b,3),"alternative_mib":round(v,3),
            "reduction_fraction":round(1-v/b,4)}
        report["ratios"]["probe14_success"]={
            "default":sum(bool(x["probe14_ok"]) for x in base),
            "alternative":sum(bool(x["probe14_ok"]) for x in alt)}
    report["passed"]=len(report["phases"])==4 and not report["errors"]
    pathlib.Path(a.output).parent.mkdir(parents=True,exist_ok=True)
    pathlib.Path(a.output).write_text(json.dumps(report,indent=2)+"\n")
    print("GLIBC_ABBA_REDUCED",json.dumps({"passed":report["passed"],
          "ratios":report["ratios"],"errors":report["errors"]},separators=(",",":")),flush=True)
    if not report["passed"]:raise SystemExit(1)
if __name__=="__main__":main()
