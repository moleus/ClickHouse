#!/usr/bin/env python3
"""Falsify merge join / tags collector / parallel aggregate memory hypotheses at 1.33m IDs.

Every query error is evidence, not a success. The input is unchanged on all cases.
"""
import argparse,json,os,pathlib,time,urllib.error,urllib.request,urllib.parse
from bench_bucket_cxx_abca import post
from bench_ch_vs_vm_dense_sparse import vm_get,vm_normalized,pct,rss_kib
from bench_filtered_selector_v4_sql import sql_string

CH="http://127.0.0.1:18191"
METRIC="jenkins_ci_95k_daily"
DAY=86400

def query(sql,qid=None):
    begin=time.perf_counter()
    try:
        ms,raw=post(CH,sql,qid)
        return {"ok":True,"ms":ms,"body":raw.decode("utf-8","replace")[:1000]}
    except Exception as err:
        return {"ok":False,"ms":1000*(time.perf_counter()-begin),
                "error":str(err)[:1500],"oom":"MEMORY_LIMIT_EXCEEDED" in str(err)}

def main():
    p=argparse.ArgumentParser()
    p.add_argument("--manifest",required=True)
    p.add_argument("--output",required=True)
    p.add_argument("--vm-pid",required=True)
    p.add_argument("--ch-pid",required=True)
    a=p.parse_args()
    m=json.loads(pathlib.Path(a.manifest).read_text())
    assert m["series"]==1330000 and m["days"]==14 and m["per_day"]==95000
    run=os.environ.get("GITHUB_RUN_ID","local")
    start=m["base"]+DAY;end=m["end"]
    ch_pid=int(pathlib.Path(a.ch_pid).read_text())
    vm_pid=int(pathlib.Path(a.vm_pid).read_text())
    rss=lambda:{"vm_kib":rss_kib(vm_pid),"ch_tree_kib":rss_kib(ch_pid)}
    expr=f"avg by(stage)(avg_over_time({METRIC}[1d]))"
    results={"rss_initial":rss(),"vm":{},"cases":{},"source":m}
    # Establish performance and equivalence of VM as independent control before CH OOM.
    for label,selector in {
        "whole":METRIC,
        "stage":METRIC+'{stage="stage-0007"}',
        "project":METRIC+'{project="project-00"}'
    }.items():
        expression=f"avg by(stage)(avg_over_time({selector}[1d]))"
        reps=[]
        for i in range(4):
            try:
                ms,payload=vm_get({"query":expression,"start":start,"end":end,
                                   "step":DAY})
                output=vm_normalized(payload)
                reps.append({"ms":ms,"points":len(output)})
            except Exception as exc:
                reps.append({"error":str(exc)[:2000]})
        results["vm"][label]={"trials":reps,
            "p50_ms":pct([x["ms"] for x in reps if "ms" in x],.5)
             if all("ms" in x for x in reps) else None}
        print("HIGHCARD_VM_CONTROL",json.dumps({"label":label,
             "result":results["vm"][label]},separators=(",",":")),flush=True)
    # Use all O1 binary, no compiler changes. Discover physical targets by own TimeSeries UUID.
    out_uuid=query("SELECT toString(uuid) FROM system.tables "
                   "WHERE database='default' AND name='churn_ts' FORMAT TSV")
    assert out_uuid["ok"],out_uuid
    uid=out_uuid["body"].strip()
    assert len(uid)==36,uid
    tick=chr(96)
    def target(kind):
        return "default."+tick+f".inner_id.{kind}.{uid}"+tick
    tags=target("tags")
    samples=target("samples")
    ranges=target("timeranges")
    start_ms=m["base"]*1000+1;end_ms=end*1000
    selector=(f"timeSeriesSelector('default','churn_ts','{METRIC}',"
              f"fromUnixTimestamp64Milli({start_ms}),"
              f"fromUnixTimestamp64Milli({end_ms}),'samples')")
    def add(name,sql,threads=4):
        sql=sql.replace(" FORMAT ",f" SETTINGS max_threads={threads} FORMAT ")
        qid=f"mem95_{run}_{name}_t{threads}"
        entry=query(sql,qid)
        entry.update({"query_id":qid,"threads":threads,"rss_after":rss()})
        results["cases"][name+"_t"+str(threads)]=entry
        print("HIGHCARD_MEMORY_PROBE",json.dumps({
          "case":name,"threads":threads,"ok":entry["ok"],"ms":round(entry["ms"],2),
          "oom":entry.get("oom",False),"error":entry.get("error","")[:450],
          "rss_ch_kib":entry["rss_after"]["ch_tree_kib"]},
          separators=(",",":")),flush=True)
    for name,stmt in [
        ("outer_rows",f"SELECT count() FROM churn_ts FORMAT TSV"),
        ("tags_rows",f"SELECT count() FROM {tags} FORMAT TSV"),
        ("ranges_rows",f"SELECT count() FROM {ranges} FORMAT TSV"),
        ("physical_samples",f"SELECT sum(length(samples)) FROM {samples} FORMAT TSV"),
        ("selector_count",f"SELECT count() FROM {selector} FORMAT TSV"),
        ("selector_lengths",f"SELECT sum(length(samples)) FROM {selector} FORMAT TSV")
    ]:
        add(name,stmt)
    for threads in (1,4):
        for days in (3,14):
            st=end-(days-1)*DAY
            e=f"avg by(stage)(avg_over_time({METRIC}[1d]))"
            base=(f"SELECT count() FROM prometheusQueryRange(churn_ts,"
                  f"{sql_string(e)},{st},{end},86400)")
            for mode in ("off","on"):
                sql=base+f" SETTINGS max_threads={threads},promql_enable_native_one_point_lab={1 if mode=='on' else 0} FORMAT TSV"
                qid=f"mem95_{run}_promql_{days}_{mode}_t{threads}"
                entry=query(sql,qid)
                entry.update({"query_id":qid,"threads":threads,"rss_after":rss()})
                key=f"promql_{days}_{mode}_t{threads}"
                results["cases"][key]=entry
                print("HIGHCARD_MEMORY_PROBE",json.dumps({
                  "case":key,"ok":entry["ok"],"ms":round(entry["ms"],2),
                  "oom":entry.get("oom",False),"error":entry.get("error","")[:400],
                  "rss_ch_kib":entry["rss_after"]["ch_tree_kib"]},
                  separators=(",",":")),flush=True)
    # Filtered selectors: changing series cardinality is a separate causal variable.
    for name,filter_text in (("stage",'{stage="stage-0007"}'),
                             ("project",'{project="project-00"}')):
        e=f"avg by(stage)(avg_over_time({METRIC}{filter_text}[1d]))"
        st=end-13*DAY
        sql=(f"SELECT count() FROM prometheusQueryRange(churn_ts,"
             f"{sql_string(e)},{st},{end},86400) "
             "SETTINGS max_threads=4,promql_enable_native_one_point_lab=1 FORMAT TSV")
        qid=f"mem95_{run}_{name}"
        rec=query(sql,qid)
        rec.update({"query_id":qid,"rss_after":rss()})
        results["cases"][name]=rec
        print("HIGHCARD_MEMORY_PROBE",json.dumps({
             "case":name,"ok":rec["ok"],"ms":round(rec["ms"],2),
             "oom":rec.get("oom",False),"error":rec.get("error","")[:450]},
             separators=(",",":")),flush=True)
    query("SYSTEM FLUSH LOGS")
    try:
        _,full_log=post(CH,"SELECT query_id,type,exception_code,"
           "substring(exception,1,500) AS err,query_duration_ms,"
           "read_rows,read_bytes,memory_usage,ProfileEvents "
           "FROM system.query_log WHERE startsWith(query_id,"
           f"{sql_string('mem95_'+run+'_')}) FORMAT JSONEachRow")
        results["query_log"]=[json.loads(l) for l in full_log.splitlines()
            if l.strip()]
    except Exception as exc:
        results["query_log_error"]=str(exc)[:3000]
    try:
        result=query("EXPLAIN PIPELINE SELECT count() FROM "
                     "prometheusQueryRange(churn_ts,"
                     f"{sql_string(expr)},{start},{end},86400) "
                     "SETTINGS promql_enable_native_one_point_lab=1")
        results["explain_pipeline"]=result
    except Exception as exc:results["explain_error"]=str(exc)
    results["rss_final"]=rss()
    results["summary"]={"vm_control_success":sum(
        v["p50_ms"] is not None for v in results["vm"].values()),
        "probe_count":len(results["cases"]),
        "oom_cases":[name for name,v in results["cases"].items()
                     if v.get("oom",False)]}
    pathlib.Path(a.output).write_text(json.dumps(results,indent=2)+"\n")
    print("HIGHCARD_MEMORY_DIAG_COMPLETE",json.dumps(
        results["summary"],separators=(",",":")),flush=True)
if __name__=="__main__":main()
