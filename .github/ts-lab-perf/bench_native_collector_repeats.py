#!/usr/bin/env python3
"""Actual native stateful ClickHouse tags collector on generated UInt64 IDs.

Independent fresh-process trials. No data imports, no C++ rebuild, no surrogate map.
All conflicting tags, absent-state, and other ID-type correctness remain untested.
"""
import argparse,json,os,pathlib,random,statistics,sys,time
sys.path.insert(0,'.github/ts-lab-bench')
from bench_collector_tag_projection import CH,sql,server_restart,run_monitored,proc_rss

N=1330000
FRACTIONS=[("f100",1330000),("f050",665000),("f010",133000),("f001",13300)]
THREADS=(1,4)
BLOCKS=(8192,65536)
RUN=os.environ.get("GITHUB_RUN_ID","local")
def save(path,d):
    pathlib.Path(path).write_text(json.dumps(d,indent=2)+"\n")
def make_query(unique,threads,block,control=False,rows=N):
    id_expr=f"toUInt64(number%{unique})"
    stage=f"concat('stage-',toString({id_expr}%38))"
    if control:
        expr=f"max(cityHash64({id_expr},{stage})) AS checksum"
    else:
        expr=f"max(timeSeriesIdToGroup(timeSeriesStoreTags({id_expr},NULL,'stage',{stage}))) AS max_group"
    source="numbers_mt" if threads>1 else "numbers"
    return (f"SELECT count() AS n,{expr} FROM {source}({rows}) "
            f"SETTINGS max_threads={threads},max_block_size={block},log_queries=1 FORMAT TSV")
def median(arr):
    return round(statistics.median(arr),3) if arr else None
def query_detail(qid):
    q=sql("SYSTEM FLUSH LOGS")
    if not q["ok"]:return {"error":"flush log: "+q.get("error","")}
    result=sql(("SELECT query_id,type,query_duration_ms,memory_usage,read_rows,read_bytes,ProfileEvents,"
                "exception_code FROM system.query_log WHERE query_id='"+qid+"' FORMAT JSONEachRow"))
    if not result["ok"]:return {"error":"querylog: "+result.get("error","")}
    rows=[json.loads(x) for x in result["body"].splitlines() if x.strip()]
    end=next((x for x in reversed(rows) if x.get("type") in ("QueryFinish","ExceptionWhileProcessing")),None)
    if end is None:return {"error":"missing QueryFinish"}
    events=end.get("ProfileEvents",{})
    return {"query_ms":end.get("query_duration_ms"),
            "query_memory_bytes":end.get("memory_usage"),
            "cpu_us":events.get("OSCPUVirtualTimeMicroseconds"),
            "read_rows":end.get("read_rows"),"read_bytes":end.get("read_bytes"),
            "exception_code":end.get("exception_code"),
            "events":{k:v for k,v in events.items() if v and any(s in k.lower() for s in
                     ("mem","alloc","cpu","hash","context","threads","wait","read","mutex","lock"))}}
def measured(binary,restart,case,trial):
    unique=case["unique"];threads=case["threads"];block=case["block"]
    key=case["key"]
    phase=f"repeat_{RUN}_{key}_{trial}"
    pid=server_restart(restart,binary,phase)
    before=proc_rss(pid)
    qid=f"nativecollector_{RUN}_{key}_{trial}"
    r=run_monitored(make_query(unique,threads,block,case["control"]),pid,qid)
    r["initial_rss_kib"]=before;r["phase"]=phase
    r["trial"]=trial;r["case"]=key
    if r["ok"]:
        try:
            count,last=r["body"].strip().split("\t")
            r["rows"]=int(count);r["group_or_checksum"]=int(last)
            if int(count)!=N:raise ValueError("wrong number of generated rows")
            if not case["control"] and int(last)!=37:
                raise ValueError("wrong group count: expected max_group=37")
        except Exception as exc:
            r["ok"]=False;r["error"]="output validation: "+str(exc)
    r["sql_log"]=query_detail(qid)
    if r["sql_log"].get("exception_code") not in (0,None):
        r["ok"]=False;r["error"]="CH querylog exception "+str(r["sql_log"]["exception_code"])
    if "error" in r["sql_log"]:
        r["ok"]=False;r["error"]="missing query log: "+r["sql_log"]["error"]
    return r

def main():
    p=argparse.ArgumentParser()
    for k in ('binary','restart-helper','output'):p.add_argument('--'+k,required=True)
    a=p.parse_args()
    assert all(N%u==0 and u%38==0 for _,u in FRACTIONS)
    cases=[]
    for code,unique in FRACTIONS:
        for threads in THREADS:
            for block in BLOCKS:
                cases.append({"key":f"{code}_t{threads}_b{block}","unique":unique,
                     "fraction":unique/N,"threads":threads,"block":block,"control":False})
    cases += [{"key":f"control_t{threads}_b{block}","unique":N,
                "fraction":1,"threads":threads,"block":block,"control":True}
              for threads in THREADS for block in BLOCKS]
    result={"schema":1,"run":RUN,"input_rows":N,"cases":cases,"trials":[],
            "errors":[],"notes":["actual ContextTimeSeriesTagsCollector, UInt64 only",
                "all repeated IDs have matching labels; 38 groups",
                "all repeated groups must remain stable across threads and block layouts",
                "physical reads should be zero for numbers() source",
                "fresh CH process per trial; no production CPU speedup inferred"]}
    save(a.output,result)
    # Validate native functions on a short deterministic query first.
    pid=server_restart(a.restart_helper,a.binary,"repeat_smoke")
    smoke=sql(make_query(3800,1,8192,rows=3800))
    if not smoke["ok"] or smoke.get("body","").strip()!="3800\t37":
        result["errors"].append({"smoke":smoke})
        save(a.output,result)
        raise RuntimeError("native collector smoke failure")
    result["smoke"]="3800 rows and 38 native groups";save(a.output,result)
    rng=random.Random(9122001)
    for round_index in range(3):
        order=list(cases);rng.shuffle(order)
        for case in order:
            trial=measured(a.binary,a.restart_helper,case,round_index)
            result["trials"].append(trial)
            if not trial["ok"]:
                result["errors"].append({"case":case["key"],"round":round_index,
                       "error":trial.get("error", "unknown")[:1500]})
            save(a.output,result)
            print("COLLECTOR_REPEAT_TRIAL",json.dumps({
                  "key":case["key"],"round":round_index,"ok":trial["ok"],
                  "duration_ms":round(trial["ms"],3),
                  "query_cpu_us":trial["sql_log"].get("cpu_us"),
                  "query_memory_mib":round((trial["sql_log"].get("query_memory_bytes") or 0)/1048576,2),
                  "rss_mib":round(trial["peak_process_rss_kib"]/1024,2),
                  "error":trial.get("error","")[:200]},separators=(",",":")),flush=True)
    summary={}
    for case in cases:
        subset=[x for x in result["trials"] if x["case"]==case["key"] and x["ok"]]
        summary[case["key"]]={"success":len(subset),"median_wall_ms":median([x["ms"] for x in subset]),
            "median_process_rss_mib":median([x["peak_process_rss_kib"]/1024 for x in subset]),
            "median_query_memory_mib":median([x["sql_log"]["query_memory_bytes"]/1048576 for x in subset]),
            "median_query_cpu_ms":median([x["sql_log"]["cpu_us"]/1000 for x in subset if x["sql_log"]["cpu_us"] is not None]),
            "median_cpu_per_row_us":median([x["sql_log"]["cpu_us"]/N for x in subset if x["sql_log"]["cpu_us"] is not None])}
        if len(subset)!=3:result["errors"].append({"case":case["key"],"success":len(subset)})
    result["summary"]=summary;result["passed"]=not result["errors"]
    save(a.output,result)
    print("COLLECTOR_REPEAT_DONE",json.dumps({"passed":result["passed"],"cases":len(cases),
         "trials":len(result["trials"]),"errors":len(result["errors"]),
         "summary":summary},separators=(",",":")),flush=True)
    if not result["passed"]:raise SystemExit(1)

if __name__=="__main__":main()
