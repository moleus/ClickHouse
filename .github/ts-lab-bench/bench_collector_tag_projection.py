#!/usr/bin/env python3
"""Probe TimeSeries per-query tags collector with full vs projected labels."""
import argparse,json,os,pathlib,random,subprocess,threading,time
from bench_jenkins_querypath import post
from bench_filtered_selector_v4_sql import sql_string

CH="http://127.0.0.1:18191"
METRIC="jenkins_ci_95k_daily"

def sql(statement,qid=None):
    t=time.perf_counter()
    try:
        ms,raw=post(CH,statement,qid)
        return {"ok":True,"ms":ms,"body":raw.decode("utf-8","replace")}
    except Exception as exc:
        err=str(exc)
        return {"ok":False,"ms":1000*(time.perf_counter()-t),
                "error":err[:3000],"oom":"MEMORY_LIMIT_EXCEEDED" in err}

def server_restart(path,binary,phase):
    subprocess.run([str(path),str(binary),phase],check=True,timeout=150)
    return int(pathlib.Path(os.environ["RUNNER_TEMP"],"ch.pid").read_text())

def proc_rss(pid):
    pending=[pid];visited=set();rss=0
    while pending:
        n=pending.pop()
        if n in visited:continue
        visited.add(n)
        try:
            text=pathlib.Path(f"/proc/{n}/status").read_text()
            for l in text.splitlines():
                if l.startswith("VmRSS:"):rss+=int(l.split()[1])
            pending.extend(int(v) for v in pathlib.Path(
                f"/proc/{n}/task/{n}/children").read_text().split())
        except (ValueError,OSError):continue
    return rss

def run_monitored(q,pid,qid):
    stop=threading.Event();rss_samples=[]
    def sample():
        while not stop.is_set():
            rss_samples.append(proc_rss(pid))
            stop.wait(.15)
    t=threading.Thread(target=sample,daemon=True);t.start()
    try:r=sql(q,qid)
    finally:stop.set();t.join(timeout=5)
    r["query_id"]=qid
    r["peak_process_rss_kib"]=max(rss_samples,default=0)
    r["rss_after_kib"]=proc_rss(pid)
    return r

def main():
    p=argparse.ArgumentParser()
    p.add_argument("--manifest",required=True)
    p.add_argument("--binary",required=True)
    p.add_argument("--restart-helper",required=True)
    p.add_argument("--output",required=True)
    a=p.parse_args()
    m=json.loads(pathlib.Path(a.manifest).read_text())
    assert m["series"]==1330000
    run=os.getenv("GITHUB_RUN_ID","local")
    pid=server_restart(a.restart_helper,a.binary,"tagproj_discovery")
    uuid=sql("SELECT toString(uuid) FROM system.tables WHERE database='default' "
             "AND name='churn_ts' FORMAT TSVRaw")
    assert uuid["ok"] and len(uuid["body"].strip())==36,uuid
    quote=chr(96)
    table="default."+quote+".inner_id.tags."+uuid["body"].strip()+quote
    # Function is explicitly stateful and returns the same ID. The dependent
    # timeSeriesIdToGroup call forces the per-query collector to be used.
    variants={
        "control":("SELECT count() AS n,sum(length(tags)) AS tag_pairs "
                   f"FROM {table} WHERE metric_name={sql_string(METRIC)} FORMAT TSV"),
        "full":("SELECT count() AS n,max(timeSeriesIdToGroup("
                "timeSeriesStoreTags(id,tags,'__name__',metric_name))) AS max_group "
                f"FROM {table} WHERE metric_name={sql_string(METRIC)} "
                "SETTINGS max_threads=4 FORMAT TSV"),
        "stage":("SELECT count() AS n,max(timeSeriesIdToGroup("
                 "timeSeriesStoreTags(id,NULL,'stage',tags['stage']))) AS max_group "
                 f"FROM {table} WHERE metric_name={sql_string(METRIC)} "
                 "SETTINGS max_threads=4 FORMAT TSV"),
    }
    rng=random.Random(7272727)
    matrix=[v for _ in range(3) for v in ("full","stage")]
    rng.shuffle(matrix)
    result={"source":m,"queries":[],"variants":variants,"code":"stage projection isolated; no PromQL claims"}
    for i,name in enumerate(["control"]+matrix):
        pid=server_restart(a.restart_helper,a.binary,f"tagproj_{i}_{name}")
        baseline=proc_rss(pid)
        qid=f"tagproj_{run}_{name}_{i}"
        res=run_monitored(variants[name],pid,qid)
        res["name"]=name
        res["rss_before_kib"]=baseline
        if res["ok"]:
            count,value=res["body"].strip().split("\t")
            assert int(count)==m["series"],("rows differ",name,count)
            res["rows"]=int(count)
            res["max_group_or_label_pairs"]=int(value)
            if name=="full":
                assert 1000000<=int(value)<=m["series"],("expected >1m distinct label groups",value)
            if name=="stage":
                assert 1<=int(value)<=40,("expected 38 stage groups",value)
        result["queries"].append(res)
        print("TAG_PROJECTION_AB",json.dumps({
             "name":name,"trial":i,"ok":res["ok"],
             "duration_ms":round(res["ms"],2),
             "rows":res.get("rows"),
             "max_group_or_label_pairs":res.get("max_group_or_label_pairs"),
             "query_peak_rss_mb":round(res["peak_process_rss_kib"]/1024,2),
             "rss_after_mb":round(res["rss_after_kib"]/1024,2),
             "error":res.get("error","")[:600]},separators=(",",":")),flush=True)
    sql("SYSTEM FLUSH LOGS")
    q=sql("SELECT query_id,type,exception_code,memory_usage,read_rows,read_bytes,"
          "query_duration_ms,ProfileEvents FROM system.query_log "
          f"WHERE startsWith(query_id,{sql_string('tagproj_'+run+'_')}) FORMAT JSONEachRow")
    if q["ok"]:
        result["querylog"]=[json.loads(z) for z in q["body"].splitlines() if z.strip()]
    else:result["querylog_error"]=q
    phases=[x for x in result["queries"] if x["name"]!="control"]
    if all(x["ok"] for x in phases):
        byvariant={k:[x for x in phases if x["name"]==k] for k in ("full","stage")}
        means={k:sorted(x["ms"] for x in arr)[1] for k,arr in byvariant.items()}
        rss={k:sorted(x["peak_process_rss_kib"] for x in arr)[1] for k,arr in byvariant.items()}
        result["summary"]={"all_passed":True,"median_latency_ms":means,
          "median_process_peak_kib":rss,"stage_vs_full_latency_ratio":means["stage"]/means["full"],
          "stage_vs_full_rss_ratio":rss["stage"]/rss["full"]}
    else:
        result["summary"]={"all_passed":False}
    pathlib.Path(a.output).write_text(json.dumps(result,indent=2)+"\n")
    print("TAG_PROJECTION_DONE",json.dumps(result["summary"],separators=(",",":")),flush=True)
if __name__=="__main__":main()
