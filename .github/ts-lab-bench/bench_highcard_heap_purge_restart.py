#!/usr/bin/env python3
"""1.33m TimeSeries: isolate live/retained/cached bytes with fresh-server A/B.

This collects evidence, never accepts OOM as query success, and keeps the
dataset, binary, VM instance and server 5GB limit unchanged.
"""
import argparse, json, os, pathlib, subprocess, threading, time, urllib.error, urllib.request
from bench_jenkins_querypath import post
from bench_bucket_cxx_abca import normalize_any
from bench_filtered_selector_v4_sql import sql_string

CH="http://127.0.0.1:18191"
METRIC="jenkins_ci_95k_daily"
DAY=86400

def sql(statement, query_id=None, timeout=240):
    t0=time.perf_counter()
    try:
        ms,raw=post(CH,statement,query_id)
        return {"ok":True,"ms":round(ms,3),"text":raw.decode("utf-8","replace")}
    except Exception as exc:
        msg=str(exc)
        return {"ok":False,"ms":round((time.perf_counter()-t0)*1000,3),
                "error":msg[:2800],"oom":"MEMORY_LIMIT_EXCEEDED" in msg}

def proc_tree(pid, detailed=True):
    out=[];pending=[pid];seen=set()
    while pending:
        current=pending.pop()
        if current in seen:continue
        seen.add(current)
        try:
            status=pathlib.Path(f"/proc/{current}/status").read_text()
            row={"pid":current,"comm":pathlib.Path(f"/proc/{current}/comm").read_text().strip()}
            for line in status.splitlines():
                for k in ("VmRSS","VmHWM","RssAnon","RssFile","VmSize","VmSwap","Threads"):
                    if line.startswith(k+":"):
                        row[k]=int(line.split()[1])
            if detailed:
                for line in pathlib.Path(f"/proc/{current}/smaps_rollup").read_text().splitlines():
                    if line.startswith(("Pss:","Private_Dirty:","Private_Clean:","Shared_Dirty:","Anonymous:")):
                        k,v=line.split(":",1)
                        row["smaps_"+k]=int(v.strip().split()[0])
            out.append(row)
            children=pathlib.Path(f"/proc/{current}/task/{current}/children")
            pending.extend(int(x) for x in children.read_text().split())
        except (OSError,ValueError):pass
    return out

def raw_summary(pid):
    members=proc_tree(pid)
    keys=["VmRSS","VmHWM","RssAnon","RssFile","smaps_Pss",
          "smaps_Private_Dirty","smaps_Anonymous"]
    return {"processes":members,**{k+"_kib":sum(p.get(k,0) for p in members) for k in keys}}

def jemalloc_metrics():
    try:
        r=sql("SELECT metric,value FROM system.asynchronous_metrics "
              "WHERE startsWith(metric,'jemalloc.') "
              "AND (metric IN ('jemalloc.allocated','jemalloc.active',"
              "'jemalloc.resident','jemalloc.mapped','jemalloc.retained',"
              "'jemalloc.fragmentation','jemalloc.metadata',"
              "'jemalloc.arenas.dirty_decay_ms')) FORMAT JSONEachRow")
        if not r["ok"]:return {"error":r["error"]}
        return {x["metric"]:x["value"] for line in r["text"].splitlines()
                if line.strip() for x in [json.loads(line)]}
    except Exception as exc:return {"error":str(exc)[:1500]}

def metrics():
    try:
        r=sql("SELECT metric,value FROM system.metrics WHERE "
              "metric IN ('MemoryTracking','Query','CachedReadBufferReadFromCacheBytes',"
              "'PrimaryIndexCacheBytes','MarkCacheBytes','UncompressedCacheBytes') "
              "FORMAT JSONEachRow")
        if r["ok"]:
            return {x["metric"]:x["value"] for line in r["text"].splitlines()
                    if line.strip() for x in [json.loads(line)]}
        return {"error":r["error"]}
    except Exception as exc:return {"error":str(exc)[:1500]}

def checkpoint(label,pid):
    time.sleep(2.0) # async jemalloc sampling normally updates once per second
    result={"name":label,"monotonic_ns":time.monotonic_ns(),
            "os":raw_summary(pid),"jemalloc":jemalloc_metrics(),"metrics":metrics()}
    print("MEMORY_OS_SNAPSHOT",json.dumps({"name":label,
          "rss_mb":round(result["os"]["VmRSS_kib"]/1024,2),
          "anon_mb":round(result["os"]["RssAnon_kib"]/1024,2),
          "jemalloc_alloc_mb":round(result["jemalloc"].get("jemalloc.allocated",0)/1e6,2),
          "jemalloc_resident_mb":round(result["jemalloc"].get("jemalloc.resident",0)/1e6,2)},
          separators=(",",":")),flush=True)
    return result

def monitored(statement,pid,qid,interval=0.2):
    stop=threading.Event();measure=[]
    def sampler():
        while not stop.is_set():
            procs=proc_tree(pid, detailed=False)
            measure.append({"ms":round(time.monotonic()*1000,1),
               "rss_kib":sum(x.get("VmRSS",0) for x in procs),
               "anon_kib":sum(x.get("RssAnon",0) for x in procs)})
            stop.wait(interval)
    worker=threading.Thread(target=sampler,daemon=True)
    worker.start()
    try:res=sql(statement,qid)
    finally:stop.set();worker.join(timeout=5)
    res["query_id"]=qid
    if res["ok"]:
        # response can be a large but bounded stage result
        res["result_points"]=len(normalize_any(res.pop("text").encode("utf-8")))
    res["rss_samples"]=measure
    res["peak_rss_kib"]=max((x["rss_kib"] for x in measure),default=0)
    return res

def restart(path,binpath,name):
    subprocess.run([str(path),str(binpath),name],check=True,timeout=180)
    time.sleep(.7)
    pid=int(pathlib.Path(os.environ["RUNNER_TEMP"],"ch.pid").read_text())
    return pid

def settings_available():
    r=sql("SELECT name FROM system.settings WHERE name IN "
          "('log_processors_profiles','query_profiler_cpu_time_period_ns',"
          "'query_profiler_real_time_period_ns','log_query_threads') FORMAT TSVRaw")
    return set(r["text"].splitlines()) if r["ok"] else set()

def main():
    ap=argparse.ArgumentParser()
    ap.add_argument("--manifest",required=True)
    ap.add_argument("--binary",required=True)
    ap.add_argument("--restart-helper",required=True)
    ap.add_argument("--output",required=True)
    a=ap.parse_args()
    meta=json.loads(pathlib.Path(a.manifest).read_text())
    assert (meta["series"],meta["per_day"],meta["days"])==(1330000,95000,14)
    run=os.getenv("GITHUB_RUN_ID","local")
    base=int(meta["base"])+DAY;end=int(meta["end"])
    expr=f"avg by(stage)(avg_over_time({METRIC}[1d]))"
    query=f"SELECT * FROM prometheusQueryRange(churn_ts,{sql_string(expr)},{base},{end},86400)"
    results={"source":meta,"run":run,"phases":[],"all_experiments_completed":False}

    # Begin with four independent cold-process experiments. File page cache
    # persists across the server restarts; process heap and caches do not.
    matrix=[("fresh_v4_t4",1,4,False),
            ("fresh_original_t4",0,4,False),
            ("fresh_v4_t1",1,1,False),
            ("fresh_v4_t4_profile",1,4,True)]
    for index,(name,enabled,threads,profile) in enumerate(matrix):
        pid=restart(a.restart_helper,a.binary,name)
        available=settings_available()
        opts=[f"max_threads={threads}",f"promql_enable_native_one_point_lab={enabled}"]
        if profile:
            for k,v in [("log_processors_profiles","1"),
                        ("log_query_threads","1"),
                        ("query_profiler_cpu_time_period_ns","5000000"),
                        ("query_profiler_real_time_period_ns","0")]:
                if k in available: opts.append(f"{k}={v}")
        statement=query+" SETTINGS "+",".join(opts)+" FORMAT JSONEachRow"
        phase={"name":name,"before":checkpoint(name+"_before",pid)}
        phase["query"]=monitored(statement,pid,f"heap95_{run}_{name}")
        phase["after"]=checkpoint(name+"_after",pid)
        phase["purge"]=sql("SYSTEM JEMALLOC PURGE")
        phase["after_purge"]=checkpoint(name+"_after_purge",pid)
        results["phases"].append(phase)
        print("MEMORY_FRESH_PHASE",json.dumps({
             "name":name,"ok":phase["query"]["ok"],
             "wall_ms":phase["query"]["ms"],"oom":phase["query"].get("oom",False),
             "sampled_peak_mb":round(phase["query"]["peak_rss_kib"]/1024,1),
             "rss_after_mb":round(phase["after"]["os"]["VmRSS_kib"]/1024,1),
             "rss_after_purge_mb":round(phase["after_purge"]["os"]["VmRSS_kib"]/1024,1),
             "jemalloc_alloc_after_mb":round(phase["after"]["jemalloc"].get("jemalloc.allocated",0)/1e6,1),
             "jemalloc_alloc_purge_mb":round(phase["after_purge"]["jemalloc"].get("jemalloc.allocated",0)/1e6,1)},
             separators=(",",":")),flush=True)

    # Repeat the stress sequence that previously failed at the end.
    for with_purge in (False,True):
        name="sequence_with_purge" if with_purge else "sequence_no_purge"
        pid=restart(a.restart_helper,a.binary,name)
        phase={"name":name,"snapshots":[checkpoint(name+"_baseline",pid)],"queries":[]}
        selector=("timeSeriesSelector('default','churn_ts',"
                  +sql_string(METRIC)+","
                  f"fromUnixTimestamp64Milli({meta['base']*1000+1}),"
                  f"fromUnixTimestamp64Milli({end*1000}),'samples')")
        preparation=[
            ("selector_count",f"SELECT count() FROM {selector} FORMAT TSV"),
            ("selector_length",f"SELECT sum(length(samples)) FROM {selector} FORMAT TSV"),
            ("three_day",f"SELECT count() FROM prometheusQueryRange(churn_ts,"
                f"{sql_string(expr)},{end-2*DAY},{end},86400) "
                "SETTINGS max_threads=1,promql_enable_native_one_point_lab=1 FORMAT TSV"),
            ("fourteen_day",f"SELECT count() FROM prometheusQueryRange(churn_ts,"
                f"{sql_string(expr)},{base},{end},86400) "
                "SETTINGS max_threads=1,promql_enable_native_one_point_lab=1 FORMAT TSV")]
        for k,(label,statement) in enumerate(preparation):
            record=sql(statement,f"heap95_{run}_{name}_{label}")
            phase["queries"].append({"name":label,"ok":record["ok"],
                         "wall_ms":record["ms"],"error":record.get("error","")[:350]})
            phase["snapshots"].append(checkpoint(name+"_"+label,pid))
        if with_purge:
            phase["purge_before_final"]=sql("SYSTEM JEMALLOC PURGE")
            phase["snapshots"].append(checkpoint(name+"_before_final_purged",pid))
        else:
            phase["snapshots"].append(checkpoint(name+"_before_final_no_purge",pid))
        statement=query+" SETTINGS max_threads=4,promql_enable_native_one_point_lab=1 FORMAT JSONEachRow"
        phase["final"]=monitored(statement,pid,f"heap95_{run}_{name}_final")
        phase["snapshots"].append(checkpoint(name+"_after_final",pid))
        results["phases"].append(phase)
        print("MEMORY_SEQUENCE_RESULT",json.dumps({
          "name":name,"final_ok":phase["final"]["ok"],
          "final_oom":phase["final"].get("oom",False),
          "final_wall_ms":phase["final"]["ms"],
          "final_peak_mb":round(phase["final"]["peak_rss_kib"]/1024,1)},
          separators=(",",":")),flush=True)

    # Persist query logs including failed queries; a failed query does not have QueryFinish.
    sql("SYSTEM FLUSH LOGS")
    q=sql("SELECT query_id,type,exception_code,exception,query_duration_ms,"
          "read_rows,read_bytes,memory_usage,ProfileEvents "
          "FROM system.query_log WHERE startsWith(query_id,"
          f"{sql_string('heap95_'+run+'_')}) FORMAT JSONEachRow")
    if q["ok"]:
        results["query_log"]=[json.loads(x) for x in q.pop("text").splitlines() if x.strip()]
    else:results["querylog_error"]=q
    # Collect trace and processor rows from the profiled query before server restart data is lost.
    for kind,querystr in {
        "processor":("SELECT query_id,name,sum(elapsed_us) AS elapsed_us,"
             "sum(input_wait_elapsed_us) AS input_wait_us,"
             "sum(output_wait_elapsed_us) AS output_wait_us,"
             "sum(input_rows) AS input_rows,sum(output_rows) AS output_rows "
             "FROM system.processors_profile_log "
             f"WHERE startsWith(query_id,'heap95_{run}_') "
             "GROUP BY query_id,name ORDER BY query_id,elapsed_us DESC "
             "FORMAT JSONEachRow"),
        "cpu":("SELECT query_id,demangle(addressToSymbol(arrayElement(trace,1))) "
            "AS leaf,count() AS n FROM system.trace_log "
            f"WHERE startsWith(query_id,'heap95_{run}_') AND trace_type='CPU' "
            "GROUP BY query_id,leaf ORDER BY query_id,n DESC LIMIT 200 "
            "SETTINGS allow_introspection_functions=1 FORMAT JSONEachRow")
        }.items():
        q=sql(querystr)
        if q["ok"]:
            try:results[kind]=[json.loads(x) for x in q["text"].splitlines() if x.strip()]
            except Exception as exc:results[kind+"_error"]=str(exc)[:2000]
        else:results[kind+"_error"]=q
    results["all_experiments_completed"]=True
    pathlib.Path(a.output).write_text(json.dumps(results,indent=2)+"\n")
    print("MEMORY_HEAP_RESTART_DONE",json.dumps({
       "phases":len(results["phases"]),
       "querylog_rows":len(results.get("query_log",[])),
       "cpu_samples":len(results.get("cpu",[])),
       "processor_rows":len(results.get("processor",[])),
       "all_experiments_completed":True},separators=(",",":")),flush=True)

if __name__=="__main__":main()
