#!/usr/bin/env python3
"""Measure process RSS retention across native PromQL runs on same binary.

Modes run 14d whole metric first or after repeated 7d whole queries.
A fresh dedicated server is started by the caller for each mode. This is a
memory-lifetime diagnostic, NOT an A/B latency speedup claim.
"""
import argparse,json,os,pathlib,sys,time
sys.path.insert(0,".github/ts-lab-bench")
sys.path.insert(0,".github/ts-lab-perf")
from bench_bucket_cxx_abca import normalize_any,post
from bench_filtered_selector_v4_sql import sql_string
from bench_granularity_v11 import sql,querylogs
FIXTURE="8f3a36846f80d6be5a8604d142e934399f2687094766c57bf105e43977f52858"
DAY=86400
EXPR='avg by(stage)(avg_over_time(jenkins_ci_95k_daily[1d]))'
def save(path,x):
    pathlib.Path(path).parent.mkdir(parents=True,exist_ok=True)
    pathlib.Path(path).write_text(json.dumps(x,indent=2,default=str)+"\n")
def rss():
    base=pathlib.Path(os.environ["RUNNER_TEMP"])/"ch.pid"
    if not base.exists():return {"error":"No ch.pid"}
    parent=int(base.read_text().strip())
    candidates=[parent]
    try:
        children=pathlib.Path(f"/proc/{parent}/task/{parent}/children").read_text().split()
        candidates.extend(int(x) for x in children)
    except OSError:pass
    result={}
    for pid in candidates:
        path=pathlib.Path(f"/proc/{pid}/status")
        if not path.exists():continue
        data={}
        for line in path.read_text().splitlines():
            if line.startswith(("Name:","VmRSS:","VmHWM:","VmSize:","Threads:")):
                k,v=line.split(":",1);data[k]=v.strip()
        result[str(pid)]=data
    return result
def asynchronous():
    try:
        raw=sql("SELECT metric,value FROM system.asynchronous_metrics WHERE metric LIKE 'jemalloc%' OR metric LIKE 'Memory%' FORMAT JSONEachRow")
        return [json.loads(s) for s in raw.splitlines() if s.strip()]
    except Exception as e:return {"error":str(e)[:400]}
def query(table,start,end):
    return f"SELECT * FROM prometheusQueryRange({table},{sql_string(EXPR)},{start},{end},{DAY}) SETTINGS max_threads=4,max_memory_usage=3500000000 FORMAT JSONEachRow"
def one(table,m,days,label,report):
    end=int(m["end"]);start=end-(days-1)*DAY
    qid=f"rsslife_{label}_{days}_{os.getenv('GITHUB_RUN_ID','local')}"
    row={"query_id":qid,"days":days,"label":label,"rss_before":rss(),"async_before":asynchronous()}
    report["steps"].append(row)
    try:
        ms,body=post("http://127.0.0.1:18191",query(table,start,end),qid)
        output=normalize_any(body)
        if len(output)!=38*days:raise AssertionError(f"Wrong full result points={len(output)}, expected={38*days}")
        row.update({"ok":True,"client_ms":ms,"points":len(output)})
    except Exception as e:
        row.update({"ok":False,"error":str(e)[:2100]})
    row["rss_after"]=rss()
    row["async_after"]=asynchronous()
    try:
        sql("SYSTEM FLUSH LOGS")
        found=[x for x in querylogs(qid) if x.get("type")=="QueryFinish" and x.get("query_id")==qid]
        if found:
            q=found[0];events=q.get("ProfileEvents",{})
            row["querylog"]={"duration_ms":q.get("query_duration_ms"),
                "peak_query_bytes":q.get("memory_usage"),"read_rows":q.get("read_rows"),
                "cpu_ms":events.get("OSCPUVirtualTimeMicroseconds",0)/1000}
    except Exception as e:row["querylog_error"]=str(e)[:450]
    print("RSS_LIFETIME_STEP",json.dumps({"label":label,"days":days,
        "ok":row["ok"],"points":row.get("points"),"error":row.get("error")},separators=(",",":")),flush=True)
    return row
def main():
    p=argparse.ArgumentParser();p.add_argument("--manifest",required=True)
    p.add_argument("--mode",choices=["fresh14","warm7then14"],required=True)
    p.add_argument("--table",default="churn_ts");p.add_argument("--out",required=True)
    a=p.parse_args();a.out=str(pathlib.Path(a.out)/"report.json");m=json.loads(pathlib.Path(a.manifest).read_text())
    assert (m["sha256"],m["series"],m["days"])==(FIXTURE,1330000,14)
    report={"mode":a.mode,"fixture_sha":FIXTURE,"scope":"isolated native full PromQL RSS diagnostic",
            "steps":[],"source":"Same pinned O1 binary and fixture; no speedup claim"}
    save(a.out,report)
    try:
        if a.mode=="warm7then14":
            for i in range(4):
                one(a.table,m,7,f"warm7_{i}",report);save(a.out,report)
        one(a.table,m,14,"probe14",report)
    except Exception as e:report["error"]=str(e)[:1900]
    finally:
        report["completed"]=True
        report["passed"]=not report.get("error") and all(x.get("ok") for x in report["steps"])
        save(a.out,report)
        print("RSS_LIFETIME_DONE",json.dumps({"mode":a.mode,"passed":report["passed"],
            "probe14_ok":report["steps"][-1].get("ok") if report["steps"] else None},separators=(",",":")),flush=True)
    # A failing 14d query is an observed result, not an infrastructure/test failure.
    if report.get("error"):raise SystemExit(1)
if __name__=="__main__":main()
