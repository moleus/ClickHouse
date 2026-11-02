#!/usr/bin/env python3
"""Proof-first A/B/A: unchanged native grid versus translated native one-point SQL."""
import argparse,json,math,os,pathlib
from bench_bucket_cxx_abca import pct,post
from bench_native_one_point_sql import translated_query,parse,compare

COUNTS=[4200,67200]
STEPS=[3,14,40]
PHASES=[("native_before","original"),("one_point","translated"),("native_after","original")]

def query_original(metric,start,end):
    return ("SELECT * FROM prometheusQueryRange(jenkins_ts,"
            "'avg by(stage)(avg_over_time(%s[1d]))',%d,%d,86400) FORMAT JSONEachRow"
            %(metric,start,end))

def run(m,output):
    end=int(m["query_end_epoch"])
    records=[]
    prefix="tslab_native_onept_"+os.environ.get("GITHUB_RUN_ID","local")
    for n in COUNTS:
        metric=f"tslab_curve_{n}"
        _,raw=post("http://127.0.0.1:18191",
                   "SELECT count() FROM jenkins_ts WHERE metric_name='%s' FORMAT TSV"%metric)
        true_n=int(raw.decode().strip())
        assert true_n==n,(metric,true_n)
        for e in STEPS:
            start=end-(e-1)*86400
            reference=None
            phases={}
            for phase,kind in PHASES:
                sql=(query_original(metric,start,end) if kind=="original"
                     else translated_query(start,end,86400,table="jenkins_ts",metric=metric))
                elapsed=[]
                ids=[]
                for i in range(7):
                    qid=f"{prefix}_{n}_{e}_{phase}_{i}"
                    ms,raw=post("http://127.0.0.1:18191",sql,qid)
                    observed=parse(raw)
                    assert observed,("no results",metric,e,phase)
                    if reference is None:
                        reference=observed
                    else:
                        delta=compare(reference,observed)
                        assert not any(delta.values()),("numeric mismatch",metric,e,phase,i,delta)
                    if i>=2:
                        elapsed.append(ms)
                        ids.append(qid)
                phases[phase]={"p50_ms":pct(elapsed,.5),"p95_ms":pct(elapsed,.95),
                               "times_ms":elapsed,"query_ids":ids}
            record={"selected_series":n,"grid_steps":e,"result_points":len(reference),"phases":phases}
            records.append(record)
            print("NATIVE_ONE_POINT_CURVE",json.dumps(record,separators=(',',':')),flush=True)
    post("http://127.0.0.1:18191","SYSTEM FLUSH LOGS")
    _,raw=post("http://127.0.0.1:18191",
        "SELECT query_id,read_rows,read_bytes,memory_usage,query_duration_ms "
        "FROM system.query_log WHERE type='QueryFinish' AND startsWith(query_id,'%s') FORMAT JSONEachRow"%prefix)
    log={obj["query_id"]:obj for line in raw.splitlines() if line.strip() for obj in [json.loads(line)]}
    for row in records:
        for phase,item in row["phases"].items():
            entries=[log[id] for id in item.pop("query_ids")]
            item["read_rows_p50"]=pct([i["read_rows"] for i in entries],.5)
            item["read_bytes_p50"]=pct([i["read_bytes"] for i in entries],.5)
            item["memory_p95_bytes"]=pct([i["memory_usage"] for i in entries],.95)
            item["server_duration_p50_ms"]=pct([i["query_duration_ms"] for i in entries],.5)
        after=row["phases"]["native_after"]["p50_ms"]
        candidate=row["phases"]["one_point"]["p50_ms"]
        row["speedup_vs_recheck"]=after/candidate
        print("NATIVE_ONE_POINT_CURVE_DONE",json.dumps(row,separators=(',',':')),flush=True)
    pathlib.Path(output).write_text(json.dumps({"all_exact":True,"cases":records},indent=2)+"\n")

if __name__=="__main__":
    p=argparse.ArgumentParser()
    p.add_argument("--manifest",required=True)
    p.add_argument("--output",required=True)
    args=p.parse_args()
    run(json.loads(pathlib.Path(args.manifest).read_text()),args.output)
