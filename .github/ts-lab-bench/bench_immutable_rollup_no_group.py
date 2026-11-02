#!/usr/bin/env python3
"""Snapshot-only direct-SELECT versus GROUP BY grouped AVG and VM exact check."""
import argparse,json,os,pathlib,random
from bench_bucket_cxx_abca import assert_same,normalize_any,pct,post
from bench_ch_vs_vm_dense_sparse import vm_get,vm_normalized,wait_vm
from bench_sparse_event_vs_vm import run_case,parse_events
from bench_sparse_rollup_vs_vm import rollup_sql

CH="http://127.0.0.1:18191"

def main():
    p=argparse.ArgumentParser()
    p.add_argument("--manifest",required=True)
    p.add_argument("--rollup-manifest",required=True)
    p.add_argument("--output",required=True)
    a=p.parse_args()
    m=json.loads(pathlib.Path(a.manifest).read_text())
    r=json.loads(pathlib.Path(a.rollup_manifest).read_text())
    assert r["stage_days"]==1520 and r["one_sample_contract"]
    # This is an immutable demo snapshot, not the eventual materialized-view update model.
    _,raw=post(CH,"SELECT count(),uniqExact(tuple(metric_name,stage,epoch)),"
                    "countDistinct(epoch) FROM rollup_stage FORMAT TSV")
    total,unique,days=(int(x) for x in raw.decode().split("\t"))
    assert total==unique==1520 and days==40,(total,unique,days)
    print("IMMUTABLE_ROW_UNIQUENESS_PROVED",
          json.dumps({"rows":total,"unique_keys":unique,"days":days}),flush=True)
    wait_vm(m)
    run=os.environ.get("GITHUB_RUN_ID","local")
    rng=random.Random(4142026)
    outcomes={}
    for scope in ("all","stage"):
        for steps in (3,14,40):
            name,expr,start,end,bases=run_case(m,scope,steps)
            direct=(f"SELECT stage,epoch,sum_v/n AS value FROM rollup_stage "
                    f"WHERE metric_name='tslab_sparse_40d' AND epoch>={start} AND epoch<={end}")
            if scope=="stage": direct+=" AND stage='stage-0007'"
            direct+=" ORDER BY stage,epoch FORMAT JSONEachRow"
            q={
                "vm":None,
                "ts_v4":bases["ts_v4"],
                "grouped":rollup_sql("rollup_stage",start,end,scope),
                "snapshot":direct,
            }
            _,refraw=post(CH,bases["ts_off"])
            ref=normalize_any(refraw)
            stat={k:[] for k in q}
            for i in range(8):
                order=list(q);rng.shuffle(order)
                for key in order:
                    if key=="vm":
                        ms,payload=vm_get({"query":expr,"start":start,"end":end,"step":86400})
                        observed=vm_normalized(payload)
                    else:
                        qid=f"single_{run}_{name}_{key}_{i}"
                        ms,raw=post(CH,q[key],qid)
                        observed=normalize_any(raw) if key=="ts_v4" else parse_events(raw.decode())
                    assert_same(ref,observed,name,key)
                    if i>=2:stat[key].append(ms)
            result={"case":name,"points":len(ref),"stats":{
                key:{"p50_ms":pct(v,.5),"p95_ms":pct(v,.95),"trials_ms":v}
                for key,v in stat.items()}}
            outcomes[name]=result
            print("IMMUTABLE_SELECT_CASE",json.dumps({"case":name,"points":len(ref),
                **{k+"_ms":v["p50_ms"] for k,v in result["stats"].items()}},
                separators=(",",":")),flush=True)
    post(CH,"SYSTEM FLUSH LOGS")
    _,raw=post(CH,"SELECT query_id,query_duration_ms,read_rows,read_bytes,memory_usage "
                  "FROM system.query_log WHERE type='QueryFinish' "
                  f"AND startsWith(query_id,'single_{run}_') FORMAT JSONEachRow")
    logs={o["query_id"]:o for line in raw.splitlines() if line.strip()
          for o in [json.loads(line)]}
    for name,result in outcomes.items():
        for key,data in result["stats"].items():
            if key=="vm":continue
            ids=[f"single_{run}_{name}_{key}_{i}" for i in range(2,8)]
            assert all(x in logs for x in ids)
            entries=[logs[x] for x in ids]
            data["read_rows_p50"]=pct([x["read_rows"] for x in entries],.5)
            data["memory_p95_bytes"]=pct([x["memory_usage"] for x in entries],.95)
            data["server_p50_ms"]=pct([x["query_duration_ms"] for x in entries],.5)
    summary={"unique_snapshot":True,"all_exact":True,"cases":len(outcomes),
            "snapshot_faster_than_vm":sum(
               x["stats"]["snapshot"]["p50_ms"]<x["stats"]["vm"]["p50_ms"]
               for x in outcomes.values())}
    pathlib.Path(a.output).write_text(json.dumps({
        "summary":summary,"cases":outcomes},indent=2)+"\n")
    print("IMMUTABLE_SELECT_COMPLETE",json.dumps(summary,separators=(",",":")),flush=True)
if __name__=="__main__":main()
