#!/usr/bin/env python3
"""Jemalloc sampled allocations and CPU trace for 1.33m TimeSeries 14d workload.

Runs each mode in a fresh server process. Allocation sampling is intentionally
separate from unprofiled latency measurements; it may change timing/memory.
"""
import argparse,json,os,pathlib,subprocess,time
from bench_bucket_cxx_abca import normalize_any
from bench_jenkins_querypath import post
from bench_filtered_selector_v4_sql import sql_string

CH="http://127.0.0.1:18191"
METRIC="jenkins_ci_95k_daily"
DAY=86400

def query(sql,qid=None):
    start=time.perf_counter()
    try:
        elapsed,raw=post(CH,sql,qid)
        return {"ok":True,"ms":round(elapsed,3),"raw":raw.decode("utf-8","replace")}
    except Exception as exc:
        err=str(exc)
        return {"ok":False,"ms":round((time.perf_counter()-start)*1000,3),
                "error":err[:2600],"oom":"MEMORY_LIMIT_EXCEEDED" in err}

def get_stats(sql):
    record=query(sql)
    if record["ok"]:
        return [json.loads(x) for x in record["raw"].splitlines() if x.strip()]
    return {"error":record["error"]}

def restart(helper,binary,phase):
    subprocess.run([str(helper),str(binary),phase],check=True,timeout=180)
    return int(pathlib.Path(os.environ["RUNNER_TEMP"],"ch.pid").read_text())

def read_rss(pid):
    pids=[pid]
    seen=set();vals={}
    while pids:
        current=pids.pop()
        if current in seen:continue
        seen.add(current)
        try:
            status=pathlib.Path(f"/proc/{current}/status").read_text()
            for key in ("VmRSS","VmHWM","RssAnon"):
                for line in status.splitlines():
                    if line.startswith(key+":"):
                        vals[key]=vals.get(key,0)+int(line.split()[1])
            pids.extend(int(x) for x in pathlib.Path(
                f"/proc/{current}/task/{current}/children").read_text().split())
        except (OSError,ValueError):continue
    return vals

def main():
    p=argparse.ArgumentParser()
    p.add_argument("--manifest",required=True)
    p.add_argument("--binary",required=True)
    p.add_argument("--restart-helper",required=True)
    p.add_argument("--output",required=True)
    a=p.parse_args()
    manifest=json.loads(pathlib.Path(a.manifest).read_text())
    assert manifest["series"]==1330000
    run=os.getenv("GITHUB_RUN_ID","local")
    begin=int(manifest["base"])+DAY;end=int(manifest["end"])
    expr=f"avg by(stage)(avg_over_time({METRIC}[1d]))"
    promql=f"SELECT * FROM prometheusQueryRange(churn_ts,{sql_string(expr)},{begin},{end},86400)"
    selector=(f"SELECT count() FROM timeSeriesSelector('default','churn_ts',"
              f"{sql_string(METRIC)},fromUnixTimestamp64Milli({manifest['base']*1000+1}),"
              f"fromUnixTimestamp64Milli({end*1000}),'samples')")
    result={"fixture_sha256":manifest["sha256"],"queries":[],"trace":[],"log":[]}
    for phase,body,threads in (
        ("selector","selector",4),
        ("full_v4_one_thread","promql",1),
        ("full_v4_four_threads","promql",4)):
        pid=restart(a.restart_helper,a.binary,"alloc_"+phase)
        supported=query("SELECT name FROM system.settings WHERE name IN ("
            "'jemalloc_enable_profiler','jemalloc_collect_profile_samples_in_trace_log',"
            "'memory_profiler_step','max_untracked_memory',"
            "'query_profiler_cpu_time_period_ns','log_processors_profiles') FORMAT TSVRaw")
        assert supported["ok"],supported
        names=set(supported["raw"].splitlines())
        required={"jemalloc_enable_profiler","jemalloc_collect_profile_samples_in_trace_log"}
        if not required<=names:
            raise AssertionError(("per-query jemalloc collector unavailable",names))
        opts=[f"max_threads={threads}",
              "jemalloc_enable_profiler=1",
              "jemalloc_collect_profile_samples_in_trace_log=1"]
        for key,value in (("memory_profiler_step","8388608"),
                          ("query_profiler_cpu_time_period_ns","10000000"),
                          ("log_processors_profiles","1")):
            if key in names:opts.append(f"{key}={value}")
        if body=="selector":
            statement=selector+" SETTINGS "+",".join(opts)+" FORMAT TSV"
        else:
            statement=promql+" SETTINGS promql_enable_native_one_point_lab=1,"+(",".join(opts))+" FORMAT JSONEachRow"
        qid=f"alloc95_{run}_{phase}"
        before=read_rss(pid)
        rec=query(statement,qid)
        rec["query_id"]=qid
        rec["phase"]=phase
        rec["rss_before"]=before
        rec["rss_after"]=read_rss(pid)
        if rec["ok"]:
            if body=="selector":
                rec["selector_count"]=rec.pop("raw").strip()
            else:
                rec["result_points"]=len(normalize_any(rec.pop("raw").encode()))
        print("ALLOC_PROFILE_QUERY",json.dumps({
             "phase":phase,"ok":rec["ok"],"duration_ms":rec["ms"],
             "rss_before":before,"rss_after":rec["rss_after"],
             "oom":rec.get("oom",False),
             "error":rec.get("error","")[:500]},separators=(",",":")),flush=True)
        query("SYSTEM FLUSH LOGS")
        # Each phase uses an independently restarted server; query logs remain on disk.
        # Only save the columns that are relevant to attribution.
        qidlit=sql_string(qid)
        trace_cols=get_stats("SELECT name,type FROM system.columns "
            "WHERE database='system' AND table='trace_log' "
            "AND name IN ('size','ptr','trace_type','trace','query_id') "
            "FORMAT JSONEachRow")
        result["trace_columns"]=trace_cols
        sample_query=(
            "SELECT trace_type,demangle(addressToSymbol(arrayElement(trace,1))) AS leaf,"
            "count() AS samples,sum(size) AS summed_sample_size "
            "FROM system.trace_log "
            f"WHERE query_id={qidlit} AND trace_type IN ('JemallocSample','MemorySample') "
            "GROUP BY trace_type,leaf ORDER BY abs(summed_sample_size) DESC "
            "LIMIT 75 SETTINGS allow_introspection_functions=1 FORMAT JSONEachRow"
        )
        cpu_query=(
            "SELECT demangle(addressToSymbol(arrayElement(trace,1))) AS leaf,"
            "count() AS cpu_samples FROM system.trace_log "
            f"WHERE query_id={qidlit} AND trace_type='CPU' "
            "GROUP BY leaf ORDER BY cpu_samples DESC LIMIT 75 "
            "SETTINGS allow_introspection_functions=1 FORMAT JSONEachRow"
        )
        inclusive=(
            "SELECT demangle(addressToSymbol(arrayJoin(trace))) AS frame,"
            "count() AS cpu_inclusive_frames FROM system.trace_log "
            f"WHERE query_id={qidlit} AND trace_type='CPU' "
            "GROUP BY frame ORDER BY cpu_inclusive_frames DESC LIMIT 70 "
            "SETTINGS allow_introspection_functions=1 FORMAT JSONEachRow"
        )
        for typ,statement in (("heap",sample_query),("cpu_leaf",cpu_query),
                              ("cpu_inclusive",inclusive)):
            found=get_stats(statement)
            result["trace"].append({"phase":phase,"kind":typ,"data":found})
            if isinstance(found,list):
                print("ALLOC_PROFILE_TOP",json.dumps({"phase":phase,
                    "kind":typ,"top":found[:7]},separators=(",",":")),flush=True)
            else:
                print("ALLOC_PROFILE_ERROR",json.dumps({
                    "phase":phase,"kind":typ,"error":found},separators=(",",":")),flush=True)
        qlog=get_stats(
            "SELECT query_id,type,exception_code,memory_usage,read_rows,read_bytes,"
            "query_duration_ms,ProfileEvents FROM system.query_log "
            f"WHERE query_id={qidlit} FORMAT JSONEachRow")
        result["log"].append({"phase":phase,"query":qlog})
        if isinstance(qlog,list):
            print("ALLOC_PROFILE_QUERY_LOG",json.dumps({"phase":phase,
                "log":[{"type":r["type"],"memory":r["memory_usage"],
                         "read_rows":r["read_rows"],
                         "err":r["exception_code"]} for r in qlog]},
                separators=(",",":")),flush=True)
        result["queries"].append(rec)
    result["complete"]=True
    pathlib.Path(a.output).write_text(json.dumps(result,indent=2)+"\n")
    print("ALLOC_PROFILE_COMPLETE",json.dumps({"phases":len(result["queries"]),
       "traces":len(result["trace"]),"complete":True},
       separators=(",",":")),flush=True)
if __name__=="__main__":main()
