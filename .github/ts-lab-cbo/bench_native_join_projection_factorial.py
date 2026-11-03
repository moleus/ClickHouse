#!/usr/bin/env python3
"""Native 2x2 factorial: time projection AUTO/OFF x JOIN merge/HASH.

An isolated source-pinned O1 ClickHouse server, fixed 1.33m fixture.
Eight balanced phases A/B/C/D/D/C/B/A, three warmups and four measured
repetitions per phase. Native full-PromQL outputs must agree exactly in
labels and timestamps and numerically within established tolerances.
"""
import argparse,json,os,pathlib,statistics,sys
sys.path.insert(0,".github/ts-lab-perf")
sys.path.insert(0,".github/ts-lab-layout")
sys.path.insert(0,".github/ts-lab-bench")
sys.path.insert(0,".github/ts-lab-cbo")
from bench_time_projection import fix_schema
from bench_granularity_v11 import sql,querylogs
from bench_bucket_cxx_abca import normalize_any,assert_same,post
from bench_filtered_selector_v4_sql import sql_string
from bench_layout_full import CANONICAL
DAY=86400
FILTERS={"stage":'{stage="stage-0007"}',"project":'{project="project-00"}'}
POINTS={"stage":1,"project":19}
MODES=("off_merge","auto_merge","off_hash","auto_hash")
ORDER=("off_merge","auto_merge","auto_hash","off_hash","off_hash","auto_hash","auto_merge","off_merge")
def save(p,v):
    pathlib.Path(p).parent.mkdir(parents=True,exist_ok=True)
    pathlib.Path(p).write_text(json.dumps(v,indent=2,default=str)+"\n")
def quote(x):return "'"+str(x).replace("'","\\'")+"'"
def query(table,case,start,end,mode):
    expr=f'avg by(stage)(avg_over_time(jenkins_ci_95k_daily{FILTERS[case]}[1d]))'
    use=int(mode.startswith("auto"))
    settings=f"max_threads=4,max_memory_usage=3500000000,optimize_use_projections={use}"
    if mode.endswith("hash"):
        settings+=",join_algorithm='hash',optimize_read_in_order=0"
    return (f"SELECT * FROM prometheusQueryRange({table},{sql_string(expr)},{start},{end},{DAY}) "
            f"SETTINGS {settings}")
def physical(qid):
    out={}
    queries={
        "processors":"SELECT plan_step_name,name,count() AS processors,sum(elapsed_us) AS work_us,"
                     "sum(input_wait_elapsed_us) AS input_wait_us,sum(output_wait_elapsed_us) AS output_wait_us,"
                     "sum(input_rows) AS input_rows,sum(output_rows) AS output_rows FROM system.processors_profile_log "
                     f"WHERE query_id={quote(qid)} GROUP BY plan_step_name,name ORDER BY work_us DESC LIMIT 120 FORMAT JSONEachRow",
        "trace_stacks":"SELECT trace_type,arrayStringConcat(arrayReverse(arrayFilter(x -> x != '', symbols)),';') AS stack,"
                       "count() AS samples FROM system.trace_log "
                       f"WHERE query_id={quote(qid)} GROUP BY trace_type,stack ORDER BY samples DESC LIMIT 180 FORMAT JSONEachRow"}
    for key,q in queries.items():
        try:out[key]=[json.loads(x) for x in sql(q).splitlines() if x]
        except Exception as e:out[key+"_error"]=str(e)[:900]
    return out
def main():
    p=argparse.ArgumentParser()
    p.add_argument("--manifest",required=True);p.add_argument("--case",choices=list(FILTERS),required=True)
    p.add_argument("--days",type=int,default=7,choices=(3,7))
    p.add_argument("--table",default="churn_ts");p.add_argument("--out",required=True)
    a=p.parse_args()
    m=json.loads(pathlib.Path(a.manifest).read_text())
    assert (m["sha256"],m["series"],m["days"])==(CANONICAL,1330000,14)
    out=pathlib.Path(a.out);out.mkdir(parents=True,exist_ok=True)
    end=int(m["end"]);start=end-(a.days-1)*DAY
    report={"scope":"NATIVE full PromQL, two-by-two factorial; no source/binary change between modes",
            "fixture_sha":CANONICAL,"case":a.case,"days":a.days,"schema":{},"phases":[],"errors":[]}
    save(out/"report.json",report)
    try:
        sql("SYSTEM START MERGES")
        fix_schema(a.table,report["schema"])
        sql("SYSTEM STOP MERGES")
        statements={mode:query(a.table,a.case,start,end,mode) for mode in MODES}
        report["plans"]={}
        for mode,q in statements.items():
            try:report["plans"][mode]=sql("EXPLAIN PLAN indexes=1,projections=1 "+q)[:90000]
            except Exception as e:report["plans"][mode]="EXPLAIN ERROR "+str(e)[:1200]
        save(out/"report.json",report)
        reference=None
        for ix,mode in enumerate(ORDER):
            trials=[]
            for t in range(7):
                qid=f"nativefactor_{a.case}_{a.days}_{ix}_{t}_{os.getenv('GITHUB_RUN_ID','local')}"
                try:
                    ms,body=post("http://127.0.0.1:18191",statements[mode]+" FORMAT JSONEachRow",qid)
                    data=normalize_any(body)
                    if len(data)!=POINTS[a.case]*a.days:
                        raise AssertionError(f"Wrong points: {len(data)}")
                    if {key[1] for key in data}!={start+i*DAY for i in range(a.days)}:
                        raise AssertionError("Wrong timestamps")
                    if reference is None:reference=data;precision={"max_abs_error":0}
                    else:precision=assert_same(reference,data,a.case,mode)
                    trials.append({"qid":qid,"client_ms":round(ms,3),"warmup":t<3,
                                   "max_abs_error":precision["max_abs_error"]})
                except Exception as e:
                    report["errors"].append({"phase":ix,"mode":mode,"trial":t,"error":str(e)[:1400]})
                save(out/"report.json",report)
            sql("SYSTEM FLUSH LOGS")
            logs={x["query_id"]:x for x in querylogs(f"nativefactor_{a.case}_{a.days}_{ix}_") if x.get("type")=="QueryFinish"}
            for tr in trials:
                q=logs.get(tr["qid"])
                if not q:
                    report["errors"].append({"missing_query_log":tr["qid"]});continue
                ev=q.get("ProfileEvents",{})
                tr.update({"wall_ms":q["query_duration_ms"],"cpu_ms":ev.get("OSCPUVirtualTimeMicroseconds",0)/1000,
                           "peak_memory_bytes":q.get("memory_usage"),"read_rows":q.get("read_rows"),
                           "read_bytes":q.get("read_bytes"),"selected_marks":ev.get("SelectedMarks")})
            measured=[tr for tr in trials if not tr["warmup"] and "wall_ms" in tr]
            phase={"variant":mode,"index":ix,"trials":trials}
            if len(measured)!=4:report["errors"].append({"incomplete_phase":ix,"measured":len(measured)})
            else:
                for metric in ("wall_ms","cpu_ms","peak_memory_bytes","read_rows","selected_marks"):
                    phase[metric+"_median"]=statistics.median(tr.get(metric,0) or 0 for tr in measured)
                # Source CPU/processor profiles are ONE representative trial, not phase median.
                phase["representative_profile_qid"]=measured[1]["qid"]
                phase["representative_profile"]=physical(measured[1]["qid"])
            report["phases"].append(phase)
            save(out/"report.json",report)
            print("NATIVE_FACTOR_PHASE",json.dumps({"case":a.case,"day":a.days,"variant":mode,
                "phase":ix,"wall_ms_median":phase.get("wall_ms_median"),
                "cpu_ms_median":phase.get("cpu_ms_median"),"read_rows":phase.get("read_rows_median"),
                "errors":len(report["errors"])},separators=(",",":")),flush=True)
        comparisons={};by={}
        for mode in MODES:
            phases=[x for x in report["phases"] if x["variant"]==mode]
            if len(phases)!=2 or any("wall_ms_median" not in x for x in phases):
                report["errors"].append({"missing_variant_phases":mode});continue
            ms=[x["wall_ms_median"] for x in phases];baseline=statistics.mean(ms)
            drift=abs(ms[0]-ms[1])/baseline
            by[mode]={"wall_ms":baseline,"cpu_ms":statistics.mean(x["cpu_ms_median"] for x in phases),
                      "read_rows":statistics.mean(x["read_rows_median"] for x in phases),
                      "memory_bytes":statistics.mean(x["peak_memory_bytes_median"] for x in phases),
                      "drift":drift}
            if drift>0.30:report["errors"].append({"unstable_variant":mode,"drift":drift})
        for left,right in (("off_merge","auto_merge"),("off_hash","auto_hash"),
                           ("off_merge","off_hash"),("auto_merge","auto_hash")):
            if left in by and right in by:
                comparisons[left+"__vs__"+right]={"speedup":round(by[left]["wall_ms"]/by[right]["wall_ms"],4),
                    "left_ms":by[left]["wall_ms"],"right_ms":by[right]["wall_ms"]}
        report["summary"]={"variants":by,"comparisons":comparisons}
        report["passed"]=not report["errors"] and len(by)==4
    except Exception as e:
        report["errors"].append(str(e)[:1800]);report["passed"]=False
    finally:
        save(out/"report.json",report)
        print("NATIVE_FACTOR_DONE",json.dumps({"case":a.case,"days":a.days,"passed":report["passed"],
            "summary":report.get("summary"),"errors":report["errors"][:5]},separators=(",",":")),flush=True)
    if not report["passed"]:raise SystemExit(1)
if __name__=="__main__":main()
