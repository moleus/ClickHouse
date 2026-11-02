#!/usr/bin/env python3
"""Same-dataset, interleaved CH v4 off/on and VM single node 40d benchmark.

Compare numbers only where actual label/time/value results are equivalent.
"""
import argparse,json,math,os,pathlib,random,time,urllib.parse,urllib.request
from bench_bucket_cxx_abca import assert_same,normalize_any,pct,post
from bench_filtered_selector_v4_sql import sql_string

CH="http://127.0.0.1:18191"
VM="http://127.0.0.1:8430"
DAY=86400

def vm_get(params,instant=False):
    endpoint="/api/v1/query" if instant else "/api/v1/query_range"
    url=VM+endpoint+"?"+urllib.parse.urlencode({**params,"nocache":"1"})
    t0=time.perf_counter()
    with urllib.request.urlopen(url,timeout=240) as response:
        data=json.loads(response.read())
    ms=(time.perf_counter()-t0)*1000
    if data.get("status")!="success":
        raise AssertionError(("VictoriaMetrics error",params,data))
    return ms,data

def vm_normalized(payload):
    result={}
    for series in payload["data"]["result"]:
        labels=tuple(sorted((str(k),str(v)) for k,v in series["metric"].items()
                            if k!="__name__"))
        for ts,raw_value in series.get("values",[]):
            key=(labels,int(float(ts)))
            if key in result:raise AssertionError(("VM duplicate",key))
            result[key]=float(raw_value)
    return result

def wait_vm(meta):
    end=int(meta["end"])
    target={"tslab_sparse_40d":1000,"tslab_dense_40d":24000}
    for attempt in range(90):
        actual={}
        try:
            for name in target:
                _,payload=vm_get({"query":f"sum(count_over_time({name}[1d]))",
                                  "time":end},instant=True)
                series=payload["data"]["result"]
                actual[name]=float(series[0]["value"][1]) if series else 0.0
            if all(actual[x]==target[x] for x in target):
                print("PAIRED_VM_READY",json.dumps({"attempt":attempt+1,
                      "last_day_counts":actual},separators=(",",":")),flush=True)
                return
        except Exception as exc:
            print("PAIRED_VM_PENDING_EXCEPTION",str(exc)[:500],flush=True)
        print("PAIRED_VM_PENDING",json.dumps({"attempt":attempt+1,"counts":actual},
              separators=(",",":")),flush=True)
        time.sleep(2)
    raise AssertionError(("VictoriaMetrics imported values not visible",actual,target))

def rss_kib(pid):
    """Account for the ClickHouse watchdog child server, not only its parent."""
    if not pid:return None
    pending=[pid]
    visited=set()
    total=0
    while pending:
        current=pending.pop()
        if current in visited:continue
        visited.add(current)
        try:
            with open(f"/proc/{current}/status") as f:
                for line in f:
                    if line.startswith("VmRSS:"):
                        total+=int(line.split()[1])
                        break
            children=f"/proc/{current}/task/{current}/children"
            with open(children) as f:
                pending.extend(int(x) for x in f.read().split())
        except (OSError,ValueError):
            continue
    return total

def vm_metrics():
    try:
        with urllib.request.urlopen(VM+"/metrics",timeout=30) as r:
            raw=r.read().decode("utf-8","replace")
        keys=("process_resident_memory_bytes","go_memstats_heap_alloc_bytes",
              "vm_cache_size_bytes","vm_cache_entries")
        return {line.split()[0]:float(line.split()[-1]) for line in raw.splitlines()
                if line and not line.startswith("#") and line.startswith(keys)
                and len(line.split())>=2 and "{" not in line.split()[0]}
    except Exception as exc:return {"error":str(exc)}

def dataset_cases(meta):
    end=int(meta["end"])
    for kind in ("sparse","dense"):
        metric=f"tslab_{kind}_40d"
        for e in (3,14,40):
            start=end-(e-1)*DAY
            for label,filter_text in (
                 ("whole",""),("stage",'{stage="stage-0007"}'),
                 ("project",'{project="project-00"}')):
                expr=f"avg by(stage)(avg_over_time({metric}{filter_text}[1d]))"
                name=f"{kind}_{label}_e{e}"
                q=f"SELECT * FROM prometheusQueryRange(cost_ts,{sql_string(expr)},{start},{end},86400)"
                yield name,expr,q,start,end

def main():
    p=argparse.ArgumentParser()
    p.add_argument("--manifest",required=True)
    p.add_argument("--vm-pid",required=True)
    p.add_argument("--ch-pid",required=True)
    p.add_argument("--output",required=True)
    a=p.parse_args()
    meta=json.loads(pathlib.Path(a.manifest).read_text())
    wait_vm(meta)
    vm_pid=int(pathlib.Path(a.vm_pid).read_text().strip())
    ch_pid=int(pathlib.Path(a.ch_pid).read_text().strip())
    run=os.getenv("GITHUB_RUN_ID","local")
    before={"vm_rss_kib":rss_kib(vm_pid),"ch_rss_kib":rss_kib(ch_pid),
            "vm_runtime":vm_metrics()}
    output=[]
    raw_records=[]
    for name,expr,base,start,end in dataset_cases(meta):
        canonical=None
        stats={"off":[],"on":[],"vm":[]}
        runorder=["off","vm","on"]
        for rep in range(8):
            # 2 warmups then 6 timed repetitions. Rotate engine order.
            modes=runorder[(rep%3):]+runorder[:(rep%3)]
            if rep%2: modes=list(reversed(modes))
            for method in modes:
                if method=="vm":
                    elapsed,payload=vm_get({"query":expr,"start":start,
                                            "end":end,"step":DAY})
                    value=vm_normalized(payload)
                else:
                    query=base+(" SETTINGS promql_enable_native_one_point_lab=1"
                                if method=="on" else
                                " SETTINGS promql_enable_native_one_point_lab=0")
                    query+=" FORMAT JSONEachRow"
                    qid=f"paired_{run}_{name}_{method}_{rep}"
                    elapsed,raw=post(CH,query,qid)
                    value=normalize_any(raw)
                if canonical is None:canonical=value
                else:
                    try:assert_same(canonical,value,name,method)
                    except Exception as exc:
                        record={"case":name,"method":method,"trial":rep,
                                "error":str(exc),"expected_points":len(canonical),
                                "actual_points":len(value)}
                        print("PAIRED_CORRECTNESS_BLOCKER",json.dumps(record),flush=True)
                        raise
                if rep>=2:
                    stats[method].append({"ms":round(elapsed,3),
                                          "rss_vm_kib":rss_kib(vm_pid),
                                          "rss_ch_kib":rss_kib(ch_pid)})
        report={"case":name,"expr":expr,"start":start,"end":end,
                "points":len(canonical),"results":{}}
        for method,values in stats.items():
            times=[x["ms"] for x in values]
            report["results"][method]={"p50_ms":pct(times,.5),"p95_ms":pct(times,.95),
                "times_ms":times,"rss_vm_after_kib":max(x["rss_vm_kib"] or 0 for x in values),
                "rss_ch_after_kib":max(x["rss_ch_kib"] or 0 for x in values)}
        report["on_over_vm"]=report["results"]["on"]["p50_ms"]/max(
             0.0001,report["results"]["vm"]["p50_ms"])
        report["off_over_vm"]=report["results"]["off"]["p50_ms"]/max(
             0.0001,report["results"]["vm"]["p50_ms"])
        output.append(report)
        print("PAIRED_CH_VM_CASE",json.dumps({"case":name,"points":len(canonical),
              "vm_ms":report["results"]["vm"]["p50_ms"],
              "ch_off_ms":report["results"]["off"]["p50_ms"],
              "ch_v4_ms":report["results"]["on"]["p50_ms"],
              "v4_over_vm":report["on_over_vm"]},separators=(",",":")),flush=True)
    post(CH,"SYSTEM FLUSH LOGS")
    _,raw=post(CH,"SELECT query_id,query_duration_ms,read_rows,read_bytes,"
        "memory_usage,ProfileEvents FROM system.query_log "
        "WHERE type='QueryFinish' AND startsWith(query_id,"
        f"{sql_string('paired_'+run+'_')}) FORMAT JSONEachRow")
    logs={x["query_id"]:x for line in raw.splitlines() if line.strip()
          for x in [json.loads(line)]}
    for row in output:
        for method in ("off","on"):
            ids=[f"paired_{run}_{row['case']}_{method}_{i}" for i in range(2,8)]
            if not all(x in logs for x in ids):
                raise AssertionError(("missing QueryFinish rows",row["case"],method))
            entries=[logs[x] for x in ids]
            row["results"][method].update({
                "server_p50_ms":pct([x["query_duration_ms"] for x in entries],.5),
                "read_rows_p50":pct([x["read_rows"] for x in entries],.5),
                "read_bytes_p50":pct([x["read_bytes"] for x in entries],.5),
                "ch_query_peak_memory_p95":pct([x["memory_usage"] for x in entries],.95)})
    after={"vm_rss_kib":rss_kib(vm_pid),"ch_rss_kib":rss_kib(ch_pid),
           "vm_runtime":vm_metrics()}
    result={"all_exact":True,"before":before,"after":after,
            "cases":output,
            "note":"CH query memory and VM RSS are different measurement types"}
    pathlib.Path(a.output).write_text(json.dumps(result,indent=2)+"\n")
    pathlib.Path(a.output).with_name("paired-querylog.json").write_text(
         json.dumps(logs,indent=2)+"\n")
    summary={"cases":len(output),
             "v4_faster_than_vm":sum(x["on_over_vm"]<1 for x in output),
             "v4_slower_than_vm":sum(x["on_over_vm"]>1 for x in output),
             "vm_idle_rss_kib":before["vm_rss_kib"],
             "ch_idle_rss_kib":before["ch_rss_kib"]}
    print("PAIRED_CH_VM_COMPLETE",json.dumps(summary,separators=(",",":")),flush=True)

if __name__=="__main__":main()
