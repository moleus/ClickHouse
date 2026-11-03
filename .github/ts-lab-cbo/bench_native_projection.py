#!/usr/bin/env python3
"""Native PromQL projection AUTO versus OFF on frozen 1.33m TimeSeries.

Tests whether direct SQL TimeRanges projection speedups survive the native
metadata JOIN. Same binary/fixture/parts; A/B/B/A, 5 warmups and 4 measured
queries per phase; reject >30% A/A drift. Do not force projection.
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
def save(p,v):
    pathlib.Path(p).parent.mkdir(parents=True,exist_ok=True)
    pathlib.Path(p).write_text(json.dumps(v,indent=2,default=str)+"\n")
def query(table,case,start,end,use_projection):
    expr=f'avg by(stage)(avg_over_time(jenkins_ci_95k_daily{FILTERS[case]}[1d]))'
    return (f"SELECT * FROM prometheusQueryRange({table},{sql_string(expr)},{start},{end},{DAY}) "
            f"SETTINGS max_threads=4,max_memory_usage=3500000000,optimize_use_projections={int(use_projection)}")
def main():
    ap=argparse.ArgumentParser()
    ap.add_argument("--manifest",required=True);ap.add_argument("--case",choices=list(FILTERS),required=True)
    ap.add_argument("--out",required=True);ap.add_argument("--table",default="churn_ts")
    args=ap.parse_args()
    m=json.loads(pathlib.Path(args.manifest).read_text())
    if (m["sha256"],m["series"],m["days"])!=(CANONICAL,1330000,14):
        raise AssertionError("Wrong fixture SHA or cardinality")
    root=pathlib.Path(args.out);root.mkdir(parents=True,exist_ok=True)
    report={"scope":"native full PromQL, automatic projection versus disabled; no forced projection",
            "case":args.case,"fixture_sha":m["sha256"],"schema":{},"windows":{},"errors":[]}
    save(root/"report.json",report)
    try:
        sql("SYSTEM START MERGES")
        fix_schema(args.table,report["schema"])
        sql("SYSTEM STOP MERGES")
        save(root/"report.json",report)
        for day in (1,3,7):
            start=int(m["end"])-(day-1)*DAY;end=int(m["end"])
            statements={name:query(args.table,args.case,start,end,enabled)
                        for name,enabled in (("off",False),("auto",True))}
            plans={}
            for name,q in statements.items():
                try:plans[name]=sql("EXPLAIN PLAN indexes=1,projections=1 "+q)[:85000]
                except Exception as e:plans[name]="EXPLAIN ERROR "+str(e)[:1300]
            win={"days":day,"plans":plans,"phases":[],"errors":[]}
            report["windows"][str(day)]=win
            reference=None
            for ix,mode in enumerate(("off","auto","auto","off")):
                ids=[];precisions=[]
                for trial in range(9):
                    qid=f"native_projection_{args.case}_{day}_{ix}_{trial}_{os.getenv('GITHUB_RUN_ID','local')}"
                    try:
                        ms,body=post("http://127.0.0.1:18191",statements[mode]+" FORMAT JSONEachRow",qid)
                        output=normalize_any(body)
                        if len(output)!=POINTS[args.case]*day:
                            raise AssertionError(f"Wrong point count {len(output)}")
                        expected_ts={start+i*DAY for i in range(day)}
                        if {key[1] for key in output}!=expected_ts:
                            raise AssertionError("Wrong timestamp grid")
                        if reference is None:
                            reference=output;comparison={"max_abs_error":0.0}
                        else:
                            comparison=assert_same(reference,output,args.case,mode)
                        ids.append({"query_id":qid,"mode":mode,"warmup":trial<5,
                                    "client_ms":round(ms,3),"max_abs_error":comparison["max_abs_error"]})
                    except Exception as e:
                        win["errors"].append({"phase":ix,"mode":mode,"trial":trial,"error":str(e)[:1400]})
                sql("SYSTEM FLUSH LOGS")
                records={}
                for row in querylogs(f"native_projection_{args.case}_{day}_{ix}_"):
                    if row.get("type")=="QueryFinish":records[row["query_id"]]=row
                for trial in ids:
                    q=records.get(trial["query_id"])
                    if not q:
                        win["errors"].append({"missing_querylog":trial["query_id"]});continue
                    e=q.get("ProfileEvents",{})
                    trial.update({"wall_ms":q["query_duration_ms"],"cpu_ms":e.get("OSCPUVirtualTimeMicroseconds",0)/1000,
                                  "peak_query_bytes":q.get("memory_usage"),"read_rows":q.get("read_rows"),
                                  "read_bytes":q.get("read_bytes"),"selected_marks":e.get("SelectedMarks")})
                measured=[t for t in ids if not t["warmup"] and "wall_ms" in t]
                if len(measured)!=4:win["errors"].append({"wrong_measurements":len(measured)})
                phase={"mode":mode,"index":ix,"trials":ids}
                if len(measured)==4:
                    for metric in ("wall_ms","cpu_ms","peak_query_bytes","read_rows","selected_marks"):
                        phase[metric+"_median"]=statistics.median(t.get(metric,0) or 0 for t in measured)
                win["phases"].append(phase)
                save(root/"report.json",report)
            if len(win["phases"])==4 and all("wall_ms_median" in p for p in win["phases"]):
                a,b,b2,a2=win["phases"]
                baseline=(a["wall_ms_median"]+a2["wall_ms_median"])/2
                alternative=(b["wall_ms_median"]+b2["wall_ms_median"])/2
                drift=abs(a["wall_ms_median"]-a2["wall_ms_median"])/baseline
                win["comparison"]={"off_wall_ms":baseline,"auto_wall_ms":alternative,
                                   "speedup":round(baseline/alternative,4),
                                   "baseline_drift":round(drift,4),
                                   "auto_explain_projection":"time_by_max" in plans["auto"],
                                   "off_explain_projection":"time_by_max" in plans["off"]}
                if drift>0.30:win["errors"].append({"invalid_baseline_drift":drift})
            print("NATIVE_PROJECTION_WINDOW",json.dumps({"case":args.case,"day":day,
                "comparison":win.get("comparison"),"errors":win["errors"]},separators=(",",":")),flush=True)
            save(root/"report.json",report)
        report["passed"]=all(not w["errors"] and w.get("comparison") for w in report["windows"].values())
    except Exception as e:
        report["errors"].append(str(e)[:1900]);report["passed"]=False
    finally:
        save(root/"report.json",report)
        print("NATIVE_PROJECTION_DONE",json.dumps({"case":args.case,"passed":report["passed"],
            "errors":report["errors"]},separators=(",",":")),flush=True)
    if not report["passed"]:raise SystemExit(1)
if __name__=="__main__":main()
