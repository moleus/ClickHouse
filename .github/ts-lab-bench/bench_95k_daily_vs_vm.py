#!/usr/bin/env python3
"""1.33m series exact-data VM 1.134 vs CH v4 O1 with query-log memory."""
import argparse,json,os,pathlib,random,time,urllib.parse,urllib.request,math
from bench_bucket_cxx_abca import assert_same,normalize_any,pct,post
from bench_ch_vs_vm_dense_sparse import vm_get,vm_normalized,rss_kib
from bench_filtered_selector_v4_sql import sql_string

CH="http://127.0.0.1:18191"
METRIC="jenkins_ci_95k_daily"

def ready(meta):
    last=int(meta["end"])
    url=("http://127.0.0.1:8430/api/v1/query?"+
         urllib.parse.urlencode({"query":f"sum(count_over_time({METRIC}[1d]))",
                                  "time":last,"nocache":1}))
    for attempt in range(100):
        try:
            with urllib.request.urlopen(url,timeout=180) as resp:
                data=json.loads(resp.read())
            vals=data["data"]["result"]
            value=float(vals[0]["value"][1]) if vals else 0
            if value==95000:
                print("JENKINS_95K_VM_READY",json.dumps({"attempts":attempt+1,
                     "last_day":value}),flush=True)
                return
            print("JENKINS_95K_VM_VISIBILITY",json.dumps({"attempt":attempt+1,
                  "last_day":value}),flush=True)
        except Exception as exc:
            print("JENKINS_95K_VM_VISIBILITY_ERROR",repr(exc)[:500],flush=True)
        time.sleep(2)
    raise AssertionError(("VM samples still not visible",METRIC))

def main():
    p=argparse.ArgumentParser()
    p.add_argument("--manifest",required=True)
    p.add_argument("--output",required=True)
    p.add_argument("--vm-pid",required=True)
    p.add_argument("--ch-pid",required=True)
    a=p.parse_args()
    meta=json.loads(pathlib.Path(a.manifest).read_text())
    assert meta["days"]==14 and meta["per_day"]==95000 and meta["series"]==1330000
    ready(meta)
    start=int(meta["base"])+86400
    end=int(meta["end"])
    run=os.getenv("GITHUB_RUN_ID","local")
    rng=random.Random(198606)
    vm_pid=int(pathlib.Path(a.vm_pid).read_text())
    ch_pid=int(pathlib.Path(a.ch_pid).read_text())
    selectors={
       "whole":METRIC,
       "stage":METRIC+'{stage="stage-0007"}',
       "project":METRIC+'{project="project-00"}',
       "branch_regex":METRIC+'{branch=~"main|feature-0[0-9]"}'}
    report={"all_exact":True,"cases":{},"rss_before":{
        "vm_kib":rss_kib(vm_pid),"ch_tree_kib":rss_kib(ch_pid)}}
    for name,selector in selectors.items():
        expr=f"avg by(stage)(avg_over_time({selector}[1d]))"
        base=f"SELECT * FROM prometheusQueryRange(churn_ts,{sql_string(expr)},{start},{end},86400)"
        sqls={"ch_off":base+" SETTINGS promql_enable_native_one_point_lab=0 FORMAT JSONEachRow",
              "ch_v4":base+" SETTINGS promql_enable_native_one_point_lab=1 FORMAT JSONEachRow"}
        observed=None
        stats={"vm":[],"ch_off":[],"ch_v4":[]}
        for trial in range(6):
            order=list(stats);rng.shuffle(order)
            for key in order:
                if key=="vm":
                    ms,payload=vm_get({"query":expr,"start":start,
                                       "end":end,"step":86400})
                    result=vm_normalized(payload)
                else:
                    qid=f"scale95_{run}_{name}_{key}_{trial}"
                    ms,raw=post(CH,sqls[key],qid)
                    result=normalize_any(raw)
                if observed is None:observed=result
                else:assert_same(observed,result,name,key)
                if trial>=2:stats[key].append(ms)
        entry={"points":len(observed),"results":{
            key:{"p50_ms":pct(times,.5),"p95_ms":pct(times,.95),"trials_ms":times}
            for key,times in stats.items()}}
        report["cases"][name]=entry
        print("JENKINS_95K_CASE",json.dumps({"case":name,"points":len(observed),
             **{k+"_ms":v["p50_ms"] for k,v in entry["results"].items()}},
            separators=(",",":")),flush=True)
    post(CH,"SYSTEM FLUSH LOGS")
    _,raw=post(CH,"SELECT query_id,query_duration_ms,read_rows,read_bytes,"
        "memory_usage,ProfileEvents FROM system.query_log WHERE type='QueryFinish' "
        f"AND startsWith(query_id,'scale95_{run}_') FORMAT JSONEachRow")
    logs={x["query_id"]:x for l in raw.splitlines() if l.strip()
          for x in [json.loads(l)]}
    for case,entry in report["cases"].items():
        for k in ("ch_off","ch_v4"):
            ids=[f"scale95_{run}_{case}_{k}_{trial}" for trial in range(2,6)]
            assert all(x in logs for x in ids),("missing QueryFinish",case,k)
            arr=[logs[x] for x in ids]
            entry["results"][k].update({
                "query_memory_p95_bytes":pct([v["memory_usage"] for v in arr],.95),
                "read_rows_p50":pct([v["read_rows"] for v in arr],.5),
                "cpu_us_p50":pct([v["ProfileEvents"].get("OSCPUVirtualTimeMicroseconds",0)
                                  for v in arr],.5)})
    report["rss_after"]={"vm_kib":rss_kib(vm_pid),"ch_tree_kib":rss_kib(ch_pid)}
    pathlib.Path(a.output).write_text(json.dumps(report,indent=2)+"\n")
    print("JENKINS_95K_COMPLETE",json.dumps({
         "all_exact":True,"cases":len(report["cases"]),
         "rss_before":report["rss_before"],"rss_after":report["rss_after"]},
         separators=(",",":")),flush=True)
if __name__=="__main__":main()
