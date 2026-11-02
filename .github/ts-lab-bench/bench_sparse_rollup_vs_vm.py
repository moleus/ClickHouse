#!/usr/bin/env python3
"""Paired stage AVG sufficient-statistics tests vs event SQL, TimeSeries v4 and VM."""
import argparse,json,math,os,pathlib,random
from bench_bucket_cxx_abca import assert_same,normalize_any,pct,post
from bench_ch_vs_vm_dense_sparse import vm_get,vm_normalized,wait_vm
from bench_sparse_event_vs_vm import run_case,parse_events,LAYOUTS

HOST="http://127.0.0.1:18191"
METRIC="tslab_sparse_40d"

def rollup_sql(table,start,end,scope):
    selector=f"metric_name='{METRIC}' AND epoch>={start} AND epoch<={end}"
    if scope=="stage":selector+=" AND stage='stage-0007'"
    if scope=="project":selector+=" AND project='project-00'"
    if scope=="regex":selector+=" AND match(toString(branch),'^(main|feature-0[0-9])$')"
    if table=="rollup_stage" and scope not in ("all","stage"):
        raise ValueError(("stage summary cannot implement project/branch selection",scope))
    return (f"SELECT stage,epoch,sum(sum_v)/sum(n) AS value FROM {table} "
            f"WHERE {selector} GROUP BY stage,epoch "
            "ORDER BY stage,epoch FORMAT JSONEachRow")

def main():
    ap=argparse.ArgumentParser()
    ap.add_argument("--manifest",required=True)
    ap.add_argument("--rollup-manifest",required=True)
    ap.add_argument("--output",required=True)
    a=ap.parse_args()
    meta=json.loads(pathlib.Path(a.manifest).read_text())
    rollup=json.loads(pathlib.Path(a.rollup_manifest).read_text())
    assert rollup["one_sample_contract"] and rollup["stage_days"]==1520
    wait_vm(meta)
    run=os.getenv("GITHUB_RUN_ID","local")
    rng=random.Random(6661979)
    reports={}
    for scope in ("all","stage","project","regex"):
        for steps in (3,14,40):
            name,expr,start,end,bases=run_case(meta,scope,steps)
            queries={
              "vm":None,
              "ts_v4":bases["ts_v4"],
              "event":bases["events_stage"] if scope!="project" else bases["events_project"],
              "rollup_facets":rollup_sql("rollup_facets",start,end,scope)
            }
            if scope in ("all","stage"):
                queries["rollup_stage"]=rollup_sql("rollup_stage",start,end,scope)
            timings={k:[] for k in queries}
            _,original=post(HOST,bases["ts_off"])
            reference=normalize_any(original)
            for rep in range(8):
                order=list(queries);rng.shuffle(order)
                for variant in order:
                    if variant=="vm":
                        latency,payload=vm_get({"query":expr,"start":start,
                                                "end":end,"step":86400})
                        rows=vm_normalized(payload)
                    else:
                        qid=f"roll_{run}_{name}_{variant}_{rep}"
                        latency,body=post(HOST,queries[variant],qid)
                        rows=(normalize_any(body) if variant=="ts_v4"
                              else parse_events(body.decode("utf-8","replace")))
                    assert_same(reference,rows,name,variant)
                    if rep>=2:timings[variant].append((rep,round(latency,3)))
            stats={k:{"p50_ms":pct([t[1] for t in trials],.5),
                       "p95_ms":pct([t[1] for t in trials],.95),
                       "timings_ms":[t[1] for t in trials]}
                   for k,trials in timings.items()}
            reports[name]={"points":len(reference),"stats":stats}
            print("ROLLUP_VS_VM_CASE",json.dumps({"case":name,"points":len(reference),
                **{x+"_ms":v["p50_ms"] for x,v in stats.items()}},
                separators=(",",":")),flush=True)
    post(HOST,"SYSTEM FLUSH LOGS")
    _,raw=post(HOST,
        "SELECT query_id,query_duration_ms,read_rows,read_bytes,memory_usage,ProfileEvents "
        "FROM system.query_log WHERE type='QueryFinish' "
        f"AND startsWith(query_id,'roll_{run}_') FORMAT JSONEachRow")
    logs={x["query_id"]:x for l in raw.splitlines() if l.strip()
          for x in [json.loads(l)]}
    for name,rec in reports.items():
        for variant,stats in rec["stats"].items():
            if variant=="vm":continue
            qids=[f"roll_{run}_{name}_{variant}_{rep}" for rep in range(2,8)]
            assert all(x in logs for x in qids),(name,variant)
            entries=[logs[x] for x in qids]
            stats.update({
                "query_server_p50_ms":pct([x["query_duration_ms"] for x in entries],.5),
                "read_rows_p50":pct([x["read_rows"] for x in entries],.5),
                "read_bytes_p50":pct([x["read_bytes"] for x in entries],.5),
                "memory_p95_bytes":pct([x["memory_usage"] for x in entries],.95),
                "cpu_us_p50":pct([x["ProfileEvents"].get("OSCPUVirtualTimeMicroseconds",0)
                                  for x in entries],.5)})
    score=sum(min(
           rec["stats"]["rollup_facets"]["p50_ms"],
           rec["stats"].get("rollup_stage",{"p50_ms":float("inf")})["p50_ms"])
           <rec["stats"]["vm"]["p50_ms"] for rec in reports.values())
    summary={"all_exact":True,"cases":len(reports),
             "best_rollup_faster_than_vm":score,
             "source_contract":"one_sample_per_ID_and_daily_window"}
    pathlib.Path(a.output).write_text(json.dumps({
        "summary":summary,"rollups":rollup,"cases":reports},indent=2)+"\n")
    pathlib.Path(a.output).with_name("rollup-vs-vm-querylog.json").write_text(
        json.dumps(logs,indent=2)+"\n")
    print("ROLLUP_VS_VM_COMPLETE",json.dumps(summary,separators=(",",":")),flush=True)
if __name__=="__main__":main()
