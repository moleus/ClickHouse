#!/usr/bin/env python3
"""Measure request-time count query overhead. Not a production guard."""
import argparse,json,os,pathlib
from bench_bucket_cxx_abca import pct,post

COUNTS=(210,1050,4200,16800,67200)

def main():
    p=argparse.ArgumentParser()
    p.add_argument("--output",required=True)
    a=p.parse_args()
    _,raw=post("http://127.0.0.1:18191",
        "SELECT name FROM system.tables WHERE database='default' "
        "AND startsWith(name,'.inner_id.tags.') FORMAT TSVRaw")
    names=raw.decode().strip().splitlines()
    assert len(names)==1,("expected one TimeSeries TAGS target",names)
    tags='default.`'+names[0].replace('`','``')+'`'
    queries={"http_floor":"SELECT 1 FORMAT TSV"}
    for n in COUNTS:
        metric=f"tslab_curve_{n}"
        queries.update({
            f"outer_count_{n}":f"SELECT count() FROM jenkins_ts WHERE metric_name='{metric}' FORMAT TSV",
            f"tags_count_{n}":f"SELECT count() FROM {tags} WHERE metric_name='{metric}' FORMAT TSV",
            f"tags_unique_{n}":f"SELECT uniqExact(id) FROM {tags} WHERE metric_name='{metric}' FORMAT TSV"
        })
    prefix="tslab_countprobe_"+os.environ.get("GITHUB_RUN_ID","local")
    data=[]
    for key,sql in queries.items():
        qids=[]
        trials=[]
        vals=[]
        for i in range(17):
            qid=f"{prefix}_{key}_{i}"
            elapsed,raw=post("http://127.0.0.1:18191",sql,qid)
            val=int(raw.decode().strip())
            if i>=2:
                qids.append(qid)
                trials.append(elapsed)
                vals.append(val)
        assert len(set(vals))==1,(key,vals)
        if key!="http_floor":
            n=int(key.rsplit("_",1)[1])
            assert vals[0]==n,(key,vals[0],n)
        row={"case":key,"answer":vals[0],"client_p50_ms":pct(trials,.5),
             "client_p95_ms":pct(trials,.95),"client_times_ms":trials,"query_ids":qids}
        data.append(row)
        print("COUNT_PROBE_CLIENT",json.dumps({k:v for k,v in row.items() if k!="query_ids"}),flush=True)
    post("http://127.0.0.1:18191","SYSTEM FLUSH LOGS")
    _,logs=post("http://127.0.0.1:18191",
        "SELECT query_id,query_duration_ms,read_rows,read_bytes,memory_usage "
        "FROM system.query_log WHERE type='QueryFinish' "
        "AND startsWith(query_id,'%s') FORMAT JSONEachRow"%prefix)
    record={x["query_id"]:x for line in logs.splitlines() if line.strip() for x in [json.loads(line)]}
    for row in data:
        matched=[record[q] for q in row.pop("query_ids")]
        row["server_p50_ms"]=pct([e["query_duration_ms"] for e in matched],.5)
        row["server_p95_ms"]=pct([e["query_duration_ms"] for e in matched],.95)
        row["read_rows_p50"]=pct([e["read_rows"] for e in matched],.5)
        row["read_bytes_p50"]=pct([e["read_bytes"] for e in matched],.5)
        row["tracked_memory_p95_bytes"]=pct([e["memory_usage"] for e in matched],.95)
        print("COUNT_PROBE_SERVER",json.dumps(row,separators=(",",":")),flush=True)
    pathlib.Path(a.output).write_text(json.dumps({"all_correct_in_fixture":True,"tags_target":tags,"cases":data},indent=2)+"\n")

if __name__=="__main__":
    main()
