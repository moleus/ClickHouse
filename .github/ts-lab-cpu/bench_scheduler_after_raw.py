#!/usr/bin/env python3
"""Native TimeSeries query scheduler and lock events AFTER high-churn raw UUID ID optimization.

Query-side OSCPUWaitMicroseconds is genuine runnable-to-CPU scheduler wait,
NOT futex/mutex waiting. Thread-pool/parts/context lock wait ProfileEvents
are explicit ClickHouse instrumentation. All tests same pinned O1 binary,
same frozen 1.33m IDs, exact native PromQL output. A/B/B/A max_threads.
"""
import argparse,collections,json,os,pathlib,statistics,sys
sys.path[:0]=[".github/ts-lab-perf",".github/ts-lab-bench"]
from bench_granularity_v11 import sql,querylogs,streamed_insert,optimize_table,wait_no_merges,inner
from bench_bucket_cxx_abca import post,normalize_any,assert_same
from bench_filtered_selector_v4_sql import sql_string
SHA="8f3a36846f80d6be5a8604d142e934399f2687094766c57bf105e43977f52858"
SELS={
 "stage":('{stage="stage-0007"}',14,1),
 "project":('{project="project-00"}',14,19),
 "whole":("",1,38)
}
EVENTS=[
 "OSCPUVirtualTimeMicroseconds","OSCPUWaitMicroseconds",
 "GlobalThreadPoolLockWaitMicroseconds","LocalThreadPoolLockWaitMicroseconds",
 "ContextLockWaitMicroseconds","PartsLockWaitMicroseconds",
 "SharedPartsLockWaitMicroseconds","PartsLockHoldMicroseconds",
 "SharedPartsLockHoldMicroseconds"
]
def save(file,x):
    p=pathlib.Path(file);p.parent.mkdir(parents=True,exist_ok=True)
    p.write_text(json.dumps(x,indent=2,default=str)+"\n")
def rows(q):
    return [json.loads(line) for line in sql(q+" FORMAT JSONEachRow").splitlines() if line.strip()]
def profile(ids):
    sql("SYSTEM FLUSH LOGS")
    quoted="("+",".join(sql_string(x) for x in ids)+")"
    result={}
    query_map={
       "threads":"SELECT query_id,thread_id,thread_name,"
           "ProfileEvents['OSCPUVirtualTimeMicroseconds'] AS cpu_us,"
           "ProfileEvents['OSCPUWaitMicroseconds'] AS runnable_us,"
           "ProfileEvents['GlobalThreadPoolLockWaitMicroseconds'] AS global_pool_lock_us,"
           "ProfileEvents['LocalThreadPoolLockWaitMicroseconds'] AS local_pool_lock_us "
           "FROM system.query_thread_log WHERE query_id IN "+quoted+
           " ORDER BY query_id,cpu_us DESC LIMIT 100 BY query_id",
       "operators":"SELECT query_id,plan_step_name,name,count() AS n,"
           "sum(elapsed_us) AS operator_work_us,"
           "sum(input_wait_elapsed_us) AS operator_input_wait_us,"
           "sum(output_wait_elapsed_us) AS operator_output_wait_us,"
           "sum(input_rows) AS input_rows,sum(output_rows) AS output_rows "
           "FROM system.processors_profile_log WHERE query_id IN "+quoted+
           " GROUP BY query_id,plan_step_name,name ORDER BY query_id,operator_work_us DESC "
           "LIMIT 80 BY query_id",
       "lock_trace":"SELECT query_id,trace_type,arrayFirst(s -> s != '',symbols) AS leaf,"
           "count() AS samples,sum(size) AS allocated_bytes "
           "FROM system.trace_log WHERE query_id IN "+quoted+
           " AND trace_type IN ('CPU','Memory') "
           "GROUP BY query_id,trace_type,leaf "
           "ORDER BY query_id,samples DESC LIMIT 100 BY query_id"
    }
    for key,q in query_map.items():
        try:result[key]=rows(q)
        except Exception as exc:result[key+"_error"]=str(exc)[:1600]
    return result
def expression(case,finish,threads):
    selector,days,_=SELS[case];begin=finish-(days-1)*86400
    exp=f"avg by(stage)(avg_over_time(jenkins_ci_95k_daily{selector}[1d]))"
    return (f"SELECT * FROM prometheusQueryRange(churn_scheduler_rawid,{sql_string(exp)},"
            f"{begin},{finish},86400) SETTINGS max_threads={threads},"
            "max_memory_usage=3500000000,log_queries=1,"
            "log_processors_profiles=1,query_profiler_cpu_time_period_ns=1000000 "
            "FORMAT JSONEachRow")
def main():
    ap=argparse.ArgumentParser()
    ap.add_argument("--case",choices=SELS,required=True)
    ap.add_argument("--manifest",required=True)
    ap.add_argument("--fixture",required=True)
    ap.add_argument("--output",required=True)
    args=ap.parse_args()
    manifest=json.loads(pathlib.Path(args.manifest).read_text())
    assert manifest["sha256"]==SHA and manifest["series"]==1330000
    days,expected_groups=SELS[args.case][1:]
    finish=int(manifest["end"]);begin=finish-(days-1)*86400
    ticks={begin+86400*i for i in range(days)}
    results={"case":args.case,"source_sha":SHA,"days":days,"comparisons":{},"errors":[]}
    save(args.output,results)
    reference=None
    prefix=f"builtinlock_{args.case}_{os.getenv('GITHUB_RUN_ID','local')}_"
    try:
        # Compare query parallelism after the RAW_ID optimization, without
        # changing binaries/flags/source between phases.
        sql("CREATE TABLE churn_scheduler_rawid ENGINE=TimeSeries "
            "SETTINGS recent_samples_ttl_seconds=0 "
            "SAMPLES INNER COLUMNS (id Tuple(UInt64, UUID)) "
            "TAGS INNER COLUMNS (id Tuple(UInt64, UUID) DEFAULT "
            "tuple(sipHash64(metric_name),reinterpretAsUUID(sipHash128(tags))), "
            "metric_name LowCardinality(String), "
            "tags Map(LowCardinality(String), String), "
            "INDEX tags_idx tags TYPE text(tokenizer = 'keyValuePairs') GRANULARITY 100000000) "
            "TIME RANGES INNER COLUMNS (id Tuple(UInt64, UUID))")
        ingested=streamed_insert("churn_scheduler_rawid",args.fixture)
        if ingested["rows"]!=1330000:raise AssertionError("Incomplete RAW_ID ingest")
        results["ingest"]=ingested
        results["merge_optimization"]=optimize_table("churn_scheduler_rawid")
        wait_no_merges()
        sql("SYSTEM STOP MERGES")
        tags=inner("churn_scheduler_rawid","tags")
        count=rows("SELECT count() AS n,countIf(tags['stage']='stage-0007') AS stage FROM "+tags)[0]
        if count["n"]!=1330000 or count["stage"]!=35000:raise AssertionError("Bad raw-ID tag counts")
        results["tag_count"]=count
        save(args.output,results)
        for target in (1,2,8):
            blocks=[]
            results["comparisons"][str(target)]={"phases":blocks}
            for phase,threads in enumerate((4,target,target,4)):
                stmt=expression(args.case,finish,threads)
                trials=[]
                for i in range(5):
                    qid=f"{prefix}{target}_{phase}_{i}"
                    wall,raw=post("http://127.0.0.1:18191",stmt,qid)
                    data=normalize_any(raw)
                    if len(data)!=days*expected_groups or {t for _,t in data}!=ticks:
                        raise AssertionError(("Wrong output",args.case,threads,len(data),ticks))
                    if reference is None:reference=data
                    else:assert_same(reference,data,args.case,"builtin-lock")
                    trials.append({"qid":qid,"client_ms":round(wall,3),"warmup":i<2})
                blocks.append({"phase":phase,"max_threads":threads,"trials":trials})
                save(args.output,results)
                print("BUILTIN_LOCK_PHASE",json.dumps({"case":args.case,"target":target,
                      "phase":phase,"threads":threads,"last_ms":trials[-1]["client_ms"]}),flush=True)
            logfile={x["query_id"]:x for x in querylogs(prefix+str(target)+"_")
                     if x.get("type")=="QueryFinish"}
            for phase in blocks:
                for trial in phase["trials"]:
                    row=logfile.get(trial["qid"])
                    if row is None:raise AssertionError("Missing QueryFinish "+trial["qid"])
                    ev=row.get("ProfileEvents",{})
                    trial.update({"server_wall_ms":row["query_duration_ms"],
                                  "read_rows":row["read_rows"],"read_bytes":row["read_bytes"],
                                  "peak_query_memory":row["memory_usage"],
                                  "events_us":{key:int(ev.get(key,0)) for key in EVENTS}})
                vals=[x for x in phase["trials"] if not x["warmup"]]
                phase["median_wall_ms"]=statistics.median(x["client_ms"] for x in vals)
                phase["median_events_us"]={key:statistics.median(x["events_us"][key] for x in vals)
                                           for key in EVENTS}
                phase["max_query_memory"]=max(x["peak_query_memory"] for x in vals)
            baseline=[x for x in blocks if x["max_threads"]==4]
            candidate=[x for x in blocks if x["max_threads"]==target]
            bwall=statistics.mean(x["median_wall_ms"] for x in baseline)
            cwall=statistics.mean(x["median_wall_ms"] for x in candidate)
            drift=abs(baseline[0]["median_wall_ms"]-baseline[1]["median_wall_ms"])/bwall
            average=lambda phases,key: statistics.mean(x["median_events_us"][key] for x in phases)
            summary={"baseline_threads":4,"alternative_threads":target,
                     "baseline_wall_ms":round(bwall,3),
                     "alternative_wall_ms":round(cwall,3),
                     "alternative_vs_baseline_wall_ratio":round(cwall/bwall,4),
                     "baseline_cpu_ms":round(average(baseline,"OSCPUVirtualTimeMicroseconds")/1000,3),
                     "alternative_cpu_ms":round(average(candidate,"OSCPUVirtualTimeMicroseconds")/1000,3),
                     "baseline_runnable_wait_ms":round(average(baseline,"OSCPUWaitMicroseconds")/1000,3),
                     "alternative_runnable_wait_ms":round(average(candidate,"OSCPUWaitMicroseconds")/1000,3),
                     "baseline_global_pool_lock_wait_ms":round(average(baseline,"GlobalThreadPoolLockWaitMicroseconds")/1000,3),
                     "alternative_global_pool_lock_wait_ms":round(average(candidate,"GlobalThreadPoolLockWaitMicroseconds")/1000,3),
                     "baseline_local_pool_lock_wait_ms":round(average(baseline,"LocalThreadPoolLockWaitMicroseconds")/1000,3),
                     "alternative_local_pool_lock_wait_ms":round(average(candidate,"LocalThreadPoolLockWaitMicroseconds")/1000,3),
                     "baseline_context_lock_wait_ms":round(average(baseline,"ContextLockWaitMicroseconds")/1000,3),
                     "alternative_context_lock_wait_ms":round(average(candidate,"ContextLockWaitMicroseconds")/1000,3),
                     "baseline_query_mem_bytes":max(x["max_query_memory"] for x in baseline),
                     "alternative_query_mem_bytes":max(x["max_query_memory"] for x in candidate),
                     "baseline_drift":round(drift,4),"valid":drift<=0.25}
            qids=[blocks[0]["trials"][2]["qid"],blocks[1]["trials"][2]["qid"]]
            results["comparisons"][str(target)].update({"summary":summary,"sampled_profiles":profile(qids)})
            if drift>.25:results["errors"].append({"target":target,"control_drift":drift})
            save(args.output,results)
            print("BUILTIN_LOCK_COMPARE",json.dumps({"case":args.case,**summary},separators=(",",":")),flush=True)
        results["passed"]=not results["errors"]
    except Exception as exc:
        results["errors"].append({"fatal":str(exc)[:1800]});results["passed"]=False
    finally:
        save(args.output,results)
        compact={"case":args.case,"passed":results["passed"],"errors":results["errors"],
                 "comparisons":{k:v.get("summary") for k,v in results["comparisons"].items()}}
        save(str(pathlib.Path(args.output).with_suffix(".compact.json")),compact)
        print("BUILTIN_LOCK_DONE",json.dumps(compact,separators=(",",":")),flush=True)
    if not results["passed"]:raise SystemExit(1)
if __name__=="__main__":main()
