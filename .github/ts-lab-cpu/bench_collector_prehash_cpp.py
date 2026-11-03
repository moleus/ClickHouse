#!/usr/bin/env python3
"""Exact native PromQL tag collector prehash C++ A/B/B/A using the SAME on-disk fixture.

Both pinned-source O1 binaries contain identical native JOIN override patch,
differing ONLY in the tag collector pending TagsKey prehash patch. Restart the ClickHouse
process to switch binaries, retain the exact same immutable 1.33m data.
Validate output labels/timestamps/value tolerance and per-trial CPU/memory.
"""
import argparse,json,os,pathlib,statistics,subprocess,sys
sys.path[:0]=[".github/ts-lab-perf",".github/ts-lab-bench"]
from bench_granularity_v11 import querylogs
from bench_bucket_cxx_abca import post,normalize_any,assert_same
from bench_filtered_selector_v4_sql import sql_string
SHA="8f3a36846f80d6be5a8604d142e934399f2687094766c57bf105e43977f52858"
SEL={"stage":('{stage="stage-0007"}',1),
     "project":('{project="project-00"}',19),
     "regex":('{branch=~"main|feature-0[0-9]"}',38),
     "whole":("",38)}
def save(p,val):
    f=pathlib.Path(p);f.parent.mkdir(parents=True,exist_ok=True)
    f.write_text(json.dumps(val,indent=2,default=str)+"\n")
def main():
    p=argparse.ArgumentParser()
    for k in ("manifest","baseline-bin","patched-bin","restart-helper","output"):
        p.add_argument("--"+k,required=True)
    p.add_argument("--case",required=True,choices=SEL)
    a=p.parse_args()
    m=json.loads(pathlib.Path(a.manifest).read_text())
    assert m["sha256"]==SHA and m["series"]==1330000
    days=1 if a.case=="whole" else 14
    end=int(m["end"]);start=end-(days-1)*86400
    expr=f"avg by(stage)(avg_over_time(jenkins_ci_95k_daily{SEL[a.case][0]}[1d]))"
    q=(f"SELECT * FROM prometheusQueryRange(churn_ts,{sql_string(expr)},"
       f"{start},{end},86400) SETTINGS max_threads=4,max_memory_usage=3500000000,"
       "log_queries=1,log_processors_profiles=1,query_profiler_cpu_time_period_ns=1000000 "
       "FORMAT JSONEachRow")
    expected=SEL[a.case][1]*days
    grid={start+i*86400 for i in range(days)}
    rep={"case":a.case,"days":days,"sha":SHA,"phases":[],"errors":[]}
    save(a.output,rep)
    ref=None
    try:
        for phase,(name,binary) in enumerate((
            ("baseline",a.baseline_bin),("patched",a.patched_bin),
            ("patched",a.patched_bin),("baseline",a.baseline_bin))):
            subprocess.run([a.restart_helper,binary,f"tagprehash_{a.case}_{phase}"],check=True,timeout=160)
            samples=[];prefix=f"tagprehash_{a.case}_{phase}_{os.getenv('GITHUB_RUN_ID','local')}_"
            for i in range(6):
                qid=prefix+str(i)
                ms,raw=post("http://127.0.0.1:18191",q,qid)
                points=normalize_any(raw)
                if len(points)!=expected:raise AssertionError(("Wrong output points",len(points),expected))
                if {t for _labels,t in points}!=grid:raise AssertionError("Wrong timestamp grid")
                if ref is None:ref=points
                else:assert_same(ref,points,a.case,name)
                samples.append({"qid":qid,"client_ms":round(ms,3),"warmup":i<2})
            logs={x["query_id"]:x for x in querylogs(prefix) if x.get("type")=="QueryFinish"}
            if len(logs)!=len(samples):raise AssertionError("Missing per-query log")
            for x in samples:
                log=logs[x["qid"]];pe=log.get("ProfileEvents",{})
                x.update({"cpu_ms":pe.get("OSCPUVirtualTimeMicroseconds",0)/1000,
                          "peak_query_memory":log["memory_usage"],
                          "read_rows":log["read_rows"],"read_bytes":log["read_bytes"]})
            measured=[v for v in samples if not v["warmup"]]
            info={"variant":name,"phase":phase,"trials":samples,
                  "median_ms":statistics.median(x["client_ms"] for x in measured),
                  "median_cpu_ms":statistics.median(x["cpu_ms"] for x in measured),
                  "peak_memory":max(x["peak_query_memory"] for x in measured)}
            rep["phases"].append(info)
            save(a.output,rep)
            print("TAG_PREHASH_PHASE",json.dumps({"case":a.case,"phase":phase,
                  "variant":name,"wall_ms":info["median_ms"],"cpu_ms":info["median_cpu_ms"]}),flush=True)
        bas=[x for x in rep["phases"] if x["variant"]=="baseline"]
        patch=[x for x in rep["phases"] if x["variant"]=="patched"]
        a_ms=statistics.mean(x["median_ms"] for x in bas)
        b_ms=statistics.mean(x["median_ms"] for x in patch)
        drift=abs(bas[0]["median_ms"]-bas[1]["median_ms"])/a_ms
        rep["summary"]={"original_ms":round(a_ms,3),"patched_ms":round(b_ms,3),
            "speedup":round(a_ms/b_ms,4),
            "original_cpu_ms":round(statistics.mean(x["median_cpu_ms"] for x in bas),3),
            "patched_cpu_ms":round(statistics.mean(x["median_cpu_ms"] for x in patch),3),
            "original_peak_memory":max(x["peak_memory"] for x in bas),
            "patched_peak_memory":max(x["peak_memory"] for x in patch),
            "drift":round(drift,4),"valid":drift<=.25}
        if drift>.25:rep["errors"].append({"control_drift":drift})
        rep["passed"]=not rep["errors"]
    except Exception as e:
        rep["errors"].append({"fatal":str(e)[:1700]});rep["passed"]=False
    finally:
        save(a.output,rep)
        compact={"case":a.case,"passed":rep["passed"],"summary":rep.get("summary"),"errors":rep["errors"]}
        save(str(pathlib.Path(a.output).with_suffix(".compact.json")),compact)
        print("TAG_PREHASH_DONE",json.dumps(compact,separators=(",",":")),flush=True)
    if not rep["passed"]:raise SystemExit(1)
if __name__=="__main__":main()
