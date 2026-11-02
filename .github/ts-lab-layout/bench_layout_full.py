#!/usr/bin/env python3
"""Same-fixture native TimeSeries compound-ID full query A/B/B/A experiment.

A/default vs B/label-prefix, fresh server per phase, 2 warmups+4 trials.
No private metrics. 1.33m immutable 14d synthetic series; source SHA gate.
Do not infer production speedup from single-host hot page-cache data.
"""
import argparse,hashlib,json,os,pathlib,random,re,statistics,sys,time,urllib.request
sys.path.insert(0,".github/ts-lab-layout")
sys.path.insert(0,".github/ts-lab-bench")
from layout_id_smoke import definition,SCHEMA
from bench_granularity_v11 import streamed_insert,optimize_table,wait_no_merges,stats,inner,querylogs
from bench_stage_cpp_vs_original_vm_95k import (HOST,METRIC,DAY,SELECTORS,
      monitored,server_restart,post,sql_string,assert_same,pct,save,expected_points)

CANONICAL="8f3a36846f80d6be5a8604d142e934399f2687094766c57bf105e43977f52858"
QUERY_SELECTORS={
    "whole":"",
    "stage":'{stage="stage-0007"}',
    "project":'{project="project-00"}',
    "project_stage":'{project="project-01",stage="stage-0007"}',
    "regex":'{branch=~"main|feature-0[0-9]"}',
}
OUTPUT_POINTS={"whole":532,"stage":14,"project":266,"project_stage":14,"regex":532}
def check_disk(folder):
    import shutil
    use=shutil.disk_usage(folder)
    return {"free_bytes":use.free,"total_bytes":use.total,
            "fraction":round(use.free/use.total,5),"passed":use.free/use.total>=.20}
def actual_sql(sql,qid=None):
    ms,body=post(HOST,sql,qid)
    return body.decode(errors="replace")
def system_snapshot(variant):
    snap={"disk":check_disk(os.environ["RUNNER_TEMP"])}
    for name,kind in [("samples","samples"),("tags","tags"),("timeranges","timeranges")]:
        tag="churn_ts" if variant=="baseline" else "churn_"+variant
        raw=stats(tag)
        snap[name]=raw.get(kind)
    rows=actual_sql("SELECT count() AS n FROM system.merges WHERE database='default' FORMAT JSONEachRow")
    snap["active_merges"]=int(json.loads(rows)["n"])
    snap["vmstat"]={z.split()[0]:int(z.split()[1]) for z in
                      pathlib.Path("/proc/vmstat").read_text().splitlines()
                      if z.startswith(("pgfault ","pgmajfault ","pswpin ","pswpout "))}
    return snap
def log_for(qid):
    query=actual_sql("SYSTEM FLUSH LOGS")
    rows=querylogs(qid)
    done=next((x for x in reversed(rows) if x.get("type")=="QueryFinish" and x.get("query_id")==qid),None)
    if not done:raise RuntimeError("Missing QueryFinish: "+qid)
    events=done.get("ProfileEvents",{})
    return {"duration_ms":done.get("query_duration_ms"),"memory_bytes":done.get("memory_usage"),
            "read_rows":done.get("read_rows"),"read_bytes":done.get("read_bytes"),
            "cpu_us":events.get("OSCPUVirtualTimeMicroseconds"),
            "file_open":events.get("FileOpen"),
            "read_compressed_bytes":events.get("ReadCompressedBytes"),
            "text_index_microseconds":events.get("TextIndexReaderTotalMicroseconds")}
def control_case(table,selector,start,end):
    expr=f"avg by(stage)(avg_over_time({METRIC}{QUERY_SELECTORS[selector]}[1d]))"
    return (f"SELECT * FROM prometheusQueryRange({table},{sql_string(expr)},"
            f"{start},{end},{DAY}) SETTINGS max_threads=4 FORMAT JSONEachRow")
def run_phase(table,case,phase,bin_path,restart_helper,reference,meta,output):
    name=f"layout_{os.environ.get('GITHUB_RUN_ID','manual')}_{case}_{phase}"
    pid=server_restart(restart_helper,bin_path,name)
    wait_no_merges()
    start=int(meta["end"])-13*DAY;end=int(meta["end"])
    query=control_case(table,case,start,end)
    snap_before=system_snapshot("baseline" if table=="churn_ts" else table.removeprefix("churn_"))
    if not snap_before["disk"]["passed"] or snap_before["active_merges"]:
        raise RuntimeError("Invalid environment before query, disk/merges: "+str(snap_before))
    trials=[]
    for i in range(6):
        qid=name+f"_{i}"
        res=monitored(query,qid,pid)
        if not res["ok"]:raise RuntimeError("query failed: "+str(res.get("error")))
        got=res.pop("points")
        if len(got)!=OUTPUT_POINTS[case]:
            raise RuntimeError(f"wrong output count {len(got)} vs {OUTPUT_POINTS[case]} {case}")
        if reference is None:reference=got
        else:assert_same(reference,got,case,phase)
        res["query_log"]=log_for(qid)
        res["warmup"]=i<2
        trials.append(res)
    snap_after=system_snapshot("baseline" if table=="churn_ts" else table.removeprefix("churn_"))
    if not snap_after["disk"]["passed"] or snap_after["active_merges"]:
        raise RuntimeError("Invalid environment after query")
    for kind in ("samples","tags","timeranges"):
        assert snap_before[kind]==snap_after[kind],("Physical data changed during timed query",kind)
    arr=trials[2:]
    result={"phase":phase,"table":table,"case":case,
        "wall_p50_ms":pct([t["duration_ms"] for t in arr],.5),
        "wall_p95_ms":pct([t["duration_ms"] for t in arr],.95),
        "cpu_p50_ms":pct([t["query_log"]["cpu_us"]/1000 for t in arr],.5),
        "query_memory_p50_mib":pct([t["query_log"]["memory_bytes"]/1048576 for t in arr],.5),
        "rss_peak_p50_mib":pct([t["rss_peak_kib"]/1024 for t in arr],.5),
        "read_rows_p50":pct([t["query_log"]["read_rows"] for t in arr],.5),
        "read_bytes_p50":pct([t["query_log"]["read_bytes"] for t in arr],.5),
        "trials":trials,"before":snap_before,"after":snap_after}
    save(output,result)
    return reference,result
def physical_probe(table,kind):
    tags=inner(table,"tags");samples=inner(table,"samples")
    labels={
      "stage":"tags['stage']='stage-0007'",
      "project":"tags['project']='project-00'",
      "project_stage":"tags['project']='project-01' AND tags['stage']='stage-0007'",
      "regex":"match(tags['branch'],'^(main|feature-0[0-9])$')"}
    sub=f"SELECT id FROM {tags} WHERE metric_name={sql_string(METRIC)} AND {labels[kind]}"
    sql=(f"SELECT sum(length(samples)) AS n FROM {samples} WHERE id IN ({sub}) "
          "SETTINGS max_threads=4,optimize_trivial_count_query=0")
    explain=actual_sql("EXPLAIN PLAN indexes=1 "+sql)[:80000]
    start=time.perf_counter()
    n=int(actual_sql(sql+" FORMAT TSV").strip())
    elapsed=round((time.perf_counter()-start)*1000,3)
    expected={"stage":35000,"project":166250}
    if kind in expected and n!=expected[kind]:raise RuntimeError("Physical sum/sample mismatch "+kind+" "+str(n))
    # These granules are per-table; keep raw EXPLAIN as definitive evidence.
    scans=[{"selected":int(x),"total":int(y)} for x,y in
            re.findall(r"Granules:\s*(\d+)/(\d+)",explain)]
    return {"query":sql,"explain":explain,"candidate_matches":n,
            "elapsed_ms_instrumented":elapsed,"granules_from_explain":scans}
def main():
    p=argparse.ArgumentParser()
    for key in ("manifest","fixture","binary","restart-helper","variant","output-dir"):
        p.add_argument("--"+key,required=True)
    a=p.parse_args()
    if a.variant not in ("stage","project","project_stage"):raise SystemExit("invalid prefix variant")
    meta=json.loads(pathlib.Path(a.manifest).read_text())
    assert meta["sha256"]==CANONICAL and meta["series"]==1330000 and meta["days"]==14
    out=pathlib.Path(a.output_dir);out.mkdir(parents=True,exist_ok=True)
    table="churn_"+a.variant
    report={"schema":1,"variant":a.variant,"canonical_sha256":meta["sha256"],
            "binary_sha256":hashlib.sha256(pathlib.Path(a.binary).read_bytes()).hexdigest(),
            "source_type":"synthetic CI stage 14d, 1.33m unique series",
            "cases":{},"physical":{},"errors":[],"checks":{"fixture":True}}
    save(out/"report.json",report)
    # Base table is created by the pinned 95k fixture generator before this script.
    try:
        ddl=definition(table,SCHEMA[a.variant])
        actual_sql(ddl)
        report["candidate_ddl"]=ddl
        start=time.perf_counter()
        imp=streamed_insert(table,a.fixture)
        report["ingest"]={"rows":imp["rows"],"batches":imp["batches"],
                          "insert_wall_s":round(time.perf_counter()-start,3)}
        assert imp["rows"]==1330000
        for name in ("churn_ts",table):
            counts=actual_sql(f"SELECT count() FROM {name} FORMAT TSV").strip()
            assert int(counts)==1330000,(name,counts)
            report.setdefault("optimize",{})[name]=optimize_table(name)
        wait_no_merges()
        report["parts"]={name:stats(name) for name in ("churn_ts",table)}
        save(out/"report.json",report)
        # Same data; baseline initial and final control; randomize selector order.
        rng=random.Random(92199)
        cases=list(QUERY_SELECTORS);rng.shuffle(cases)
        for case in cases:
            print("LAYOUT_FULL_START",json.dumps({"variant":a.variant,"case":case}),flush=True)
            variants=["A","B","B","A"]
            ref=None;responses=[]
            for ix,part in enumerate(variants):
                selected="churn_ts" if part=="A" else table
                phase=f"{part}{ix}"
                fname=out/(f"{case}-{phase}.json")
                ref,r=run_phase(selected,case,phase,a.binary,a.restart_helper,ref,meta,fname)
                responses.append({k:v for k,v in r.items() if k not in ("trials","before","after")})
                report["cases"][case]={"phases":responses,"numerical":True}
                save(out/"report.json",report)
                print("LAYOUT_FULL_CASE",json.dumps({"variant":a.variant,"case":case,"phase":phase,
                    "wall_ms":r["wall_p50_ms"],"cpu_ms":r["cpu_p50_ms"],
                    "rows":r["read_rows_p50"],"rss_mib":r["rss_peak_p50_mib"]},
                    separators=(",",":")),flush=True)
            aa=[x["wall_p50_ms"] for x in responses if x["phase"].startswith("A")]
            b=[x["wall_p50_ms"] for x in responses if x["phase"].startswith("B")]
            base=sum(aa)/2
            if abs(aa[0]-aa[1])/base>.30:
                report["errors"].append({"case":case,"kind":"BASELINE_DRIFT",
                    "baseline_start_end":aa})
            report["cases"][case]["compared"]={
                "baseline_p50_mean_ms":round(base,3),
                "candidate_p50_mean_ms":round(sum(b)/2,3),
                "speedup":round(base/(sum(b)/2),4),
                "baseline_drift_fraction":round(abs(aa[0]-aa[1])/base,4)}
            save(out/"report.json",report)
        for name in ("churn_ts",table):
            report["physical"][name]={}
            for kind in ("stage","project","project_stage","regex"):
                try:report["physical"][name][kind]=physical_probe(name,kind)
                except Exception as exc:report["errors"].append({"physical":kind,"table":name,"error":str(exc)[:1400]})
                save(out/"report.json",report)
    except Exception as exc:
        report["errors"].append({"fatal":str(exc)[:2500]})
        save(out/"report.json",report)
        raise
    report["passed"]=not report["errors"] and len(report["cases"])==5
    save(out/"report.json",report)
    print("LAYOUT_FULL_DONE",json.dumps({"variant":a.variant,"passed":report["passed"],
          "cases":{k:v.get("compared") for k,v in report["cases"].items()},
          "errors":report["errors"]},separators=(",",":")),flush=True)
    if not report["passed"]:raise SystemExit(1)
if __name__=="__main__":main()
