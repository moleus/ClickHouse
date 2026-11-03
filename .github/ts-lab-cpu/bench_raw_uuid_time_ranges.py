#!/usr/bin/env python3
"""Stock ClickHouse O1, raw UUID v11: TimeRanges ON vs OFF × time-window.

Separately create two new TimeSeries tables; do not alter installed data.
Both use identical Tuple(UInt64,UUID) ID and exact tag-hash generator.
This isolates the cost of TimeRanges metadata scan + native SQL JOIN
against the sampling saved by the time filter (1/3/7/14 day queries).
A/B/B/A on ONE stock binary; no C++ lab patch, no process restart.
"""
import argparse,json,os,pathlib,statistics,sys,time
sys.path[:0]=[".github/ts-lab-perf",".github/ts-lab-bench",".github/ts-lab-cpu"]
from bench_granularity_v11 import sql,inner,streamed_insert,optimize_table,querylogs,wait_no_merges
from bench_bucket_cxx_abca import post,normalize_any,assert_same
from bench_filtered_selector_v4_sql import sql_string
from bench_native_uuid_type import capture
SOURCE="8f3a36846f80d6be5a8604d142e934399f2687094766c57bf105e43977f52858"
DAY=86400
NAME="jenkins_ci_95k_daily"
SEL={"stage":('{stage="stage-0007"}',1),"project":('{project="project-00"}',19),"whole":("",38)}
WINDOWS=(1,3,7,14)
METRIC=NAME
def save(p,data):
    path=pathlib.Path(p);path.parent.mkdir(parents=True,exist_ok=True)
    path.write_text(json.dumps(data,indent=2,sort_keys=True,default=str)+"\n")
def rows(q):
    return [json.loads(x) for x in sql(q+" FORMAT JSONEachRow").splitlines() if x.strip()]
def make_ddl(table, ranges):
    out=(f"CREATE TABLE {table} ENGINE=TimeSeries SETTINGS recent_samples_ttl_seconds=0,"
         f"store_time_ranges={int(ranges)} "
         "SAMPLES INNER COLUMNS (id Tuple(UInt64, UUID)) "
         "TAGS INNER COLUMNS (id Tuple(UInt64, UUID) DEFAULT "
         "tuple(sipHash64(metric_name),reinterpretAsUUID(sipHash128(tags))), "
         "metric_name LowCardinality(String),tags Map(LowCardinality(String),String),"
         "INDEX tags_idx tags TYPE text(tokenizer = 'keyValuePairs') GRANULARITY 100000000)")
    if ranges:out+=" TIME RANGES INNER COLUMNS (id Tuple(UInt64, UUID))"
    return out
def check_data(table):
    tags=inner(table,"tags")
    samples=inner(table,"samples")
    tag=rows("SELECT count() AS n,sum(sipHash64(toString(id))) AS digest,"
             "countIf(tags['stage']='stage-0007') AS stage_count FROM "+tags)[0]
    sam=rows("SELECT count() AS n,sum(sipHash64(toString(id))) AS digest FROM "+samples)[0]
    for place in ("tags","samples"):
        n=inner(table,place).split(chr(96))[1]
        c=rows("SELECT type FROM system.columns WHERE database='default' AND table="
               +sql_string(n)+" AND name='id'")[0]["type"]
        if c!="Tuple(UInt64, UUID)":raise AssertionError(("Wrong ID",table,place,c))
    if tag["n"]!=1330000 or tag["stage_count"]!=35000 or sam["n"]!=1330000:
        raise AssertionError(("Wrong rows",table,tag,sam))
    return {"tags":tag,"samples":sam}
def query(table,selector,days,end):
    name=SEL[selector][0]
    begin=end-(days-1)*DAY
    expr=f"avg by(stage)(avg_over_time({METRIC}{name}[1d]))"
    return (f"SELECT * FROM prometheusQueryRange({table},{sql_string(expr)},"
            f"{begin},{end},{DAY}) SETTINGS max_threads=4,"
            "max_memory_usage=3500000000,log_queries=1,log_processors_profiles=1,"
            "query_profiler_cpu_time_period_ns=1000000 FORMAT JSONEachRow")
def finish_logs(qids):
    missing=qids[:]
    for attempt in range(12):
        result={x["query_id"]:x for x in querylogs("tsnorange_rawid_") if x.get("type")=="QueryFinish"}
        missing=[qid for qid in qids if qid not in result]
        if not missing:return result
        print("RAW_TIME_QUERYLOG_RETRY",json.dumps({"attempt":attempt+1,"missing_count":len(missing)}),flush=True)
        time.sleep(.25+.15*attempt)
    raise AssertionError(("QueryFinish missing after retries",missing))
def bench_window(report,case,days,table_on,table_off,finish,outfile):
    prefix=f"tsnorange_rawid_{case}_{days}_{os.getenv('GITHUB_RUN_ID','local')}_"
    times={finish-(days-1)*DAY+i*DAY for i in range(days)}
    count=SEL[case][1]*days
    reference=None
    phases=[]
    qids=[]
    for phase,side in enumerate(("A","B","B","A")):
        table=table_on if side=="A" else table_off
        stmt=query(table,case,days,finish)
        trials=[]
        for rep in range(6):
            qid=f"{prefix}{phase}_{rep}"
            ms,body=post("http://127.0.0.1:18191",stmt,qid)
            got=normalize_any(body)
            if len(got)!=count or {timestamp for _tags,timestamp in got}!=times:
                raise AssertionError(("Output parity point count or time grid failed",case,days,table,len(got),count))
            if reference is None:reference=got
            else:assert_same(reference,got,case,f"TimeRanges_{side}")
            trials.append({"qid":qid,"client_ms":round(ms,3),"warmup":rep<2})
            qids.append(qid)
        phases.append({"phase":phase,"side":side,"table":table,"trials":trials})
        report["windows"][str(days)]={"phases":phases,"status":"partial"}
        save(outfile,report)
        print("RAW_TIME_PHASE",json.dumps({"selector":case,"days":days,"phase":phase,"side":side,
                                          "last_ms":trials[-1]["client_ms"]}),flush=True)
    logs=finish_logs(qids)
    for ph in phases:
        for trial in ph["trials"]:
            log=logs[trial["qid"]]
            ev=log.get("ProfileEvents") or {}
            trial.update({"cpu_ms":round(ev.get("OSCPUVirtualTimeMicroseconds",0)/1000,3),
                          "scheduler_wait_ms":round(ev.get("OSCPUWaitMicroseconds",0)/1000,3),
                          "read_rows":log["read_rows"],"read_bytes":log["read_bytes"],
                          "query_peak_memory_bytes":log["memory_usage"],
                          "selected_marks":ev.get("SelectedMarks"),
                          "compressed_read_bytes":ev.get("ReadCompressedBytes")})
        measured=ph["trials"][2:]
        ph["p50_ms"]=statistics.median(x["client_ms"] for x in measured)
        ph["p50_cpu_ms"]=statistics.median(x["cpu_ms"] for x in measured)
        ph["p50_read_rows"]=statistics.median(x["read_rows"] for x in measured)
        ph["p50_read_bytes"]=statistics.median(x["read_bytes"] for x in measured)
        ph["p50_scheduler_wait_ms"]=statistics.median(x["scheduler_wait_ms"] for x in measured)
        ph["max_peak_memory_bytes"]=max(x["query_peak_memory_bytes"] for x in measured)
    base=[ph for ph in phases if ph["side"]=="A"]
    alt=[ph for ph in phases if ph["side"]=="B"]
    avg=lambda x,key:statistics.mean(y[key] for y in x)
    baseline=avg(base,"p50_ms");candidate=avg(alt,"p50_ms")
    drift=abs(base[0]["p50_ms"]-base[1]["p50_ms"])/baseline
    summary={"time_window_days":days,
             "ranges_on_wall_ms":round(baseline,3),
             "ranges_off_wall_ms":round(candidate,3),
             "ranges_off_speedup":round(baseline/candidate,4),
             "ranges_on_cpu_ms":round(avg(base,"p50_cpu_ms"),3),
             "ranges_off_cpu_ms":round(avg(alt,"p50_cpu_ms"),3),
             "ranges_on_read_rows":round(avg(base,"p50_read_rows")),
             "ranges_off_read_rows":round(avg(alt,"p50_read_rows")),
             "ranges_on_read_bytes":round(avg(base,"p50_read_bytes")),
             "ranges_off_read_bytes":round(avg(alt,"p50_read_bytes")),
             "ranges_on_query_memory_max_bytes":max(x["max_peak_memory_bytes"] for x in base),
             "ranges_off_query_memory_max_bytes":max(x["max_peak_memory_bytes"] for x in alt),
             "control_drift":round(drift,4),"valid":drift<=.25}
    profile_ids=[base[0]["trials"][2]["qid"],alt[0]["trials"][2]["qid"]]
    report["windows"][str(days)].update({"status":"complete","summary":summary,
                                         "cpu_profiles":capture(profile_ids)})
    if drift>.25:report["errors"].append({"days":days,"baseline_drift":round(drift,4)})
    save(outfile,report)
    print("RAW_TIME_WINDOW",json.dumps({"selector":case,**summary},separators=(",",":")),flush=True)
    return summary
def main():
    p=argparse.ArgumentParser()
    p.add_argument("--case",choices=SEL,required=True)
    for key in ("manifest","fixture","output"):p.add_argument("--"+key,required=True)
    a=p.parse_args()
    m=json.loads(pathlib.Path(a.manifest).read_text())
    if m.get("sha256")!=SOURCE or m.get("series")!=1330000:
        raise AssertionError("Canonical 95k daily fixture differs")
    on,off="tsnorange_rawid_on","tsnorange_rawid_off"
    report={"case":a.case,"fixture_sha":SOURCE,"variants":{"ranges_on":on,"ranges_off":off},
            "source":"stock ClickHouse pinned O1, raw UUID both tables",
            "windows":{},"errors":[],"phases_before":{}}
    save(a.output,report)
    try:
        for table,enabled in ((on,True),(off,False)):
            sql(make_ddl(table,enabled))
            ingested=streamed_insert(table,a.fixture)
            if ingested["rows"]!=1330000:raise AssertionError(("Incomplete insert",table,ingested))
            report.setdefault("ingest",{})[table]=ingested
            report.setdefault("optimize",{})[table]=optimize_table(table)
        wait_no_merges()
        sql("SYSTEM STOP MERGES")
        report["identity"]={x:check_data(x) for x in (on,off)}
        if report["identity"][on]!=report["identity"][off]:
            raise AssertionError("Series/sample ID digests differ")
        for days in WINDOWS:
            bench_window(report,a.case,days,on,off,int(m["end"]),a.output)
        report["passed"]=not report["errors"] and len(report["windows"])==len(WINDOWS)
    except Exception as e:
        report["errors"].append({"fatal":str(e)[:2000]})
        report["passed"]=False
    finally:
        save(a.output,report)
        compact={"case":a.case,"passed":report.get("passed",False),
                 "errors":report["errors"],"summary":{k:v.get("summary") for k,v in report["windows"].items()}}
        save(str(pathlib.Path(a.output).with_suffix(".compact.json")),compact)
        print("RAW_TIME_DONE",json.dumps(compact,separators=(",",":")),flush=True)
    if not report["passed"]:raise SystemExit(1)
if __name__=="__main__":main()
