#!/usr/bin/env python3
"""Native PromQL CPU attribution: change one execution knob at a time.

Canonical 1.33m fixture and SAME pinned O1 binary for all trials. Exact label
and timestamp parity, numeric tolerance, A/B/B/A drift gate, query log,
full CPU sample taxonomy, processor work/wait, hot CPU + memory leaf stacks.
"""
import argparse,json,os,pathlib,statistics,sys
sys.path.insert(0,".github/ts-lab-perf")
sys.path.insert(0,".github/ts-lab-bench")
from bench_granularity_v11 import sql,querylogs
from bench_bucket_cxx_abca import post,normalize_any,assert_same
from bench_filtered_selector_v4_sql import sql_string

FIXTURE="8f3a36846f80d6be5a8604d142e934399f2687094766c57bf105e43977f52858"
NAME="jenkins_ci_95k_daily"
DAY=86400
SELECTORS={"stage":('{stage="stage-0007"}',1),
           "project":('{project="project-00"}',19),
           "regex":('{branch=~"main|feature-0[0-9]"}',38),
           "whole":("",38)}
MODES={"threads":("max_threads",4,[1,2,8]),
       "blocks":("max_block_size",65536,[1024,4096,16384])}

def write(file,obj):
    p=pathlib.Path(file);p.parent.mkdir(parents=True,exist_ok=True)
    p.write_text(json.dumps(obj,indent=2,sort_keys=True,default=str)+"\n")
def query(case,days,finish,knob,value):
    expr=f"avg by(stage)(avg_over_time({NAME}{SELECTORS[case][0]}[1d]))"
    opts={"max_threads":4,"max_block_size":65536,"max_memory_usage":3500000000,
          "log_queries":1,"log_processors_profiles":1,
          "query_profiler_cpu_time_period_ns":1000000,
          "query_profiler_real_time_period_ns":1000000,
          "memory_profiler_step":1048576}
    opts[knob]=value
    settings=",".join(f"{k}={v}" for k,v in opts.items())
    start=finish-(days-1)*DAY
    return (f"SELECT * FROM prometheusQueryRange(churn_ts,{sql_string(expr)},"
            f"{start},{finish},{DAY}) SETTINGS {settings} FORMAT JSONEachRow")

def data(q):
    return [json.loads(s) for s in sql(q+" FORMAT JSONEachRow").splitlines() if s.strip()]
def capture(q,out,key):
    try:out[key]=data(q)
    except Exception as e:out[key+"_error"]=str(e)[:1500]
def capture_profiles(qids):
    sql("SYSTEM FLUSH LOGS")
    ids="("+",".join(sql_string(q) for q in qids)+")"
    o={}
    capture("SELECT query_id,plan_step_name,name,count() AS instances,"
            "sum(elapsed_us) AS work_us,sum(input_wait_elapsed_us) AS input_wait_us,"
            "sum(output_wait_elapsed_us) AS output_wait_us,"
            "sum(input_rows) AS input_rows,sum(output_rows) AS output_rows "
            "FROM system.processors_profile_log WHERE query_id IN "+ids+
            " GROUP BY query_id,plan_step_name,name ORDER BY query_id,work_us DESC LIMIT 300 BY query_id",o,"processors")
    # No limit on input samples. Classify complete CPU trace_log on the server.
    classifier="""multiIf(
      arrayExists(s -> position(s,'ReverseIndex')>0 OR position(s,'ColumnUnique')>0 OR position(s,'SerializationLowCardinality')>0 OR position(s,'ColumnLowCardinality')>0, symbols), '01_lowcard_dictionary',
      arrayExists(s -> position(s,'FillingRightJoinSide')>0 OR position(s,'HashJoin')>0 OR position(s,'JoiningTransform')>0, symbols), '02_hash_join',
      arrayExists(s -> position(s,'MergeJoinTransform')>0 OR position(s,'FullSortingMergeJoin')>0, symbols), '03_merge_join',
      arrayExists(s -> position(s,'timeSeriesStoreTags')>0 OR position(s,'ContextTimeSeriesTagsCollector')>0, symbols), '04_tag_registration',
      arrayExists(s -> position(s,'ReadBufferFromFile')>0 OR position(s,'MergeTreeReader')>0 OR position(s,'MergeTreeReadTask')>0, symbols), '05_read_decode',
      arrayExists(s -> position(s,'AdaptiveBucketMergingSource')>0 OR position(s,'Aggregator')>0 OR position(s,'AggregatingTransform')>0, symbols), '06_grid_aggregate',
      arrayExists(s -> position(s,'CreatingSets')>0 OR position(s,'SetVariants')>0, symbols), '07_id_set',
      arrayExists(s -> position(s,'ExpressionTransform')>0, symbols), '08_expression',
      '09_other')"""
    capture("SELECT query_id,"+classifier+" AS component,count() AS cpu_samples "
            "FROM system.trace_log WHERE trace_type='CPU' AND query_id IN "+ids+
            " GROUP BY query_id,component ORDER BY query_id,cpu_samples DESC LIMIT 90 BY query_id",o,"cpu_components")
    capture("SELECT query_id,trace_type,arrayFirst(x -> x != '',symbols) AS leaf,"
            "count() AS samples,sum(size) AS bytes FROM system.trace_log WHERE query_id IN "+ids+
            " AND trace_type IN ('CPU','Memory')"
            " GROUP BY query_id,trace_type,leaf ORDER BY query_id,samples DESC LIMIT 250 BY query_id",o,"hot_leaves")
    capture("SELECT query_id,trace_type,"
            "arrayStringConcat(arrayReverse(symbols),';') AS stack,"
            "count() AS samples,sum(size) AS bytes FROM system.trace_log WHERE query_id IN "+ids+
            " GROUP BY query_id,trace_type,stack ORDER BY query_id,samples DESC LIMIT 300 BY query_id",o,"folded_stacks")
    return o

def main():
    ap=argparse.ArgumentParser()
    ap.add_argument("--case",choices=SELECTORS,required=True)
    ap.add_argument("--days",type=int,required=True)
    ap.add_argument("--mode",choices=MODES,required=True)
    ap.add_argument("--manifest",required=True)
    ap.add_argument("--output",required=True)
    args=ap.parse_args()
    m=json.loads(pathlib.Path(args.manifest).read_text())
    assert m.get("sha256")==FIXTURE and m.get("series")==1330000 and args.days in (1,3,7,14)
    knob,base,alts=MODES[args.mode]
    finish=int(m["end"])
    expected={finish-(args.days-1)*DAY+i*DAY for i in range(args.days)}
    expected_n=SELECTORS[args.case][1]*args.days
    report={"case":args.case,"days":args.days,"mode":args.mode,"knob":knob,
            "source_fixture_sha":FIXTURE,"baseline":base,"comparisons":{},"errors":[]}
    write(args.output,report)
    reference=None
    prefix=f"cpulab_{args.case}_{args.days}_{args.mode}_"
    def one(val,alt,phase,i):
        nonlocal reference
        qid=prefix+f"{alt}_{phase}_{i}_{os.getenv('GITHUB_RUN_ID','local')}"
        ms,raw=post("http://127.0.0.1:18191",
                    query(args.case,args.days,finish,knob,val),qid)
        result=normalize_any(raw)
        if len(result)!=expected_n:raise AssertionError(f"Wrong number of points: {len(result)} != {expected_n}")
        if {t for (_labels,t) in result}!=expected:raise AssertionError("Incorrect time grid")
        if reference is None:reference=result
        else:assert_same(reference,result,args.case,args.mode)
        return {"qid":qid,"ms":round(ms,3),"value":val,"warmup":i==0}
    try:
        sql("SYSTEM STOP MERGES")
        for alt in alts:
            phases=[]
            report["comparisons"][str(alt)]={"phases":phases}
            for phase,value in enumerate((base,alt,alt,base)):
                trials=[one(value,alt,phase,i) for i in range(3)]
                phases.append({"number":phase,"value":value,"trials":trials})
                write(args.output,report)
                print("CPULAB_PHASE",json.dumps({"case":args.case,"mode":args.mode,
                      "alternative":alt,"phase":phase,"last_ms":trials[-1]["ms"]}),flush=True)
            logs={x["query_id"]:x for x in querylogs(prefix+str(alt)+"_")
                  if x.get("type")=="QueryFinish"}
            for ph in phases:
                for t in ph["trials"]:
                    log=logs.get(t["qid"])
                    if not log:raise AssertionError("Missing QueryFinish "+t["qid"])
                    ev=log.get("ProfileEvents",{})
                    t.update({"wall_ms":log["query_duration_ms"],
                              "cpu_ms":round(ev.get("OSCPUVirtualTimeMicroseconds",0)/1000,3),
                              "memory_bytes":log["memory_usage"],
                              "read_rows":log["read_rows"],"read_bytes":log["read_bytes"],
                              "selected_marks":ev.get("SelectedMarks"),
                              "compressed_bytes":ev.get("ReadCompressedBytes")})
                ph["median_ms"]=statistics.median(x["ms"] for x in ph["trials"][1:])
                ph["median_cpu_ms"]=statistics.median(x["cpu_ms"] for x in ph["trials"][1:])
            a=[p for p in phases if p["value"]==base]
            b=[p for p in phases if p["value"]==alt]
            a_ms=statistics.mean(x["median_ms"] for x in a)
            b_ms=statistics.mean(x["median_ms"] for x in b)
            drift=abs(a[0]["median_ms"]-a[-1]["median_ms"])/a_ms
            summary={"baseline_ms":round(a_ms,3),"variant_ms":round(b_ms,3),
                     "speedup_base_vs_variant":round(a_ms/b_ms,4),
                     "baseline_cpu_ms":round(statistics.mean(x["median_cpu_ms"] for x in a),3),
                     "variant_cpu_ms":round(statistics.mean(x["median_cpu_ms"] for x in b),3),
                     "baseline_drift":round(drift,4),
                     "valid":drift<=0.25}
            qids=[phases[0]["trials"][1]["qid"],phases[1]["trials"][1]["qid"]]
            report["comparisons"][str(alt)].update({
                "summary":summary,"profiles":capture_profiles(qids),
                "sampled_qids":qids})
            if drift>0.25:report["errors"].append({"alternative":alt,"drift":drift})
            print("CPULAB_COMPARISON",json.dumps({"case":args.case,"days":args.days,
                  "mode":args.mode,"alternative":alt,**summary}),flush=True)
            write(args.output,report)
        report["passed"]=not report["errors"]
    except Exception as e:
        report["errors"].append({"fatal":str(e)[:1800]});report["passed"]=False
    finally:
        write(args.output,report)
        write(str(pathlib.Path(args.output).with_suffix(".compact.json")),
              {"case":args.case,"days":args.days,"mode":args.mode,"passed":report.get("passed"),
               "summaries":{k:v.get("summary") for k,v in report["comparisons"].items()},
               "errors":report["errors"]})
        print("CPULAB_DONE",json.dumps({"case":args.case,"days":args.days,
              "mode":args.mode,"passed":report.get("passed"),
              "summaries":{k:v.get("summary") for k,v in report["comparisons"].items()},
              "errors":report["errors"]},separators=(",",":")),flush=True)
    if not report["passed"]:raise SystemExit(1)
if __name__=="__main__":main()
