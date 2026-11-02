#!/usr/bin/env python3
"""Last-1d/3d/7d/14d retention-window CH native TimeRanges A/B/B/A.

Fixed short ID, matched 1.33m immutable synthetic series and O2 binary.
A: default TimeRanges, B: no TimeRanges. Same current 14d physical fixture.
"""
import argparse,hashlib,json,os,pathlib,random,statistics,sys,time,shutil
sys.path.insert(0,".github/ts-lab-layout")
sys.path.insert(0,".github/ts-lab-perf")
sys.path.insert(0,".github/ts-lab-bench")
from bench_layout_full import CANONICAL,actual_sql,_sha_file
from bench_granularity_v11 import (streamed_insert,optimize_table,wait_no_merges,stats,querylogs)
from bench_stage_cpp_vs_original_vm_95k import (METRIC,DAY,monitored,server_restart,
    assert_same,sql_string,save,pct)

QUERY={
  "stage":('avg by(stage)(avg_over_time('+METRIC+'{stage="stage-0007"}[1d]))',1),
  "whole":('avg by(stage)(avg_over_time('+METRIC+'[1d]))',38),
  "project":('avg by(stage)(avg_over_time('+METRIC+'{project="project-00"}[1d]))',19),
}
WINDOWS=(1,3,7,14)

def actual_query(table,selector,start,end):
    expression=QUERY[selector][0]
    return (f"SELECT * FROM prometheusQueryRange({table},{sql_string(expression)},"
            f"{start},{end},{DAY}) SETTINGS max_threads=4,"
            "promql_enable_native_one_point_lab=1 FORMAT JSONEachRow")
def logs_for(prefix,expected_ids):
    rows=[]
    for attempt in range(6):
        rows=querylogs(prefix)
        by={x["query_id"]:x for x in rows if x.get("type")=="QueryFinish"}
        if all(i in by for i in expected_ids):return by
        time.sleep(.2*(attempt+1))
    raise RuntimeError("Missing QueryFinish for "+prefix+" got="+str(len(by))+" expected="+str(len(expected_ids)))
def run_phase(table,selector,days,phase,binary,restart,ref,meta,out):
    rid=os.environ.get("GITHUB_RUN_ID","local")
    name=f"twindow_{rid}_{selector}_{days}d_{phase}"
    pid=server_restart(restart,binary,name)
    wait_no_merges()
    end=int(meta["end"]);start=end-(days-1)*DAY
    q=actual_query(table,selector,start,end)
    records=[]
    disk=shutil.disk_usage(os.environ["RUNNER_TEMP"])
    if disk.free/disk.total<.20:raise RuntimeError("Environment disk below 20% capacity")
    for n in range(6):
        qid=name+f"_{n}"
        obs=monitored(q,qid,pid)
        if not obs["ok"]:raise RuntimeError(f"Native query failure {qid}: "+str(obs.get("error")))
        pts=obs.pop("points")
        expected=days*QUERY[selector][1]
        if len(pts)!=expected:raise RuntimeError(f"Unexpected result count {len(pts)} != {expected} for {name}")
        if ref is None:ref=pts
        else:assert_same(ref,pts,selector,name)
        records.append({"qid":qid,"warmup":n<2,"wall_ms":obs["duration_ms"],
             "rss_kib":obs["rss_peak_kib"],"points":len(pts)})
    by=logs_for(name,[x["qid"] for x in records])
    for x in records:
        ev=by[x["qid"]].get("ProfileEvents") or {}
        x.update({"cpu_ms":(ev.get("OSCPUVirtualTimeMicroseconds") or 0)/1000,
                  "read_rows":by[x["qid"]].get("read_rows"),
                  "read_bytes":by[x["qid"]].get("read_bytes"),
                  "query_memory":by[x["qid"]].get("memory_usage"),
                  "compressed_read":ev.get("ReadCompressedBytes")})
    measured=records[2:]
    phase_rec={"table":table,"phase":phase,"window_days":days,
      "mean_wall_ms":round(statistics.mean(x["wall_ms"] for x in measured),3),
      "median_wall_ms":pct([x["wall_ms"] for x in measured],.5),
      "cpu_ms_p50":pct([x["cpu_ms"] for x in measured],.5),
      "read_rows_p50":pct([x["read_rows"] for x in measured],.5),
      "read_bytes_p50":pct([x["read_bytes"] for x in measured],.5),
      "max_rss_p50_mib":pct([x["rss_kib"]/1024 for x in measured],.5),
      "trials":records,"physical":{x:stats(table)[x] for x in ("samples","tags","timeranges")} if table=="churn_ts" else {
        x:stats(table)[x] for x in ("samples","tags")}}
    save(out,phase_rec)
    return ref,phase_rec

def main():
    p=argparse.ArgumentParser()
    for k in ("manifest","fixture","binary","restart-helper","selector","output-dir"):
        p.add_argument("--"+k,required=True)
    a=p.parse_args()
    if a.selector not in QUERY:raise SystemExit("Invalid selector")
    out=pathlib.Path(a.output_dir);out.mkdir(parents=True,exist_ok=True)
    meta=json.loads(pathlib.Path(a.manifest).read_text())
    if meta["sha256"]!=CANONICAL or meta["series"]!=1330000 or meta["days"]!=14:
        raise RuntimeError("Immutable fixture mismatch")
    table="churn_timeranges_disabled"
    r={"schema":1,"selector":a.selector,"source_sha256":CANONICAL,
       "binary_sha256":_sha_file(a.binary),
       "time_filters":"avg_over_time one day, windows queried 1/3/7/14 daily timestamps",
       "variant":"same original 2-element id, no TimeRanges target",
       "physical":{},"windows":{},"errors":[]}
    save(out/"window-results.json",r)
    try:
        actual_sql("CREATE TABLE "+table+" ENGINE=TimeSeries SETTINGS recent_samples_ttl_seconds=0,store_time_ranges=0")
        start=time.monotonic()
        imp=streamed_insert(table,a.fixture)
        if imp["rows"]!=1330000:raise RuntimeError("Incomplete candidate ingest")
        r["ingest_wall_s"]=round(time.monotonic()-start,3)
        for tbl in ("churn_ts",table):
            r["physical"][tbl]={"optimize":optimize_table(tbl),"parts":stats(tbl)}
            actual_sql(f"SELECT count() FROM {tbl} FORMAT TSV")
        wait_no_merges()
        save(out/"window-results.json",r)
        # Deterministic alternation to suppress skew from cache/cold starts.
        windows=[14,1,7,3]
        for days in windows:
            phases=[];ref=None
            for ix,side in enumerate(("A","B","B","A")):
                table_name="churn_ts" if side=="A" else table
                phase=side+str(ix)
                ref,measurement=run_phase(table_name,a.selector,days,phase,a.binary,
                    a.restart_helper,ref,meta,out/(f"window-{days}-{phase}.json"))
                phases.append(measurement)
                r["windows"][str(days)]={"phases":[
                    {k:v for k,v in x.items() if k not in ("trials","physical")} for x in phases],
                    "values_equal":True}
                save(out/"window-results.json",r)
                print("TIMEWINDOW_PHASE",json.dumps({"selector":a.selector,"days":days,
                    "phase":phase,"wall_ms":measurement["median_wall_ms"],
                    "cpu_ms":measurement["cpu_ms_p50"],"read_rows":measurement["read_rows_p50"]},
                    separators=(",",":")),flush=True)
            base=[x for x in phases if x["phase"].startswith("A")]
            cand=[x for x in phases if x["phase"].startswith("B")]
            control=statistics.mean(x["median_wall_ms"] for x in base)
            alt=statistics.mean(x["median_wall_ms"] for x in cand)
            drift=abs(base[0]["median_wall_ms"]-base[1]["median_wall_ms"])/control
            if drift>.30:r["errors"].append({"days":days,"control_drift":round(drift,4)})
            r["windows"][str(days)]["summary"]={
                "original_ms":round(control,3),"without_ranges_ms":round(alt,3),
                "speedup":round(control/alt,4),"baseline_drift":round(drift,4),
                "original_cpu_ms":round(statistics.mean(x["cpu_ms_p50"] for x in base),3),
                "without_ranges_cpu_ms":round(statistics.mean(x["cpu_ms_p50"] for x in cand),3),
                "original_read_rows":round(statistics.mean(x["read_rows_p50"] for x in base)),
                "without_ranges_read_rows":round(statistics.mean(x["read_rows_p50"] for x in cand))}
            save(out/"window-results.json",r)
            print("TIMEWINDOW_CASE",json.dumps({"selector":a.selector,"days":days,
                **r["windows"][str(days)]["summary"]},separators=(",",":")),flush=True)
    except Exception as exc:
        r["errors"].append({"fatal":str(exc)[:2200]})
        save(out/"window-results.json",r)
        raise
    r["passed"]=not r["errors"] and len(r["windows"])==len(WINDOWS)
    save(out/"window-results.json",r)
    print("TIMEWINDOW_DONE",json.dumps({"selector":a.selector,"passed":r["passed"],
      "windows":{k:v.get("summary") for k,v in r["windows"].items()},
      "errors":r["errors"]},separators=(",",":")),flush=True)
    if not r["passed"]:raise SystemExit(1)
if __name__=="__main__":main()
