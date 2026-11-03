#!/usr/bin/env python3
"""Full native TimeSeries v11 storage-ID A/B/B/A, SAME O1 binary and fixture.

Compare default Tuple(UInt64, LowCardinality(UUID)) against
Tuple(UInt64, UUID) across TAGS, TimeRanges, SAMPLES. This changes the
whole TimeSeries id_type, not just one isolated inner table. Assert exact
logical ID digests and exact output labels/timestamps/numeric tolerance.
"""
import argparse,json,os,pathlib,statistics,sys
sys.path.insert(0,".github/ts-lab-perf")
sys.path.insert(0,".github/ts-lab-bench")
from bench_granularity_v11 import sql,inner,streamed_insert,optimize_table,querylogs,wait_no_merges
from bench_bucket_cxx_abca import post,normalize_any,assert_same
from bench_filtered_selector_v4_sql import sql_string
SHA="8f3a36846f80d6be5a8604d142e934399f2687094766c57bf105e43977f52858"
METRIC="jenkins_ci_95k_daily"
DAY=86400
SELECTORS={"stage":('{stage="stage-0007"}',1),
           "project":('{project="project-00"}',19),
           "regex":('{branch=~"main|feature-0[0-9]"}',38),
           "whole":("",38)}
def write(file,obj):
    p=pathlib.Path(file);p.parent.mkdir(parents=True,exist_ok=True)
    p.write_text(json.dumps(obj,indent=2,default=str)+"\n")
def data(q):
    return [json.loads(s) for s in sql(q+" FORMAT JSONEachRow").splitlines() if s.strip()]
def attempt(q,obj,key):
    try:obj[key]=data(q)
    except Exception as e:obj[key+"_error"]=str(e)[:1600]
def physical(table):
    result={}
    for target in ("tags","timeranges","samples"):
        t=inner(table,target)
        n=t.split(chr(96))[1]
        out={"name":n,"id":data("SELECT name,type FROM system.columns WHERE database='default' AND table="
                              +sql_string(n)+" AND name='id'")[0]}
        out["parts"]=data("SELECT count() AS parts,sum(rows) AS rows,"
                          "sum(bytes_on_disk) AS bytes,sum(marks) AS marks "
                          "FROM system.parts WHERE active AND database='default' AND table="+sql_string(n))[0]
        result[target]=out
    return result
def query(table,case,days,end):
    sel=SELECTORS[case][0]
    expr=f"avg by(stage)(avg_over_time({METRIC}{sel}[1d]))"
    start=end-(days-1)*DAY
    return (f"SELECT * FROM prometheusQueryRange({table},{sql_string(expr)},"
            f"{start},{end},{DAY}) SETTINGS max_threads=4,"
            "max_memory_usage=3500000000,log_queries=1,log_processors_profiles=1,"
            "query_profiler_cpu_time_period_ns=1000000,query_profiler_real_time_period_ns=1000000 "
            "FORMAT JSONEachRow")
def capture(qids):
    sql("SYSTEM FLUSH LOGS")
    ids="("+",".join(sql_string(x) for x in qids)+")"
    result={}
    attempt("SELECT query_id,name,plan_step_name,count() AS processors,"
            "sum(elapsed_us) AS work_us,sum(input_wait_elapsed_us) AS input_wait_us,"
            "sum(output_wait_elapsed_us) AS output_wait_us,"
            "sum(input_rows) AS input_rows,sum(output_rows) AS output_rows "
            "FROM system.processors_profile_log WHERE query_id IN "+ids+
            " GROUP BY query_id,name,plan_step_name ORDER BY query_id,work_us DESC LIMIT 160",result,"processors")
    classifier="""multiIf(
      arrayExists(s -> position(s,'ReverseIndex')>0 OR position(s,'ColumnUnique')>0 OR position(s,'SerializationLowCardinality')>0 OR position(s,'ColumnLowCardinality')>0,symbols),'01_lowcard_dictionary',
      arrayExists(s -> position(s,'FillingRightJoinSide')>0 OR position(s,'HashJoin')>0 OR position(s,'JoiningTransform')>0,symbols),'02_hash_join',
      arrayExists(s -> position(s,'MergeJoinTransform')>0,symbols),'03_merge_join',
      arrayExists(s -> position(s,'timeSeriesStoreTags')>0 OR position(s,'ContextTimeSeriesTagsCollector')>0,symbols),'04_tag_registration',
      arrayExists(s -> position(s,'MergeTreeReader')>0 OR position(s,'MergeTreeReadTask')>0,symbols),'05_physical_read',
      arrayExists(s -> position(s,'AdaptiveBucketMergingSource')>0 OR position(s,'Aggregator')>0 OR position(s,'AggregatingTransform')>0,symbols),'06_aggregate',
      arrayExists(s -> position(s,'CreatingSets')>0,symbols),'07_id_set',
      '08_other')"""
    attempt("SELECT query_id,"+classifier+" AS category,count() AS samples "
            "FROM system.trace_log WHERE trace_type='CPU' AND query_id IN "+ids+
            " GROUP BY query_id,category ORDER BY query_id,samples DESC LIMIT 100",result,"cpu")
    attempt("SELECT query_id,trace_type,arrayFirst(s -> s != '',symbols) AS leaf,"
            "count() AS samples,sum(size) AS bytes "
            "FROM system.trace_log WHERE query_id IN "+ids+" AND trace_type IN ('CPU','Memory')"
            " GROUP BY query_id,trace_type,leaf ORDER BY query_id,samples DESC LIMIT 240",result,"leaf")
    return result
def main():
    p=argparse.ArgumentParser()
    p.add_argument("--case",choices=SELECTORS,required=True)
    p.add_argument("--days",type=int,required=True)
    p.add_argument("--fixture",required=True)
    p.add_argument("--manifest",required=True)
    p.add_argument("--output",required=True)
    args=p.parse_args()
    m=json.loads(pathlib.Path(args.manifest).read_text())
    assert m["sha256"]==SHA and m["series"]==1330000
    assert args.days in (1,3,7,14)
    expected_points=SELECTORS[args.case][1]*args.days
    end=int(m["end"]); start=end-(args.days-1)*DAY
    expected_grid={start+i*DAY for i in range(args.days)}
    alt="churn_rawid_ts"
    report={"case":args.case,"days":args.days,"fixture_sha":SHA,"errors":[],
            "variants":{"default":"churn_ts","raw_uuid":alt},"physical":{},"phases":[]}
    write(args.output,report)
    try:
        sql("CREATE TABLE "+alt+" ENGINE=TimeSeries SETTINGS recent_samples_ttl_seconds=0,"
            "id_type='Tuple(UInt64, UUID)'")
        ingested=streamed_insert(alt,args.fixture)
        if ingested["rows"]!=1330000:raise AssertionError("Incomplete raw UUID import")
        report["ingest"]=ingested
        for table in ("churn_ts",alt):
            report.setdefault("optimize",{})[table]=optimize_table(table)
        wait_no_merges()
        report["physical"]={table:physical(table) for table in ("churn_ts",alt)}
        for table, expected_type in (("churn_ts","LowCardinality(UUID)"),(alt,"Tuple(UInt64, UUID)")):
            for part in ("tags","samples","timeranges"):
                found=report["physical"][table][part]["id"]["type"]
                if expected_type not in found:
                    raise AssertionError((table,part,"wrong ID",found,expected_type))
            tags=inner(table,"tags")
            check=data("SELECT count() AS n,sum(sipHash64(toString(id))) AS digest,"
                       "countIf(tags['stage']='stage-0007') AS stage_count FROM "+tags)[0]
            if check["n"]!=1330000 or check["stage_count"]!=35000:
                raise AssertionError(("Wrong series metadata",table,check))
            report.setdefault("digest",{})[table]=check
        if report["digest"]["churn_ts"]!=report["digest"][alt]:
            raise AssertionError("Raw UUID altered logical series identity")
        write(args.output,report)
        reference=None
        for phase,table in enumerate(("churn_ts",alt,alt,"churn_ts")):
            trials=[]
            for repeat in range(4):
                qid=f"cpunativeid_{args.case}_{args.days}_{phase}_{repeat}_{os.getenv('GITHUB_RUN_ID','local')}"
                ms,raw=post("http://127.0.0.1:18191",query(table,args.case,args.days,end),qid)
                points=normalize_any(raw)
                if len(points)!=expected_points:
                    raise AssertionError(("Wrong native points",table,len(points),expected_points))
                if {t for _l,t in points}!=expected_grid:
                    raise AssertionError("Native sample times not equal")
                if reference is None:reference=points
                else:assert_same(reference,points,args.case,"raw-uuid")
                trials.append({"qid":qid,"wall_client_ms":round(ms,3),"warmup":repeat==0})
            report["phases"].append({"phase":phase,"table":table,"trials":trials})
            write(args.output,report)
            print("CPUNATIVEID_PHASE",json.dumps({"case":args.case,"days":args.days,
                  "phase":phase,"table":table,"last_ms":trials[-1]["wall_client_ms"]}),flush=True)
        logs={x["query_id"]:x for x in querylogs(f"cpunativeid_{args.case}_{args.days}_")
              if x.get("type")=="QueryFinish"}
        for ph in report["phases"]:
            for trial in ph["trials"]:
                row=logs.get(trial["qid"])
                if not row:raise AssertionError("Missing query log: "+trial["qid"])
                ev=row.get("ProfileEvents",{})
                trial.update({"query_wall_ms":row["query_duration_ms"],
                              "cpu_ms":round(ev.get("OSCPUVirtualTimeMicroseconds",0)/1000,3),
                              "read_rows":row["read_rows"],"read_bytes":row["read_bytes"],
                              "memory_bytes":row["memory_usage"],
                              "selected_marks":ev.get("SelectedMarks"),
                              "compressed_read_bytes":ev.get("ReadCompressedBytes")})
            samples=ph["trials"][1:]
            ph["median_wall_ms"]=statistics.median(x["wall_client_ms"] for x in samples)
            ph["median_cpu_ms"]=statistics.median(x["cpu_ms"] for x in samples)
            ph["peak_query_memory"]=max(x["memory_bytes"] for x in samples)
        before=[x for x in report["phases"] if x["table"]=="churn_ts"]
        after=[x for x in report["phases"] if x["table"]==alt]
        base_ms=statistics.mean(x["median_wall_ms"] for x in before)
        alt_ms=statistics.mean(x["median_wall_ms"] for x in after)
        drift=abs(before[0]["median_wall_ms"]-before[1]["median_wall_ms"])/base_ms
        report["summary"]={"original_ms":round(base_ms,3),"raw_uuid_ms":round(alt_ms,3),
                           "speedup":round(base_ms/alt_ms,4),
                           "original_cpu_ms":round(statistics.mean(x["median_cpu_ms"] for x in before),3),
                           "raw_uuid_cpu_ms":round(statistics.mean(x["median_cpu_ms"] for x in after),3),
                           "original_peak_memory":max(x["peak_query_memory"] for x in before),
                           "raw_uuid_peak_memory":max(x["peak_query_memory"] for x in after),
                           "control_drift":round(drift,4)}
        qids=[report["phases"][0]["trials"][1]["qid"],
              report["phases"][1]["trials"][1]["qid"]]
        report["profiles"]=capture(qids)
        if drift>.25:report["errors"].append({"control_drift":drift})
        report["passed"]=not report["errors"]
    except Exception as e:
        report["errors"].append({"fatal":str(e)[:2000]});report["passed"]=False
    finally:
        write(args.output,report)
        compact={"case":args.case,"days":args.days,"passed":report["passed"],
                 "summary":report.get("summary"),"errors":report["errors"],
                 "physical":report.get("physical")}
        write(str(pathlib.Path(args.output).with_suffix(".compact.json")),compact)
        print("CPUNATIVEID_DONE",json.dumps({"case":args.case,"days":args.days,
              "passed":report["passed"],"summary":report.get("summary"),
              "errors":report["errors"]},separators=(",",":")),flush=True)
    if not report["passed"]:raise SystemExit(1)
if __name__=="__main__":main()
