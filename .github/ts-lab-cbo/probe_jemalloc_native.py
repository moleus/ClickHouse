#!/usr/bin/env python3
"""Prove jemalloc capability and repeat native PromQL OOM workload.

Use only SHA-verified pinned O1 JOIN-override binary built with jemalloc.
Inspect per-query RSS and jemalloc allocated/active/resident/retained, making
it possible to distinguish live allocations from retained allocator pages.
This script does NOT make production changes.
"""
import argparse,json,pathlib,os,sys
sys.path.insert(0,'.github/ts-lab-cbo')
import probe_native_rss_lifetime as base
from bench_granularity_v11 import sql
METRICS=("jemalloc.allocated","jemalloc.active","jemalloc.resident","jemalloc.mapped",
         "jemalloc.retained","jemalloc.fragmentation","MemoryResident",
         "MemoryResidentWithoutPageCache")
def collect():
    q=("SELECT metric,value FROM system.asynchronous_metrics WHERE "
       "metric IN ("+",".join("'"+x+"'" for x in METRICS)+") FORMAT JSONEachRow")
    return {x["metric"]:x["value"] for x in (json.loads(row) for row in sql(q).splitlines() if row)}
def app():
    p=argparse.ArgumentParser()
    p.add_argument("--manifest",required=True)
    p.add_argument("--out",required=True)
    p.add_argument("--table",default="churn_ts")
    p.add_argument("--repeats",type=int,default=12)
    args=p.parse_args()
    if args.repeats<1 or args.repeats>14:raise AssertionError("Bad repeats")
    m=json.loads(pathlib.Path(args.manifest).read_text())
    assert (m["sha256"],m["series"],m["days"])==(base.FIXTURE,1330000,14)
    root=pathlib.Path(args.out);root.mkdir(parents=True,exist_ok=True)
    report={"scope":"native full PromQL with production-like jemalloc allocator",
            "fixture":base.FIXTURE,"series":1330000,"repeats":args.repeats,
            "steps":[],"errors":[],"completed":False,"passed":False}
    try:
        opts=[json.loads(x) for x in sql("SELECT name,value FROM system.build_options WHERE name='USE_JEMALLOC' FORMAT JSONEachRow").splitlines() if x]
        report["build_options"]=opts
        if len(opts)!=1 or opts[0]["value"].lower() not in ("1","on","true","yes"):
            raise AssertionError("USE_JEMALLOC is not enabled on this executable: "+repr(opts))
        metrics=collect();report["initial_jemalloc"]=metrics
        required=("jemalloc.allocated","jemalloc.active","jemalloc.resident")
        if any(x not in metrics for x in required):
            raise AssertionError("Missing jemalloc metrics: "+repr(metrics))
        # Replace the original metric function, not the query or fixture.
        base.asynchronous=collect
        for i in range(args.repeats):
            row=base.one(args.table,m,7,f"jemalloc7_{i}",report)
            report["last_7d_ok"]=bool(row.get("ok"))
            base.save(root/"report.json",report)
            print("JEMALLOC_NATIVE_7D",json.dumps({"index":i,"ok":row.get("ok"),
                "jemalloc":row.get("async_after"),"rss":row.get("rss_after")},
                separators=(",",":")),flush=True)
            if not row.get("ok"):
                report["errors"].append({"failed_7d":i,"error":row.get("error","")[:1200]})
                break
        if len([r for r in report["steps"] if r.get("days")==7 and r.get("ok")])==args.repeats:
            row=base.one(args.table,m,14,"jemalloc14_final",report)
            report["final14_ok"]=bool(row.get("ok"))
            if not row.get("ok"):
                report["observed_final_oom"]=("MEMORY_LIMIT_EXCEEDED" in row.get("error","") or
                    "memory limit exceeded" in row.get("error","").lower())
                if not report["observed_final_oom"]:
                    report["errors"].append({"unexpected_final_error":row.get("error","")[:1500]})
        report["passed"]=not report["errors"] and report.get("final14_ok",False)
    except Exception as e:
        report["errors"].append({"infrastructure":str(e)[:1600]})
    finally:
        report["completed"]=True
        base.save(root/"report.json",report)
        print("JEMALLOC_NATIVE_DONE",json.dumps({"passed":report["passed"],
            "final14_ok":report.get("final14_ok"),"observed_oom":report.get("observed_final_oom"),
            "errors":report["errors"]},separators=(",",":")),flush=True)
    if report["errors"]:raise SystemExit(1)
if __name__=="__main__":app()
