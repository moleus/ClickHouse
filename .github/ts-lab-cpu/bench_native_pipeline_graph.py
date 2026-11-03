#!/usr/bin/env python3
"""Native TimeSeries pipeline graph and CPU-stage attribution after RAW_ID fix.

Stock O1 ClickHouse binary, same 1.33m series, default LC UUID versus raw UUID
v11 inner schema. Collect each processor ID and parent_ids from the real native
query. Report active-work, input-starvation, output-backpressure, graph shape,
and the longest weighted DAG work chain. This chain is NOT wall critical path
because streaming processors overlap and may process multiple blocks in parallel.
"""
import argparse,collections,json,os,pathlib,statistics,sys
sys.path[:0]=[".github/ts-lab-perf",".github/ts-lab-bench"]
from bench_granularity_v11 import sql,inner,streamed_insert,optimize_table,querylogs,wait_no_merges
from bench_bucket_cxx_abca import post,normalize_any,assert_same
from bench_filtered_selector_v4_sql import sql_string
SOURCE="8f3a36846f80d6be5a8604d142e934399f2687094766c57bf105e43977f52858"
SELS={
 "stage":('{stage="stage-0007"}',14,1),
 "project":('{project="project-00"}',14,19),
 "whole":("",1,38)
}
def save(p,o):
    f=pathlib.Path(p);f.parent.mkdir(parents=True,exist_ok=True)
    f.write_text(json.dumps(o,indent=2,sort_keys=True,default=str)+"\n")
def records(q):
    return [json.loads(line) for line in sql(q+" FORMAT JSONEachRow").splitlines() if line.strip()]
def graph_summary(nodes):
    by_id={}
    for n in nodes:
        key=int(n["id"])
        if key in by_id:raise AssertionError("Duplicate processor ID "+str(key))
        by_id[key]=n
    missing_parents=0
    children=collections.defaultdict(list);in_degree={x:0 for x in by_id}
    for idx,row in by_id.items():
        for par in row["parent_ids"]:
            par=int(par)
            if par not in by_id:missing_parents+=1;continue
            children[par].append(idx);in_degree[idx]+=1
    # Work DAG structural estimate. Ignores concurrent streaming overlap and
    # is NOT a real wall critical-path measurement.
    q=collections.deque(x for x,n in in_degree.items() if n==0)
    best={idx:(int(by_id[idx]["elapsed_us"]),None) for idx in q}
    visited=0
    while q:
        idx=q.popleft();visited+=1
        work=int(by_id[idx]["elapsed_us"])
        for dst in children[idx]:
            length=best[idx][0]+int(by_id[dst]["elapsed_us"])
            if length>best.get(dst,(0,None))[0]:
                best[dst]=(length,idx)
            in_degree[dst]-=1
            if in_degree[dst]==0:q.append(dst)
    if visited!=len(by_id):raise AssertionError("Cycle or incomplete query pipeline DAG")
    end=max(best,key=lambda x:best[x][0]);back=[]
    cur=end
    while cur is not None:
        row=by_id[cur]
        back.append({"id":cur,"name":row["name"],"step":row["plan_step_name"],
                     "work_us":row["elapsed_us"],
                     "input_wait_us":row["input_wait_elapsed_us"],
                     "output_wait_us":row["output_wait_elapsed_us"]})
        cur=best[cur][1]
    steps=collections.defaultdict(lambda:{"processors":0,"work_us":0,"input_wait_us":0,
                                          "output_wait_us":0,"max_processor_work_us":0,
                                          "rows_in":0,"rows_out":0})
    for row in by_id.values():
        key=(row["plan_step_name"],row["name"])
        t=steps[key]
        t["processors"]+=1
        for name,target in [
            ("elapsed_us","work_us"),("input_wait_elapsed_us","input_wait_us"),
            ("output_wait_elapsed_us","output_wait_us"),("input_rows","rows_in"),
            ("output_rows","rows_out")]:
            t[target]+=int(row[name])
        t["max_processor_work_us"]=max(t["max_processor_work_us"],int(row["elapsed_us"]))
    rows=[{"plan_step":k[0],"name":k[1],**v} for k,v in steps.items()]
    rows.sort(key=lambda x:x["work_us"],reverse=True)
    upstream_starved=sorted(rows,key=lambda x:x["input_wait_us"],reverse=True)[:16]
    downstream_stalled=sorted(rows,key=lambda x:x["output_wait_us"],reverse=True)[:16]
    return {"processors":len(by_id),"edges":sum(len(x) for x in children.values()),
            "missing_parent_edges":missing_parents,
            "structural_longest_sum_work_us":best[end][0],
            "critical_chain_not_wall":list(reversed(back)),
            "top_work_stages":rows[:30],
            "top_input_starved":upstream_starved,
            "top_output_stalled":downstream_stalled,
            "all_operator_groups":rows}
def details(ids):
    sql("SYSTEM FLUSH LOGS")
    quoted="("+",".join(sql_string(v) for v in ids)+")"
    proc=records(
        "SELECT query_id,id,parent_ids,plan_step,plan_step_name,name,"
        "elapsed_us,input_wait_elapsed_us,output_wait_elapsed_us,"
        "input_rows,output_rows FROM system.processors_profile_log "
        "WHERE query_id IN "+quoted+" FORMAT JSONEachRow")
    by_q=collections.defaultdict(list)
    for n in proc:by_q[n["query_id"]].append(n)
    out={}
    for qid in ids:
        if qid not in by_q:raise AssertionError("No pipeline nodes for "+qid)
        nodes=by_q[qid]
        out[qid]={"graph":graph_summary(nodes),"nodes":nodes}
    cpu=records(
        "SELECT query_id,"
        "multiIf(arrayExists(s -> position(s,'ReverseIndex')>0 OR position(s,'ColumnUnique')>0,symbols),'lowcard_dictionary',"
        "arrayExists(s -> position(s,'ContextTimeSeriesTagsCollector')>0,symbols),'tagcollector',"
        "arrayExists(s -> position(s,'MergeTreeReader')>0,symbols),'physical_read',"
        "arrayExists(s -> position(s,'Aggregat')>0 OR position(s,'timeSeries')>0,symbols),'grid_aggregate','other') AS component,"
        "count() AS cpu_samples FROM system.trace_log WHERE query_id IN "+quoted+
        " AND trace_type='CPU' GROUP BY query_id,component ORDER BY query_id,cpu_samples DESC")
    for row in cpu:out[row["query_id"]].setdefault("cpu",[]).append(row)
    return out
def make_sql(table,case,finish):
    sel,days,_=SELS[case];start=finish-(days-1)*86400
    expr=f"avg by(stage)(avg_over_time(jenkins_ci_95k_daily{sel}[1d]))"
    return (f"SELECT * FROM prometheusQueryRange({table},{sql_string(expr)},"
            f"{start},{finish},86400) SETTINGS max_threads=4,max_memory_usage=3500000000,"
            "log_queries=1,log_processors_profiles=1,query_profiler_cpu_time_period_ns=1000000 "
            "FORMAT JSONEachRow")
def main():
    p=argparse.ArgumentParser()
    for name in ("manifest","fixture","output"):p.add_argument("--"+name,required=True)
    p.add_argument("--case",choices=SELS,required=True)
    a=p.parse_args()
    meta=json.loads(pathlib.Path(a.manifest).read_text())
    assert meta["sha256"]==SOURCE and meta["series"]==1330000
    raw="tslab_pipeline_rawid"
    report={"case":a.case,"source_sha":SOURCE,"errors":[],"phases":[]}
    save(a.output,report)
    try:
        sql("CREATE TABLE "+raw+" ENGINE=TimeSeries SETTINGS recent_samples_ttl_seconds=0 "
            "SAMPLES INNER COLUMNS (id Tuple(UInt64,UUID)) "
            "TAGS INNER COLUMNS (id Tuple(UInt64,UUID) DEFAULT "
            "tuple(sipHash64(metric_name),reinterpretAsUUID(sipHash128(tags))),"
            "metric_name LowCardinality(String),tags Map(LowCardinality(String),String),"
            "INDEX tags_idx tags TYPE text(tokenizer = 'keyValuePairs') GRANULARITY 100000000) "
            "TIME RANGES INNER COLUMNS (id Tuple(UInt64,UUID))")
        ins=streamed_insert(raw,a.fixture)
        if ins["rows"]!=1330000:raise AssertionError("Missing imported data")
        for t in ("churn_ts",raw):optimize_table(t)
        wait_no_merges()
        ids={}
        for t in ("churn_ts",raw):
            n=inner(t,"tags")
            ids[t]=records("SELECT count() AS n,sum(sipHash64(toString(id))) AS hash "
                           "FROM "+n)[0]
        if ids["churn_ts"]!=ids[raw]:raise AssertionError("Mismatched logical series IDs")
        finish=int(meta["end"]);sel,days,n=SELS[a.case]
        grid={finish-(days-1)*86400+i*86400 for i in range(days)}
        reference=None
        for phase,table in enumerate(("churn_ts",raw,raw,"churn_ts")):
            trials=[];q=make_sql(table,a.case,finish)
            for i in range(5):
                qid=f"tsgraph_{a.case}_{phase}_{i}_{os.getenv('GITHUB_RUN_ID','local')}"
                ms,body=post("http://127.0.0.1:18191",q,qid)
                points=normalize_any(body)
                if len(points)!=days*n or {t for (_l,t) in points}!=grid:
                    raise AssertionError("Unexpected output cardinality")
                if reference is None:reference=points
                else:assert_same(reference,points,a.case,table)
                trials.append({"qid":qid,"client_ms":round(ms,3)})
            report["phases"].append({"phase":phase,"table":table,"trials":trials})
            save(a.output,report)
        logs={x["query_id"]:x for x in querylogs("tsgraph_"+a.case+"_") if x.get("type")=="QueryFinish"}
        for phase in report["phases"]:
            for t in phase["trials"]:
                log=logs.get(t["qid"])
                if log is None:raise AssertionError("Missing query log "+t["qid"])
                ev=log.get("ProfileEvents",{})
                t.update({"cpu_ms":ev.get("OSCPUVirtualTimeMicroseconds",0)/1000,
                          "scheduler_wait_ms":ev.get("OSCPUWaitMicroseconds",0)/1000,
                          "memory":log["memory_usage"],"read_rows":log["read_rows"],
                          "read_bytes":log["read_bytes"]})
            vals=phase["trials"][2:]
            phase["median_ms"]=statistics.median(t["client_ms"] for t in vals)
            phase["median_cpu_ms"]=statistics.median(t["cpu_ms"] for t in vals)
            phase["profile_qid"]=vals[0]["qid"]
        qids=[p["profile_qid"] for p in report["phases"][:2]]
        report["attribution"]=details(qids)
        baseline=[x for x in report["phases"] if x["table"]=="churn_ts"]
        candidate=[x for x in report["phases"] if x["table"]==raw]
        aa=statistics.mean(x["median_ms"] for x in baseline)
        bb=statistics.mean(x["median_ms"] for x in candidate)
        drift=abs(baseline[0]["median_ms"]-baseline[1]["median_ms"])/aa
        report["summary"]={"baseline_ms":round(aa,3),"raw_uuid_ms":round(bb,3),
                           "speedup":round(aa/bb,4),
                           "baseline_cpu_ms":round(statistics.mean(x["median_cpu_ms"] for x in baseline),3),
                           "raw_cpu_ms":round(statistics.mean(x["median_cpu_ms"] for x in candidate),3),
                           "control_drift":round(drift,4),"valid":drift<=.25}
        if drift>.25:report["errors"].append({"control_drift":drift})
        report["passed"]=not report["errors"]
    except Exception as e:
        report["errors"].append({"fatal":str(e)[:1900]});report["passed"]=False
    finally:
        save(a.output,report)
        compact={"case":a.case,"passed":report["passed"],
                 "summary":report.get("summary"),"errors":report["errors"],
                 "top_stages":{qid:{"work":v["graph"]["top_work_stages"][:12],
                                   "input_starved":v["graph"]["top_input_starved"][:10],
                                   "output_stalled":v["graph"]["top_output_stalled"][:10],
                                   "structural_chain":v["graph"]["critical_chain_not_wall"]}
                                for qid,v in report.get("attribution",{}).items()}}
        save(str(pathlib.Path(a.output).with_suffix(".compact.json")),compact)
        print("TSGRAPH_DONE",json.dumps({"case":a.case,"passed":report["passed"],
              "summary":report.get("summary"),"errors":report["errors"]},separators=(",",":")),flush=True)
    if not report["passed"]:raise SystemExit(1)
if __name__=="__main__":main()
