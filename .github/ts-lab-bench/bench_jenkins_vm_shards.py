#!/usr/bin/env python3
"""VM 165k cap: compare date chunking and disjoint project sharding exactly.

For one-sample CI events each stage/day overall avg is reconstructed as
(sum of per-project-shard values) / (sum of per-project-shard counts).
Never use average-of-averages without counts.
"""
import argparse
import json
import math
import os
import pathlib
import signal
import subprocess
import time
import urllib.error
import urllib.parse
import urllib.request

from bench_jenkins_querypath import (
    post, normalized_events, normalized_vm, compare, epoch, pct
)


def vm_call(expr,start,end,step,root="http://127.0.0.1:8430"):
    url=root+"/api/v1/query_range?"+urllib.parse.urlencode(
        {"query":expr,"start":start,"end":end,"step":step,"nocache":1})
    begin=time.perf_counter()
    try:
        with urllib.request.urlopen(url,timeout=180) as f:
            status=f.status
            raw=f.read()
    except urllib.error.HTTPError as ex:
        status=ex.code
        raw=ex.read()
    cost=round(1000*(time.perf_counter()-begin),3)
    payload=json.loads(raw)
    return status,cost,payload


def normalize(data):
    if data.get("status")!="success":
        raise AssertionError(("VM not successful",data))
    return normalized_vm(data)


def main():
    p=argparse.ArgumentParser()
    p.add_argument("--manifest",required=True)
    p.add_argument("--output",required=True)
    p.add_argument("--vm-binary",required=True)
    p.add_argument("--vm-data",required=True)
    p.add_argument("--vm-pid-file",required=True)
    p.add_argument("--repeats",type=int,default=4)
    a=p.parse_args()
    m=json.loads(pathlib.Path(a.manifest).read_text())
    if not m["days"]==3 or not m["total_series"]>165000:
        raise AssertionError(("this diagnostic needs >165k 3d series",m))
    start,end,step=m["query_start_epoch"],m["query_end_epoch"],m["step_seconds"]
    metric=m["metric"]
    base=f"avg_over_time({metric}[1d])"
    expr=f"avg by(stage) ({base})"
    # SQL is the independent reference for fixed daily event-style samples.
    q=(f"SELECT toUInt64(toUnixTimestamp(toStartOfDay(ts))+86400) AS epoch, "
       "stage,avg(duration) AS value FROM jenkins_events "
       f"WHERE ts >= fromUnixTimestamp({start-86400}) AND ts < fromUnixTimestamp({end}) "
       "GROUP BY epoch,stage FORMAT JSONEachRow")
    _,body=post("http://127.0.0.1:18191",q)
    expected=normalized_events(body)
    results={"run_id":os.getenv("GITHUB_RUN_ID"),"manifest":m,
             "source":"VM1.134.0 single-server with explicit 165k matching MetricID cap",
             "semantics":"avg_of_one_sample_series reconstructed by sum/count across disjoint project labels",
             "reference_output_points":len(expected),"tests":{}}
    status,uncapped_ms,uncapped=vm_call(expr,start,end,step)
    assert status==200,("uncapped VM query failed",status,uncapped)
    compare(expected,normalize(uncapped),"uncapped","VM")
    results["tests"]["uncapped"]={"status":status,"ms":uncapped_ms,"exact":True}
    print("JENKINS_SHARD_UNCAPPED",json.dumps(results["tests"]["uncapped"]),flush=True)

    # Restart VM safely on the SAME dataset with just the cap changed.
    pid=int(pathlib.Path(a.vm_pid_file).read_text())
    os.kill(pid,signal.SIGTERM)
    for _ in range(150):
        try:
            urllib.request.urlopen("http://127.0.0.1:8430/health",timeout=1)
        except Exception:
            break
        time.sleep(.2)
    else:
        raise RuntimeError("VictoriaMetrics old server has not stopped")
    log=pathlib.Path(a.output).parent/"vm-sharded-server.log"
    command=[a.vm_binary,
        "-storageDataPath="+a.vm_data,
        "-retentionPeriod=4w",
        "-httpListenAddr=127.0.0.1:8430",
        "-search.disableCache",
        "-search.maxUniqueTimeseries=165000"]
    with log.open("wb") as output:
        child=subprocess.Popen(command,stdout=output,stderr=subprocess.STDOUT)
    pathlib.Path(a.output).parent.joinpath("vm-sharded-pid.txt").write_text(str(child.pid)+"\n")
    for _ in range(150):
        if child.poll() is not None:raise RuntimeError(("capped VM exited",child.returncode,log.read_text()[-3000:]))
        try:
            with urllib.request.urlopen("http://127.0.0.1:8430/health",timeout=1) as f:
                if f.status==200:break
        except Exception:
            pass
        time.sleep(.2)
    else:raise RuntimeError("capped VM not ready")
    status,full_ms,full=vm_call(expr,start,end,step)
    results["tests"]["capped_full"]={"http_status":status,"ms":full_ms,
                                     "error":full.get("error","")[:1500]}
    assert status==422 and "exceeds 165000" in full.get("error",""),(status,full)
    print("JENKINS_SHARD_FULL_422",json.dumps(results["tests"]["capped_full"]),flush=True)

    # A query with one evaluation day may still use adjacent daily index dates.
    # Record either success or rejection. Do NOT silently assume day splitting works.
    daily=[]
    for d in range(m["days"]):
        end_at=start+d*step
        status,ms,payload=vm_call(expr,end_at,end_at,step)
        row={"day":d,"epoch":end_at,"http_status":status,"wall_ms":ms}
        if status==200:
            got=normalize(payload)
            want={k:v for k,v in expected.items() if k[1]==end_at}
            row["validation"]=compare(want,got,"day-"+str(d),"VM")
        else:
            row["error"]=payload.get("error","")[:700]
        daily.append(row)
        print("JENKINS_SHARD_DAY",json.dumps(row,sort_keys=True),flush=True)
    results["tests"]["daily"]=daily

    # Disjoint label shards: 34 projects each, selected through anchored RE.
    patterns=["project-(0[0-9]|1[0-9]|2[0-9]|3[0-3])",
              "project-(3[4-9]|4[0-9]|5[0-9]|6[0-7])"]
    shard_results=[]
    for trial in range(a.repeats+1):
        total_ms=0.
        partials=[]
        for shard,regex in enumerate(patterns):
            selector=f'{metric}{{project=~"{regex}"}}'
            rolled=f'avg_over_time({selector}[1d])'
            for kind in ("sum","count"):
                q=f"{kind} by(stage) ({rolled})"
                status,ms,payload=vm_call(q,start,end,step)
                total_ms+=ms
                if status!=200:
                    raise AssertionError(("shard query rejected",shard,kind,status,payload))
                vals=normalize(payload)
                partials.append((shard,kind,vals))
        sums,counts={},{}
        for shard,kind,rows in partials:
            target=sums if kind=="sum" else counts
            for key,v in rows.items():
                target[key]=target.get(key,0.)+v
        if set(sums)!=set(counts):
            raise AssertionError(("sum/count group mismatch",len(sums),len(counts)))
        agg={k:v/counts[k] for k,v in sums.items() if counts[k]>0}
        validation=compare(expected,agg,"shard_2","weighted_vm")
        stats={"trial":trial,"total_http_ms":round(total_ms,3),
               "calls":len(partials),"points":len(agg),
               "max_abs_error":validation["max_abs_error"]}
        shard_results.append(stats)
        print("JENKINS_SHARD_TRIAL",json.dumps(stats,sort_keys=True),flush=True)
    measured=[x["total_http_ms"] for x in shard_results[1:]]
    results["tests"]["project_shards"]={
        "patterns":patterns,"count":2,"calls_per_full_query":4,
        "semantics":"sum(partial_sum)/sum(partial_count), not average of averages",
        "p50_ms":pct(measured,.5),"p95_ms":pct(measured,.95),
        "trials":shard_results,"all_outputs_exact":True}

    path=pathlib.Path(a.output)
    path.write_text(json.dumps(results,indent=2)+"\n")
    print("JENKINS_SHARD_DONE",json.dumps({"run_id":results["run_id"],
          "full_422":True,"days_success":sum(x["http_status"]==200 for x in daily),
          "project_shards_exact":True,
          "shards_p50_ms":results["tests"]["project_shards"]["p50_ms"]},sort_keys=True),flush=True)


if __name__=="__main__":
    main()
