#!/usr/bin/env python3
"""Patched selector native PromQL JOIN override, with unchanged default A/B/B/A control.

Requires patched C++ binary; never mix unpatched baseline and new compile flags
when reporting JOIN speedup. Validate complete labels/timestamps and close numeric
values, not hash-only digests. Used only with synthetic fixed Jenkins fixture.
"""
import argparse,json,os,pathlib,random,statistics,sys,time
sys.path.insert(0,".github/ts-lab-perf")
sys.path.insert(0,".github/ts-lab-bench")
from bench_granularity_v11 import sql,querylogs
from bench_bucket_cxx_abca import normalize_any,assert_same,post
from bench_filtered_selector_v4_sql import sql_string
METRIC="jenkins_ci_95k_daily"
DAY=86400
FILTERS={
 "stage":'{stage="stage-0007"}',
 "project":'{project="project-00"}',
 "regex":'{branch=~"main|feature-0[0-9]"}',
 "whole":""
}
ALGORITHMS={
 "default":"",
 "merge":"join_algorithm='full_sorting_merge'",
 "hash":"join_algorithm='hash',optimize_read_in_order=0",
 "auto":"join_algorithm='auto',optimize_read_in_order=0",
 "parallel_hash":"join_algorithm='parallel_hash',optimize_read_in_order=0",
 "merge_no_order":"join_algorithm='full_sorting_merge',optimize_read_in_order=0"
}
def write(p,obj):
    pathlib.Path(p).parent.mkdir(parents=True,exist_ok=True)
    pathlib.Path(p).write_text(json.dumps(obj,indent=2,default=str)+"\n")
def query(table,selector,start,end,variant):
    expr=f"avg by(stage)(avg_over_time({METRIC}{FILTERS[selector]}[1d]))"
    settings="max_threads=4,max_memory_usage=3500000000"
    if ALGORITHMS[variant]:settings+=","+ALGORITHMS[variant]
    return (f"SELECT * FROM prometheusQueryRange({table},{sql_string(expr)},"
            f"{start},{end},{DAY}) SETTINGS {settings} FORMAT JSONEachRow")
def profile(qid,item):
    try:
        qlogs=querylogs(qid)
        item["querylog"]=next((x for x in qlogs if x.get("type")=="QueryFinish"),{})
    except Exception as e:item["querylog_error"]=str(e)[:1000]
    for label,query_text in {
        "processors":"SELECT plan_step_name,name,count() AS processors,sum(elapsed_us) AS work_us,sum(input_wait_elapsed_us) AS input_wait_us,sum(output_wait_elapsed_us) AS output_wait_us,sum(input_rows) AS rows_in,sum(output_rows) AS rows_out FROM system.processors_profile_log WHERE query_id="+sql_string(qid)+" GROUP BY plan_step_name,name ORDER BY work_us DESC LIMIT 80 FORMAT JSONEachRow",
        "flame_stacks":"SELECT trace_type,arrayStringConcat(arrayReverse(arrayFilter(x -> x != '', symbols)),';') AS stack,count() AS samples,sum(size) AS bytes FROM system.trace_log WHERE query_id="+sql_string(qid)+" GROUP BY trace_type,stack ORDER BY samples DESC LIMIT 250 FORMAT JSONEachRow",
        "predicates":"SELECT table,index_names,total_granules,granules_after,index_selectivities,predicate_expression,input_rows,passed_rows FROM system.predicate_statistics_log WHERE query_id="+sql_string(qid)+" LIMIT 150 FORMAT JSONEachRow",
    }.items():
        try:item[label]=[json.loads(s) for s in sql(query_text).splitlines() if s.strip()]
        except Exception as e:item[label+"_error"]=str(e)[:1400]
def main():
    p=argparse.ArgumentParser()
    p.add_argument("--table",default="churn_ts")
    p.add_argument("--manifest",required=True)
    p.add_argument("--case",required=True,choices=list(FILTERS))
    p.add_argument("--out",required=True)
    p.add_argument("--windows",default="1,14")
    args=p.parse_args()
    manifest=json.loads(pathlib.Path(args.manifest).read_text())
    assert manifest["series"]==1330000 and manifest["days"]==14
    root=pathlib.Path(args.out);root.mkdir(parents=True,exist_ok=True)
    report={"case":args.case,"source_sha":manifest["sha256"],"windows":{},"errors":[],
            "interpretation":"Native PromQL JOIN settings compared WITHIN the patched binary only"}
    write(root/"report.json",report)
    for days in [int(x) for x in args.windows.split(",")]:
        end=int(manifest["end"]);start=end-(days-1)*DAY
        item={"days":days,"trials":[],"errors":[],"plan":{}}
        report["windows"][str(days)]=item
        reference=None
        variants=["default","hash","hash","default","auto","merge",
                  "parallel_hash","merge_no_order","default"]
        for n,v in enumerate(variants):
            q=query(args.table,args.case,start,end,v)
            if v not in item["plan"]:
                try:item["plan"][v]=sql("EXPLAIN PLAN indexes=1 "+q.replace(" FORMAT JSONEachRow",""))[:90000]
                except Exception as exc:item["plan"][v+"_error"]=str(exc)[:1400]
            qid=f"nativecbo_{args.case}_{days}_{n}_{os.getenv('GITHUB_RUN_ID','local')}"
            try:
                ms,body=post("http://127.0.0.1:18191",q,qid)
                output=normalize_any(body)
                expected_per_day={"whole":38,"regex":38,"project":19,"stage":1}[args.case]
                expected_points=expected_per_day*days
                if len(output)!=expected_points:
                    raise AssertionError(("Incomplete native PromQL result",args.case,days,
                                          len(output),expected_points))
                expected_grid={start+i*DAY for i in range(days)}
                actual_grid={key[1] for key in output}
                if actual_grid!=expected_grid:
                    raise AssertionError(("Wrong timestamps",args.case,days,
                                          sorted(actual_grid),sorted(expected_grid)))
                if reference is None:reference=output;precision={"max_abs_error":0,"points":len(output)}
                else:precision=assert_same(reference,output,args.case,v)
                rec={"variant":v,"query_id":qid,"ms":round(ms,3),
                     "points":len(output),"max_abs_error":precision["max_abs_error"]}
                item["trials"].append(rec)
                print("NATIVE_JOIN_TRIAL",json.dumps({"case":args.case,"days":days,**rec}),flush=True)
            except Exception as e:
                item["errors"].append({"variant":v,"error":str(e)[:1600]})
                print("NATIVE_JOIN_ERROR",json.dumps(item["errors"][-1]),flush=True)
            write(root/"report.json",report)
        sql("SYSTEM FLUSH LOGS")
        arr_a=[x for x in item["trials"] if x["variant"]=="default"]
        arr_b=[x for x in item["trials"] if x["variant"]=="hash"]
        if len(arr_a)==3 and len(arr_b)==2:
            a=[x["ms"] for x in arr_a];b=[x["ms"] for x in arr_b]
            item["comparison"]={"default_p50_ms":statistics.median(a),
                "hash_p50_ms":statistics.median(b),
                "speedup_default_over_hash":round(statistics.median(a)/statistics.median(b),4),
                "baseline_drift":round(abs(a[0]-a[-1])/statistics.mean(a),4)}
            if item["comparison"]["baseline_drift"]>.30:
                item["errors"].append({"drift":"Over 30% baseline drift"})
            # A matching result does not prove that an explicit algorithm took effect.
            # Keep the full EXPLAIN and CPU stacks; only label a gain when the
            # physical operator agrees with the configured algorithm.
        for rec in item["trials"]:
            if rec["variant"] in ("default","hash") and len(item.get("profiles",{}))<4:
                out={}
                profile(rec["query_id"],out)
                item.setdefault("profiles",{})[rec["query_id"]]=out
        write(root/"report.json",report)
        print("NATIVE_JOIN_WINDOW",json.dumps({"case":args.case,"days":days,
              "compared":item.get("comparison"),"errors":item["errors"]}),flush=True)
    report["passed"]=all(not x["errors"] and x.get("comparison") for x in report["windows"].values())
    write(root/"report.json",report)
    write(root/"compact.json",{"case":args.case,"passed":report["passed"],"errors":report["errors"],
          "windows":{k:{"comparison":v.get("comparison"),"errors":v["errors"]} for k,v in report["windows"].items()}})
    if not report["passed"]:raise SystemExit(1)
if __name__=="__main__":
    main()
