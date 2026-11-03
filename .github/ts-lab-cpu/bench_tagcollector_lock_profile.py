#!/usr/bin/env python3
"""Native PromQL exact lock wait/hold attribution: instrumented A/B/B/A.

Both O1 binaries have identical ProfileEvents probes and differ only by
moving pending TagsKey hash computation outside the collector exclusive lock.
Controls are fresh-process A/B/B/A per max_threads. Same frozen v11 data,
ID generator, source SHA, settings, PromQL semantics, compiler and ABI.
"""
import argparse,collections,json,os,pathlib,statistics,subprocess,sys
sys.path[:0]=[".github/ts-lab-perf",".github/ts-lab-bench"]
from bench_granularity_v11 import querylogs,sql
from bench_bucket_cxx_abca import post,normalize_any,assert_same
from bench_filtered_selector_v4_sql import sql_string

SHA="8f3a36846f80d6be5a8604d142e934399f2687094766c57bf105e43977f52858"
HOST="http://127.0.0.1:18191"
SELS={"stage":('{stage="stage-0007"}',1,14),
      "project":('{project="project-00"}',19,14),
      "whole":("",38,1)}
EVENTS=[
 "TimeSeriesCollectorReadLockWaitNanoseconds",
 "TimeSeriesCollectorReadLockHoldNanoseconds",
 "TimeSeriesCollectorWriteLockWaitNanoseconds",
 "TimeSeriesCollectorWriteLockHoldNanoseconds",
 "TimeSeriesCollectorWriteAcquisitions",
 "TimeSeriesCollectorPendingIDs"
]
def save(p,x):
    f=pathlib.Path(p);f.parent.mkdir(parents=True,exist_ok=True)
    f.write_text(json.dumps(x,indent=2,sort_keys=True,default=str)+"\n")
def median(xs):return statistics.median(xs)
def mean(xs):return statistics.mean(xs)
def make_sql(case,days,end,threads):
    sel,_,_=SELS[case]
    start=end-(days-1)*86400
    expr=f"avg by(stage)(avg_over_time(jenkins_ci_95k_daily{sel}[1d]))"
    return (f"SELECT * FROM prometheusQueryRange(churn_ts,{sql_string(expr)},"
            f"{start},{end},86400) SETTINGS max_threads={threads},"
            "max_memory_usage=3500000000,log_queries=1,log_processors_profiles=1,"
            "query_profiler_cpu_time_period_ns=1000000 FORMAT JSONEachRow")
def query_log(qids):
    out={x["query_id"]:x for x in querylogs("taglock_profile_") if x.get("type")=="QueryFinish"}
    missing=[x for x in qids if x not in out]
    if missing:raise AssertionError(("Missing QueryFinish",missing))
    return out
def profiles(qids):
    sql("SYSTEM FLUSH LOGS")
    ids="("+",".join(sql_string(q) for q in qids)+")"
    out={}
    queries={
        "processors":"SELECT query_id,name,plan_step_name,sum(elapsed_us) AS work_us,"
                     "sum(input_wait_elapsed_us) AS input_wait_us,"
                     "sum(output_wait_elapsed_us) AS output_wait_us,"
                     "sum(input_rows) AS rows_in,sum(output_rows) AS rows_out "
                     "FROM system.processors_profile_log WHERE query_id IN "+ids+
                     " GROUP BY query_id,name,plan_step_name ORDER BY query_id,work_us DESC "
                     "LIMIT 150 BY query_id",
        "cpu_area":"SELECT query_id,multiIf("
                   "arrayExists(s -> position(s,'ContextTimeSeriesTagsCollector')>0,symbols),'collector',"
                   "arrayExists(s -> position(s,'ReverseIndex')>0 OR position(s,'ColumnUnique')>0,symbols),'lc_dictionary',"
                   "arrayExists(s -> position(s,'MergeTreeReader')>0,symbols),'reader',"
                   "arrayExists(s -> position(s,'Aggregator')>0,symbols),'aggregation','other') AS area,"
                   "count() AS samples FROM system.trace_log WHERE query_id IN "+ids+
                   " AND trace_type='CPU' GROUP BY query_id,area ORDER BY query_id,samples DESC",
        "cpu_hot":"SELECT query_id,arrayFirst(s -> s!='',symbols) AS leaf,"
                  "count() AS samples FROM system.trace_log WHERE query_id IN "+ids+
                  " AND trace_type='CPU' GROUP BY query_id,leaf "
                  "ORDER BY query_id,samples DESC LIMIT 150 BY query_id",
        "thread":"SELECT query_id,countDistinct(thread_id) AS thread_count,"
                 "sum(ProfileEvents['OSCPUVirtualTimeMicroseconds']) AS cpu_us "
                 "FROM system.query_thread_log WHERE query_id IN "+ids+" GROUP BY query_id"
    }
    for key,q in queries.items():
        try:out[key]=[json.loads(l) for l in sql(q+" FORMAT JSONEachRow").splitlines() if l.strip()]
        except Exception as e:out[key+"_error"]=str(e)[:1500]
    return out
def main():
    p=argparse.ArgumentParser()
    p.add_argument("--case",choices=SELS,required=True)
    for k in ("manifest","baseline-bin","patched-bin","restart-helper","output"):p.add_argument("--"+k,required=True)
    p.add_argument("--threads",default="1,4,8")
    a=p.parse_args()
    m=json.loads(pathlib.Path(a.manifest).read_text())
    assert m.get("sha256")==SHA and m.get("series")==1330000
    cores=[int(x) for x in a.threads.split(",")]
    assert cores and len(cores)==len(set(cores)) and all(x in (1,2,4,8) for x in cores)
    selector,groups,days=SELS[a.case]
    end=int(m["end"]); start=end-(days-1)*86400
    expected={start+i*86400 for i in range(days)}
    record={"case":a.case,"days":days,"source_sha":SHA,
            "optimization":"O1 pinned with common JOIN override and identical lock counters",
            "threads":cores,"cases":{},"errors":[]}
    save(a.output,record)
    reference=None
    try:
        for threads in cores:
            phases=[]
            key=str(threads)
            record["cases"][key]={"phases":phases}
            q=make_sql(a.case,days,end,threads)
            for phase,(label,binary) in enumerate((
                    ("baseline",a.baseline_bin),("prehash",a.patched_bin),
                    ("prehash",a.patched_bin),("baseline",a.baseline_bin))):
                subprocess.run([a.restart_helper,binary,f"taglock_{a.case}_{threads}_{phase}"],
                               check=True,timeout=180)
                prefix=f"taglock_profile_{a.case}_{threads}_{phase}_{os.getenv('GITHUB_RUN_ID','local')}"
                trials=[]
                for k in range(5):
                    qid=prefix+"_"+str(k)
                    ms,body=post(HOST,q,qid)
                    got=normalize_any(body)
                    if len(got)!=groups*days or {timestamp for (_tags,timestamp) in got}!=expected:
                        raise AssertionError(("Output cardinality or times changed",label,threads,len(got)))
                    if reference is None:reference=got
                    else:assert_same(reference,got,a.case,label)
                    trials.append({"qid":qid,"wall_ms":round(ms,3),"warmup":k<2})
                logs=query_log([x["qid"] for x in trials])
                for t in trials:
                    row=logs[t["qid"]];ev=row.get("ProfileEvents",{})
                    for x in EVENTS:t[x]=int(ev.get(x,0))
                    t.update({"server_wall_ms":row["query_duration_ms"],
                              "cpu_ms":round(ev.get("OSCPUVirtualTimeMicroseconds",0)/1000,3),
                              "memory_bytes":row["memory_usage"],
                              "read_rows":row["read_rows"],"read_bytes":row["read_bytes"],
                              "selected_marks":ev.get("SelectedMarks")})
                measured=[x for x in trials if not x["warmup"]]
                phase_entry={"variant":label,"phase":phase,"trials":trials,
                             "median_wall_ms":median(x["wall_ms"] for x in measured),
                             "median_cpu_ms":median(x["cpu_ms"] for x in measured),
                             "median_wait_read_ms":median(x[EVENTS[0]]/1e6 for x in measured),
                             "median_hold_read_ms":median(x[EVENTS[1]]/1e6 for x in measured),
                             "median_wait_write_ms":median(x[EVENTS[2]]/1e6 for x in measured),
                             "median_hold_write_ms":median(x[EVENTS[3]]/1e6 for x in measured),
                             "median_write_acquisitions":median(x[EVENTS[4]] for x in measured),
                             "median_pending_ids":median(x[EVENTS[5]] for x in measured),
                             "max_query_memory_bytes":max(x["memory_bytes"] for x in measured)}
                if not any(x["TimeSeriesCollectorWriteAcquisitions"]>0 for x in trials):
                    raise AssertionError("Expected collector registration events missing: "+label)
                phases.append(phase_entry)
                save(a.output,record)
                print("TAGLOCK_PHASE",json.dumps({"case":a.case,"threads":threads,"phase":phase,
                    "variant":label,"wall_ms":phase_entry["median_wall_ms"],
                    "cpu_ms":phase_entry["median_cpu_ms"],
                    "write_wait_ms":phase_entry["median_wait_write_ms"],
                    "write_hold_ms":phase_entry["median_hold_write_ms"],
                    "pending_ids":phase_entry["median_pending_ids"]}),flush=True)
            aa=[x for x in phases if x["variant"]=="baseline"]
            bb=[x for x in phases if x["variant"]=="prehash"]
            abase=mean(x["median_wall_ms"] for x in aa)
            btest=mean(x["median_wall_ms"] for x in bb)
            drift=abs(aa[0]["median_wall_ms"]-aa[-1]["median_wall_ms"])/abase
            summary={
                "threads":threads,
                "baseline_wall_ms":round(abase,3),"prehash_wall_ms":round(btest,3),
                "latency_speedup":round(abase/btest,4),
                "baseline_cpu_ms":round(mean(x["median_cpu_ms"] for x in aa),3),
                "prehash_cpu_ms":round(mean(x["median_cpu_ms"] for x in bb),3),
                "baseline_exclusive_wait_ms":round(mean(x["median_wait_write_ms"] for x in aa),3),
                "prehash_exclusive_wait_ms":round(mean(x["median_wait_write_ms"] for x in bb),3),
                "baseline_exclusive_hold_ms":round(mean(x["median_hold_write_ms"] for x in aa),3),
                "prehash_exclusive_hold_ms":round(mean(x["median_hold_write_ms"] for x in bb),3),
                "baseline_shared_wait_ms":round(mean(x["median_wait_read_ms"] for x in aa),3),
                "prehash_shared_wait_ms":round(mean(x["median_wait_read_ms"] for x in bb),3),
                "baseline_shared_hold_ms":round(mean(x["median_hold_read_ms"] for x in aa),3),
                "prehash_shared_hold_ms":round(mean(x["median_hold_read_ms"] for x in bb),3),
                "baseline_pending_ids":mean(x["median_pending_ids"] for x in aa),
                "prehash_pending_ids":mean(x["median_pending_ids"] for x in bb),
                "baseline_memory_peak_bytes":max(x["max_query_memory_bytes"] for x in aa),
                "prehash_memory_peak_bytes":max(x["max_query_memory_bytes"] for x in bb),
                "drift":round(drift,4),"valid":drift<=.25}
            if drift>.25:record["errors"].append({"threads":threads,"baseline_drift":drift})
            record["cases"][key]["summary"]=summary
            qids=[phases[0]["trials"][2]["qid"],phases[1]["trials"][2]["qid"]]
            record["cases"][key]["profiles"]=profiles(qids)
            save(a.output,record)
            print("TAGLOCK_COMPARE",json.dumps({"case":a.case,**summary},separators=(",",":")),flush=True)
        record["passed"]=not record["errors"]
    except Exception as exc:
        record["errors"].append({"fatal":str(exc)[:1800]});record["passed"]=False
    finally:
        save(a.output,record)
        compact={"case":a.case,"passed":record["passed"],"errors":record["errors"],
                 "threads":{k:v.get("summary") for k,v in record["cases"].items()}}
        save(str(pathlib.Path(a.output).with_suffix(".compact.json")),compact)
        print("TAGLOCK_DONE",json.dumps(compact,separators=(",",":")),flush=True)
    if not record["passed"]:raise SystemExit(1)
if __name__=="__main__":main()
