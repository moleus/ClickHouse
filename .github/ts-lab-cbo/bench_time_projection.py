#!/usr/bin/env python3
"""Physical time index A/B/B/A on canonical TimeSeries TimeRanges (1.33m IDs).

Projection is experimental and uses AggregatingMergeTree
deduplicate_merge_projection_mode='rebuild'. Read-only benchmark after full
materialization. Not proof of correct native incremental TimeRanges updates.
"""
import argparse,json,os,pathlib,statistics,sys
sys.path.insert(0,".github/ts-lab-perf")
sys.path.insert(0,".github/ts-lab-layout")
from bench_granularity_v11 import sql,inner,querylogs,wait_no_merges
from bench_layout_full import CANONICAL
DAY=86400
def save(p,v):
    pathlib.Path(p).parent.mkdir(parents=True,exist_ok=True)
    pathlib.Path(p).write_text(json.dumps(v,indent=2,default=str)+"\n")
def sq(s):return "'"+str(s).replace("'","\\'")+"'"
def run(q,qid):
    raw=sql(q+" FORMAT JSONEachRow",qid)
    return json.loads(raw.strip())
def explain(q):
    try:return sql("EXPLAIN PLAN indexes=1,projections=1 "+q)[:65000]
    except Exception as e:return "EXPLAIN_ERROR "+str(e)[:1200]
def make_query(table,start,end,kind,setting):
    tr=inner(table,"timeranges");tags=inner(table,"tags")
    temporal=(f"max_time>=fromUnixTimestamp64Milli({start*1000}) "
              f"AND min_time<=fromUnixTimestamp64Milli({end*1000})")
    if kind=="read":
        core=f"SELECT count() AS n FROM {tr} WHERE {temporal}"
    else:
        core=(f"SELECT count() AS n FROM {tags} t INNER JOIN "
              f"(SELECT id FROM {tr} WHERE {temporal}) r USING(id)")
    opts={"none":"optimize_use_projections=0",
          "cost":"optimize_use_projections=1",
          "force":"optimize_use_projections=1,force_optimize_projection=1"}
    if kind=="join":
        opts[setting]+=",join_algorithm='hash',use_statistics=1"
    return core+f" SETTINGS max_threads=4,max_memory_usage=3500000000,optimize_trivial_count_query=0,{opts[setting]}"
def fix_schema(table,report):
    tr=inner(table,"timeranges")
    report["target"]=tr
    report["before"]={"create":sql("SHOW CREATE TABLE "+tr+" FORMAT TSVRaw")}
    commands=[
      ("projection_merge_setting",f"ALTER TABLE {tr} MODIFY SETTING deduplicate_merge_projection_mode='rebuild'"),
      ("add_minmax",f"ALTER TABLE {tr} ADD INDEX idx_time_max max_time TYPE minmax GRANULARITY 1"),
      ("materialize_minmax",f"ALTER TABLE {tr} MATERIALIZE INDEX idx_time_max SETTINGS mutations_sync=2"),
      ("add_projection",f"ALTER TABLE {tr} ADD PROJECTION time_by_max (SELECT id,min_time,max_time ORDER BY max_time,id)"),
      ("materialize_projection",f"ALTER TABLE {tr} MATERIALIZE PROJECTION time_by_max SETTINGS mutations_sync=2")
    ]
    for label,cmd in commands:
        try:report[label]={"ok":True,"response":sql(cmd)[:800]}
        except Exception as e:
            report[label]={"ok":False,"error":str(e)[:1500]}
            raise
    wait_no_merges()
    raw=tr.split(chr(96))[1]
    report["after"]={}
    report["after"]["parts"]=[json.loads(x) for x in sql("SELECT name,rows,marks,bytes_on_disk FROM system.parts WHERE active AND database='default' AND table="+sq(raw)+" FORMAT JSONEachRow").splitlines() if x.strip()]
    report["after"]["projections"]=[json.loads(x) for x in sql("SELECT name,parent_name,rows,marks,bytes_on_disk FROM system.projection_parts WHERE active AND database='default' AND table="+sq(raw)+" FORMAT JSONEachRow").splitlines() if x.strip()]
def main():
    p=argparse.ArgumentParser()
    p.add_argument("--case",choices=("read","join"),required=True)
    p.add_argument("--manifest",required=True)
    p.add_argument("--output",required=True)
    a=p.parse_args()
    m=json.loads(pathlib.Path(a.manifest).read_text())
    if m["sha256"]!=CANONICAL or m["series"]!=1330000 or m["days"]!=14:
        raise AssertionError("Canonical input mismatch")
    root=pathlib.Path(a.output);root.mkdir(parents=True,exist_ok=True)
    report={"case":a.case,"fixture_sha":m["sha256"],"schema":{},"windows":{},"errors":[]}
    save(root/"report.json",report)
    try:
        fix_schema("churn_ts",report["schema"])
        save(root/"report.json",report)
        for days in (1,3,7,14):
            end=int(m["end"]);start=end-days*DAY
            cases={x:{"sql":make_query("churn_ts",start,end,a.case,x)}
                   for x in ("none","cost","force")}
            for v in cases.values():v["explain"]=explain(v["sql"])
            trials=[];ref=None;failed=[]
            # Same physical table, disable vs force projection. Every phase has
            # warmup+timed trials; alternate A/B/B/A to reject cache drift.
            for ix,which in enumerate(("none","force","force","none")):
                values=[]
                for t in range(5):
                    qid=f"timeprojection_{a.case}_{days}_{ix}_{t}_{os.getenv('GITHUB_RUN_ID','local')}"
                    try:
                        result=run(cases[which]["sql"],qid)
                        if int(result["n"])<=0:raise AssertionError("Empty time window")
                        if ref is None:ref=result
                        if result!=ref:raise AssertionError("Projection changed result rows "+str(result))
                        values.append({"query_id":qid,"n":result["n"],"warmup":t==0})
                    except Exception as e:
                        failed.append({"variant":which,"error":str(e)[:1200]})
                logrows=querylogs(f"timeprojection_{a.case}_{days}_{ix}_")
                by={v["query_id"]:v for v in logrows if v.get("type")=="QueryFinish"}
                for trial in values:
                    qid=trial["query_id"]
                    if qid not in by:raise AssertionError("Missing QueryFinish "+qid)
                    e=by[qid].get("ProfileEvents",{})
                    trial.update({"wall_ms":by[qid]["query_duration_ms"],
                        "cpu_ms":e.get("OSCPUVirtualTimeMicroseconds",0)/1000,
                        "read_rows":by[qid]["read_rows"],
                        "selected_marks":e.get("SelectedMarks"),
                        "query_memory":by[qid]["memory_usage"]})
                measured=[x for x in values if not x["warmup"]]
                if not measured:raise AssertionError("No measured trials")
                trials.append({"variant":which,"phase":ix,
                               "trials":values,
                               "wall_ms":statistics.median(x["wall_ms"] for x in measured),
                               "cpu_ms":statistics.median(x["cpu_ms"] for x in measured),
                               "read_rows":statistics.median(x["read_rows"] for x in measured),
                               "selected_marks":statistics.median(x["selected_marks"] or 0 for x in measured)})
            p0=[x for x in trials if x["variant"]=="none"]
            p1=[x for x in trials if x["variant"]=="force"]
            base_ms=statistics.mean(x["wall_ms"] for x in p0)
            alt_ms=statistics.mean(x["wall_ms"] for x in p1)
            drift=abs(p0[0]["wall_ms"]-p0[1]["wall_ms"])/base_ms
            comparison={"no_projection_ms":base_ms,"forced_projection_ms":alt_ms,
                        "speedup":round(base_ms/alt_ms,4),
                        "drift":round(drift,4),
                        "base_read_rows":statistics.mean(x["read_rows"] for x in p0),
                        "projection_read_rows":statistics.mean(x["read_rows"] for x in p1),
                        "base_marks":statistics.mean(x["selected_marks"] for x in p0),
                        "projection_marks":statistics.mean(x["selected_marks"] for x in p1)}
            if drift>.30:failed.append({"baseline_drift":drift})
            report["windows"][str(days)]={"days":days,"alternatives":cases,
                "phases":trials,"comparison":comparison,"errors":failed}
            print("TIME_PROJECTION_RESULT",json.dumps({"case":a.case,"days":days,
                "comparison":comparison,"errors":failed},separators=(",",":")),flush=True)
            save(root/"report.json",report)
        report["passed"]=len(report["windows"])==4 and all(not x["errors"] for x in report["windows"].values())
    except Exception as e:
        report["errors"].append(str(e)[:1800]);report["passed"]=False
    finally:
        save(root/"report.json",report)
        save(root/"compact.json",{"case":a.case,"passed":report["passed"],
            "errors":report["errors"],"windows":{k:v["comparison"] for k,v in report["windows"].items()}})
        print("TIME_PROJECTION_DONE",json.dumps({"case":a.case,"passed":report["passed"],
            "errors":report["errors"]},separators=(",",":")),flush=True)
    if not report["passed"]:raise SystemExit(1)
if __name__=="__main__":
    main()
