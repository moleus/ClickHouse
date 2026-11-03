#!/usr/bin/env python3
"""Six physical TAGS/TimeRanges/SAMPLES read-CPU A/B/B/A probes.

Compare identical 1.33m TimeSeries ID values in LC UUID and raw UUID.
All queries use FORMAT Null, so client serialization is excluded.
Do not add per-layer wall times: true native pipeline executes concurrently.
"""
import argparse,json,os,pathlib,statistics,sys
sys.path[:0]=[".github/ts-lab-perf",".github/ts-lab-bench"]
from bench_granularity_v11 import sql,inner,streamed_insert,optimize_table,querylogs,wait_no_merges
from bench_bucket_cxx_abca import post
from bench_filtered_selector_v4_sql import sql_string
FIXTURE="8f3a36846f80d6be5a8604d142e934399f2687094766c57bf105e43977f52858"
CASES={"tags_id":("tags","id"),"tags_payload":("tags","id,tags"),
       "ranges_id":("timeranges","id"),"ranges_payload":("timeranges","id,min_time,max_time"),
       "samples_id":("samples","id"),"samples_payload":("samples","id,bucket,samples")}
def write(path,val):
    p=pathlib.Path(path);p.parent.mkdir(parents=True,exist_ok=True)
    p.write_text(json.dumps(val,indent=2,default=str)+"\n")
def records(q):
    return [json.loads(x) for x in sql(q+" FORMAT JSONEachRow").splitlines() if x.strip()]
def main():
    p=argparse.ArgumentParser()
    for x in ("manifest","fixture","output"):p.add_argument("--"+x,required=True)
    a=p.parse_args()
    m=json.loads(pathlib.Path(a.manifest).read_text())
    assert m["sha256"]==FIXTURE and m["series"]==1330000
    alt="tscpu_layers_plainid"
    result={"source_sha":FIXTURE,"physical":{},"cases":{},"errors":[]}
    write(a.output,result)
    try:
        sql("CREATE TABLE "+alt+" ENGINE=TimeSeries SETTINGS recent_samples_ttl_seconds=0 "
            "SAMPLES INNER COLUMNS (id Tuple(UInt64,UUID)) "
            "TAGS INNER COLUMNS (id Tuple(UInt64,UUID) "
            "DEFAULT tuple(sipHash64(metric_name),reinterpretAsUUID(sipHash128(tags))),"
            "metric_name LowCardinality(String),tags Map(LowCardinality(String),String),"
            "INDEX tags_idx tags TYPE text(tokenizer = 'keyValuePairs') GRANULARITY 100000000) "
            "TIME RANGES INNER COLUMNS (id Tuple(UInt64,UUID))")
        if streamed_insert(alt,a.fixture)["rows"]!=1330000:
            raise AssertionError("Incomplete import")
        for table in ("churn_ts",alt):
            optimize_table(table)
            for kind in ("tags","timeranges","samples"):
                n=inner(table,kind).split(chr(96))[1]
                result["physical"][table+"/"+kind]=records(
                    "SELECT count() AS parts,sum(rows) AS rows,sum(marks) AS marks,"
                    "sum(bytes_on_disk) AS bytes FROM system.parts "
                    "WHERE active AND database='default' AND table="+sql_string(n))[0]
        wait_no_merges()
        for case,(kind,columns) in CASES.items():
            phases=[];result["cases"][case]={"phases":phases}
            prefix=f"cpulayer_{case}_{os.getenv('GITHUB_RUN_ID','local')}_"
            for phase,table in enumerate(("churn_ts",alt,alt,"churn_ts")):
                stmt=f"SELECT {columns} FROM {inner(table,kind)} " \
                     "SETTINGS max_threads=4,log_processors_profiles=1," \
                     "query_profiler_cpu_time_period_ns=1000000 FORMAT Null"
                trials=[]
                for i in range(4):
                    qid=prefix+f"{phase}_{i}"
                    ms,_=post("http://127.0.0.1:18191",stmt,qid)
                    trials.append({"qid":qid,"ms":round(ms,3)})
                phases.append({"phase":phase,"table":table,"trials":trials})
                write(a.output,result)
            logs={x["query_id"]:x for x in querylogs(prefix) if x.get("type")=="QueryFinish"}
            for ph in phases:
                for trial in ph["trials"]:
                    log=logs.get(trial["qid"])
                    if log is None:raise AssertionError("Missing querylog "+trial["qid"])
                    ev=log.get("ProfileEvents",{})
                    trial.update({"cpu_ms":ev.get("OSCPUVirtualTimeMicroseconds",0)/1000,
                                  "read_rows":log["read_rows"],"read_bytes":log["read_bytes"],
                                  "memory":log["memory_usage"],"marks":ev.get("SelectedMarks")})
                ph["wall_ms"]=statistics.median(x["ms"] for x in ph["trials"][1:])
                ph["cpu_ms"]=statistics.median(x["cpu_ms"] for x in ph["trials"][1:])
            left=[x for x in phases if x["table"]=="churn_ts"]
            right=[x for x in phases if x["table"]==alt]
            w1=statistics.mean(x["wall_ms"] for x in left)
            w2=statistics.mean(x["wall_ms"] for x in right)
            drift=abs(left[0]["wall_ms"]-left[1]["wall_ms"])/w1
            summary={"lc_ms":round(w1,3),"raw_ms":round(w2,3),
                     "speedup":round(w1/w2,3),
                     "lc_cpu_ms":round(statistics.mean(x["cpu_ms"] for x in left),3),
                     "raw_cpu_ms":round(statistics.mean(x["cpu_ms"] for x in right),3),
                     "baseline_drift":round(drift,4),"valid":drift<=.25}
            result["cases"][case]["summary"]=summary
            qids=[phases[0]["trials"][1]["qid"],phases[1]["trials"][1]["qid"]]
            query="SELECT query_id,multiIf(arrayExists(s -> position(s,'ReverseIndex')>0 OR position(s,'SerializationLowCardinality')>0,symbols),'lowcard',arrayExists(s -> position(s,'MergeTreeReader')>0,symbols),'read','other') AS component,count() AS samples FROM system.trace_log WHERE trace_type='CPU' AND query_id IN ("+",".join(sql_string(i) for i in qids)+") GROUP BY query_id,component"
            try:result["cases"][case]["cpu_taxonomy"]=records(query)
            except Exception as e:result["cases"][case]["profile_error"]=str(e)[:1400]
            if drift>.25:result["errors"].append({"case":case,"drift":drift})
            print("CPU_LAYER_RESULT",json.dumps({"case":case,**summary}),flush=True)
            write(a.output,result)
        result["passed"]=not result["errors"]
    except Exception as exc:
        result["errors"].append({"fatal":str(exc)[:1700]});result["passed"]=False
    finally:
        write(a.output,result)
        compact={"passed":result["passed"],"errors":result["errors"],
                 "cases":{k:v.get("summary") for k,v in result["cases"].items()}}
        write(str(pathlib.Path(a.output).with_suffix(".compact.json")),compact)
        print("CPU_LAYER_DONE",json.dumps(compact,separators=(",",":")),flush=True)
    if not result["passed"]:raise SystemExit(1)
if __name__=="__main__":main()
