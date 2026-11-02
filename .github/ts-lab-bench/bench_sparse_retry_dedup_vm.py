#!/usr/bin/env python3
"""Retry-safe ReplacingMergeTree FINAL vs VM and event without duplicates."""
import argparse,json,os,pathlib,random
from bench_bucket_cxx_abca import assert_same,normalize_any,pct,post
from bench_ch_vs_vm_dense_sparse import vm_get,vm_normalized,wait_vm
from bench_sparse_event_vs_vm import run_case,parse_events,event_sql

HOST="http://127.0.0.1:18191"

def final_sql(start,end,scope):
    suffix={
      "all":"",
      "stage":" AND stage='stage-0007'",
      "project":" AND project='project-00'",
      "regex":" AND match(toString(branch),'^(main|feature-0[0-9])$')",
    }[scope]
    start_ms=(start-86400)*1000
    return ("SELECT stage,toUInt64(intDiv(toUnixTimestamp64Milli(ts),86400000)*86400+86400) "
            "AS epoch,avg(value) AS value FROM events_dedup FINAL "
            "WHERE metric_name='tslab_sparse_40d' "
            f"AND ts>fromUnixTimestamp64Milli({start_ms}) "
            f"AND ts<=fromUnixTimestamp64Milli({end*1000}){suffix} "
            "GROUP BY stage,epoch ORDER BY stage,epoch FORMAT JSONEachRow")

def main():
    p=argparse.ArgumentParser()
    p.add_argument("--manifest",required=True)
    p.add_argument("--dedup-report",required=True)
    p.add_argument("--output",required=True)
    a=p.parse_args()
    m=json.loads(pathlib.Path(a.manifest).read_text())
    d=json.loads(pathlib.Path(a.dedup_report).read_text())
    assert d["physical_rows"]==42000 and d["logical_rows_final"]==40000
    assert d["negative_control_changed_groups"]>0
    wait_vm(m)
    run=os.environ.get("GITHUB_RUN_ID","local")
    rng=random.Random(202611021)
    result={}
    for scope in ("all","stage","project","regex"):
        for steps in (3,14,40):
            name,expr,start,end,queries=run_case(m,scope,steps)
            sqls={"vm":None,"ts_v4":queries["ts_v4"],
                  "event":queries["events_stage"] if scope!="project" else queries["events_project"],
                  "dedup_final":final_sql(start,end,scope)}
            _,raw=post(HOST,queries["ts_off"])
            reference=normalize_any(raw)
            times={k:[] for k in sqls}
            for trial in range(8):
                order=list(sqls);rng.shuffle(order)
                for variant in order:
                    if variant=="vm":
                        ms,vm=vm_get({"query":expr,"start":start,
                                     "end":end,"step":86400})
                        observed=vm_normalized(vm)
                    else:
                        qid=f"dedup_{run}_{name}_{variant}_{trial}"
                        ms,raw=post(HOST,sqls[variant],qid)
                        observed=(normalize_any(raw) if variant=="ts_v4"
                                  else parse_events(raw.decode("utf-8","replace")))
                    assert_same(reference,observed,name,variant)
                    if trial>=2:times[variant].append(ms)
            stats={k:{"p50_ms":pct(v,.5),"p95_ms":pct(v,.95),
                      "trials_ms":v} for k,v in times.items()}
            result[name]={"points":len(reference),"stats":stats}
            print("DEDUP_VS_VM_CASE",json.dumps({"case":name,"points":len(reference),
                **{k+"_ms":v["p50_ms"] for k,v in stats.items()}},
                separators=(",",":")),flush=True)
    post(HOST,"SYSTEM FLUSH LOGS")
    _,raw=post(HOST,"SELECT query_id,query_duration_ms,read_rows,read_bytes,memory_usage "
                    "FROM system.query_log WHERE type='QueryFinish' "
                    f"AND startsWith(query_id,'dedup_{run}_') FORMAT JSONEachRow")
    logs={x["query_id"]:x for line in raw.splitlines() if line.strip()
          for x in [json.loads(line)]}
    for name,rec in result.items():
        for variant,stat in rec["stats"].items():
            if variant=="vm":continue
            ids=[f"dedup_{run}_{name}_{variant}_{i}" for i in range(2,8)]
            assert all(x in logs for x in ids),(name,variant)
            recs=[logs[x] for x in ids]
            stat.update({"memory_p95_bytes":pct([x["memory_usage"] for x in recs],.95),
                         "read_rows_p50":pct([x["read_rows"] for x in recs],.5)})
    summary={"all_exact":True,"cases":len(result),
             "final_faster_than_vm":sum(
               v["stats"]["dedup_final"]["p50_ms"]<v["stats"]["vm"]["p50_ms"]
               for v in result.values()),"replay_rows":2000}
    pathlib.Path(a.output).write_text(json.dumps({
        "summary":summary,"dedup":d,"cases":result},indent=2)+"\n")
    print("DEDUP_VS_VM_COMPLETE",json.dumps(summary,separators=(",",":")),flush=True)
if __name__=="__main__":main()
