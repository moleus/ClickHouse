#!/usr/bin/env python3
"""Matched 1.33m TAGS Map vs materialized stage column estimator experiment.

Same metric IDs and TimeSeries semantics. Test selectivity estimates separately
from execution performance. Do not assume adding a label column improves native
latency; prior broad workload reported regressions.
"""
import argparse,json,os,pathlib,random,re,statistics,sys
sys.path.insert(0,".github/ts-lab-perf")
sys.path.insert(0,".github/ts-lab-layout")
sys.path.insert(0,".github/ts-lab-bench")
from bench_granularity_v11 import sql,inner,streamed_insert,optimize_table,wait_no_merges,querylogs
from bench_layout_full import CANONICAL
from bench_bucket_cxx_abca import normalize_any,assert_same,post
from bench_filtered_selector_v4_sql import sql_string
METRIC="jenkins_ci_95k_daily"
def write(path,obj):
    pathlib.Path(path).parent.mkdir(parents=True,exist_ok=True)
    pathlib.Path(path).write_text(json.dumps(obj,indent=2,default=str)+"\n")
def literal(x):return "'"+str(x).replace("'","\\'")+"'"
def catalog(table):
    out={}
    for name in ("tags","timeranges","samples"):
        physical=inner(table,name);raw=physical.split(chr(96))[1]
        queries={
          "columns":"SELECT name,type,statistics FROM system.columns WHERE database='default' AND table="+literal(raw)+" FORMAT JSONEachRow",
          "part_estimates":"SELECT column,statistics,estimates.cardinality AS ndv,estimates.min AS min,estimates.max AS max,rows,marks FROM system.parts_columns WHERE active AND database='default' AND table="+literal(raw)+" FORMAT JSONEachRow",
          "parts":"SELECT name,rows,marks,bytes_on_disk FROM system.parts WHERE active AND database='default' AND table="+literal(raw)+" FORMAT JSONEachRow"
        }
        entry={"physical":physical}
        for k,q in queries.items():
            try:entry[k]=[json.loads(s) for s in sql(q).splitlines() if s.strip()]
            except Exception as e:entry[k+"_error"]=str(e)[:1000]
        out[name]=entry
    return out
def sqlquery(table,custom_column,method,stats_on):
    tag=inner(table,"tags");times=inner(table,"timeranges")
    filt="stage='stage-0007'" if custom_column else "tags['stage']='stage-0007'"
    if method=="tags":
        stmt=f"SELECT count() AS n FROM {tag} WHERE metric_name={literal(METRIC)} AND {filt}"
    else:
        stmt=(f"SELECT count() AS n FROM (SELECT t.id FROM (SELECT id FROM {tag} "
              f"WHERE metric_name={literal(METRIC)} AND {filt}) t "
              f"LEFT JOIN (SELECT id,toUInt8(1) AS matched FROM {times}) r USING(id) "
              f"WHERE r.matched=1)")
    return stmt+f" SETTINGS use_statistics={stats_on},max_threads=4,join_algorithm='auto',max_memory_usage=3500000000"
def inspect(table,stage_column,method,stats_on,run_id):
    query=sqlquery(table,stage_column,method,stats_on)
    entry={"sql":query}
    try:entry["explain"]=sql("EXPLAIN PLAN indexes=1 "+query)[:100000]
    except Exception as e:entry["explain_error"]=str(e)[:1600]
    estimated=[x.strip() for x in entry.get("explain","").splitlines() if "rows estimated" in x or "Algorithm:" in x]
    entry["estimated"]=estimated[:12]
    rec=[]
    for i in range(6):
        qid=f"cbo_stats_{run_id}_{method}_{table}_{stats_on}_{i}"
        try:
            result=json.loads(sql(query+" FORMAT JSONEachRow",qid).strip())
            if result["n"]!=35000:raise AssertionError(f"Expected 35000 rows, got {result['n']}")
            rec.append({"qid":qid,"count":result["n"],"warmup":i<2})
        except Exception as exc:
            entry["error"]=str(exc)[:1800]
            return entry
    try:
        logs=querylogs(f"cbo_stats_{run_id}_{method}_{table}_{stats_on}_")
        finished={x["query_id"]:x for x in logs if x.get("type")=="QueryFinish"}
        for row in rec:
            z=finished.get(row["qid"],{})
            ev=z.get("ProfileEvents",{})
            row["wall_ms"]=z.get("query_duration_ms")
            row["cpu_ms"]=ev.get("OSCPUVirtualTimeMicroseconds",0)/1000
            row["memory_bytes"]=z.get("memory_usage")
            row["read_rows"]=z.get("read_rows")
        measured=[x for x in rec[2:] if x.get("wall_ms") is not None]
        entry["median_ms"]=statistics.median(x["wall_ms"] for x in measured)
        entry["median_cpu_ms"]=statistics.median(x["cpu_ms"] for x in measured)
        entry["median_read_rows"]=statistics.median(x["read_rows"] for x in measured)
    except Exception as exc:entry["querylog_error"]=str(exc)[:1600]
    entry["trials"]=rec
    return entry
def native(table,meta):
    # Genuine PromQL parity control, separate from TAGS-only SQL diagnostics.
    expr=f"avg by(stage)(avg_over_time({METRIC}{{stage=\"stage-0007\"}}[1d]))"
    end=int(meta["end"]);start=end-13*86400
    q=f"SELECT * FROM prometheusQueryRange({table},{literal(expr)},{start},{end},86400) SETTINGS max_threads=4 FORMAT JSONEachRow"
    ms,raw=post("http://127.0.0.1:18191",q)
    points=normalize_any(raw)
    if len(points)!=14:raise AssertionError(("Unexpected native point count",table,len(points)))
    return points,{"points":len(points),"wall_ms":ms,"sql":q}
def main():
    p=argparse.ArgumentParser()
    p.add_argument("--manifest",required=True)
    p.add_argument("--fixture",required=True)
    p.add_argument("--out",required=True)
    a=p.parse_args()
    root=pathlib.Path(a.out);root.mkdir(parents=True,exist_ok=True)
    meta=json.loads(pathlib.Path(a.manifest).read_text())
    assert meta["sha256"]==CANONICAL and meta["series"]==1330000
    table="tslab_cbo_stage_stats"
    report={"fixture_sha":CANONICAL,"source":"synthetic Jenkins 1.33m","cases":{},"errors":[]}
    write(root/"report.json",report)
    try:
        sql("CREATE TABLE "+table+" ENGINE=TimeSeries SETTINGS recent_samples_ttl_seconds=0,tags_to_columns={'stage':'stage'}")
        imp=streamed_insert(table,a.fixture)
        if imp["rows"]!=1330000:raise AssertionError("Partial candidate import")
        report["ingest"]=imp
        for t in ("churn_ts",table):
            report.setdefault("optimize",{})[t]=optimize_table(t)
        report["catalog_before"]={t:catalog(t) for t in ("churn_ts",table)}
        for t in ("churn_ts",table):
            for name in ("tags","timeranges"):
                target=inner(t,name)
                try:
                    sql("ALTER TABLE "+target+" MATERIALIZE STATISTICS ALL SETTINGS mutations_sync=2")
                    report.setdefault("materialize",{})[t+"/"+name]="ok"
                except Exception as exc:
                    report.setdefault("materialize",{})[t+"/"+name]=str(exc)[:1400]
        wait_no_merges()
        report["catalog_after"]={t:catalog(t) for t in ("churn_ts",table)}
        baseline,binfo=native("churn_ts",meta)
        selected,sinfo=native(table,meta)
        report["native_parity"]=assert_same(baseline,selected,"stage-column","native")
        report["native_parity"]["baseline"]=binfo
        report["native_parity"]["candidate"]=sinfo
        for t,has_stage in [("churn_ts",False),(table,True)]:
            for mode in ["tags","join"]:
                for enabled in [0,1]:
                    k=f"{t}-{mode}-stats{enabled}"
                    report["cases"][k]=inspect(t,has_stage,mode,enabled,os.getenv("GITHUB_RUN_ID","local"))
                    write(root/"report.json",report)
        # Strict paired A/B/B/A: same physical dataset, alternating the
        # optimizer statistics setting to separate planner changes from cache drift.
        report["paired"]={}
        for t,has_stage in [("churn_ts",False),(table,True)]:
            phases=[]
            for ix,enabled in enumerate([0,1,1,0]):
                stage=f"P{ix}"
                detail=inspect(t,has_stage,"join",enabled,
                    os.getenv("GITHUB_RUN_ID","local")+"_"+stage)
                if "error" in detail or "querylog_error" in detail:
                    raise RuntimeError("Paired trial failure: "+str(detail.get("error",detail.get("querylog_error"))))
                phases.append({"phase":stage,"use_statistics":enabled,
                               "wall_ms":detail["median_ms"],
                               "cpu_ms":detail["median_cpu_ms"],
                               "read_rows":detail["median_read_rows"],
                               "estimated":detail.get("estimated",[])})
                report["paired"][t]={"phases":phases}
                write(root/"report.json",report)
            off=[x for x in phases if x["use_statistics"]==0]
            on=[x for x in phases if x["use_statistics"]==1]
            off_ms=statistics.mean(x["wall_ms"] for x in off)
            on_ms=statistics.mean(x["wall_ms"] for x in on)
            drift=abs(off[0]["wall_ms"]-off[1]["wall_ms"])/off_ms
            report["paired"][t]["summary"]={
                "statistics_off_ms":round(off_ms,3),
                "statistics_on_ms":round(on_ms,3),
                "speedup":round(off_ms/on_ms,4),
                "baseline_drift":round(drift,4),
                "off_cpu_ms":round(statistics.mean(x["cpu_ms"] for x in off),3),
                "on_cpu_ms":round(statistics.mean(x["cpu_ms"] for x in on),3)}
            if drift>.30:
                report["errors"].append({"table":t,"reason":"Over 30% A/B/B/A control drift","drift":drift})
        report["passed"]=(all("error" not in x for x in report["cases"].values())
                          and len(report["cases"])==8 and not report["errors"])
    except Exception as exc:
        report["errors"].append(str(exc)[:2000]);report["passed"]=False
    finally:
        write(root/"report.json",report)
        digest={"passed":report.get("passed"),"errors":report["errors"],"fixture_sha":CANONICAL,
                "cases":{k:{"estimated":v.get("estimated"),"median_ms":v.get("median_ms"),
                            "median_cpu_ms":v.get("median_cpu_ms"),"error":v.get("error")}
                        for k,v in report["cases"].items()}}
        write(root/"compact.json",digest)
        digest["paired"]={t:z.get("summary") for t,z in report.get("paired",{}).items()}
        print("CBO_STATS_RESULT",json.dumps(digest,separators=(",",":")),flush=True)
    if not report["passed"]:raise SystemExit(1)
if __name__=="__main__":main()
