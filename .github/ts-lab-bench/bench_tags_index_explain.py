#!/usr/bin/env python3
"""Inspect the default v11 tags_idx: row postings, direct-read query eligibility.

The same TimeSeries tag rows are queried using the original tags[key]=value
and native mapContainsKeyValue(tags,key,value), both with skip indexes on/off.
Every returned full series ID set must match exactly. QueryFinish read_rows,
read_bytes, memory and latency are recorded. No additional label-index table.
"""
import argparse
import json
import os
import pathlib
import time
from bench_jenkins_querypath import post,pct


def sql(statement,query_id=None):
    latency,data=post("http://127.0.0.1:18191",statement,query_id)
    return latency,data.decode("utf-8","replace")


def main():
    p=argparse.ArgumentParser()
    p.add_argument("--output",required=True)
    p.add_argument("--repeats",type=int,default=5)
    p.add_argument("--warmups",type=int,default=1)
    a=p.parse_args()
    root=pathlib.Path(a.output)
    root.parent.mkdir(parents=True,exist_ok=True)
    _,raw=sql("SELECT name FROM system.tables WHERE database='default' "
              "AND startsWith(name,'.inner_id.tags.') FORMAT TSVRaw")
    names=raw.strip().splitlines()
    if len(names)!=1:raise AssertionError(("expected one TimeSeries tags target",names))
    name=names[0]
    table="default.`"+name.replace("`","``")+"`"
    _,ddl=sql("SHOW CREATE TABLE "+table+" FORMAT TSVRaw")
    root.with_name("tags-target-ddl.txt").write_text(ddl)
    idx_hints=["tags_idx","keyValuePairs"]
    matches={h:h in ddl for h in idx_hints}
    print("TAGS_INDEX_DDL",json.dumps({"table":table,"default_index_present":matches},sort_keys=True),flush=True)
    _,indices=sql("SELECT name,type,granularity FROM system.data_skipping_indices "
                  "WHERE database='default' AND table='"+name+"' FORMAT JSONEachRow")
    root.with_name("tags-target-skip-indices.jsonl").write_text(indices)
    variants=[
        ("plain_skip",False,True),
        ("mapkv_skip",True,True),
        ("plain_no_skip",False,False),
        ("mapkv_no_skip",True,False),
    ]
    cases=[
        ("stage", [("stage","stage-0007")]),
        ("project",[("project","project-00")]),
        ("stage_and_branch",[("stage","stage-0007"),("branch","main")]),
        ("stage_and_project",[("stage","stage-0007"),("project","project-00")]),
    ]
    result={"table":table,"default_index_present":matches,"run_id":os.getenv("GITHUB_RUN_ID"),
            "cases":{}}
    run=os.getenv("GITHUB_RUN_ID","local")
    for case,labels in cases:
        expected=None
        variations={}
        for variant,use_map,enabled in variants:
            conditions=["metric_name='jenkins_avs_build_stage_duration'"]
            for label,value in labels:
                if use_map:conditions.append(f"mapContainsKeyValue(tags,'{label}','{value}')")
                else:conditions.append(f"tags['{label}']='{value}'")
            # Ordered ID output makes full selected series ID sets comparable.
            query=("SELECT toString(id) AS selected_id FROM "+table+
                   " WHERE "+" AND ".join(conditions)+
                   " ORDER BY selected_id SETTINGS use_skip_indexes="+("1" if enabled else "0"))
            timing=[]
            for i in range(a.warmups+a.repeats):
                qid=f"tslab_tagsidx_{run}_{case}_{variant}_{i}"
                ms,data=sql(query+" FORMAT TSVRaw",qid)
                values=data.splitlines()
                if expected is None:expected=values
                if values!=expected:
                    raise AssertionError(("selected IDs differ",case,variant,i,len(expected),len(values)))
                if i>=a.warmups:timing.append(ms)
            variations[variant]={"p50_ms":pct(timing,.5),"p95_ms":pct(timing,.95),
                                 "times_ms":timing,"selected_ids":len(expected),"query":query}
            try:
                _,explain=sql("EXPLAIN indexes=1 "+query)
                root.with_name("tags-index-"+case+"-"+variant+".explain.txt").write_text(explain)
            except Exception as e:
                root.with_name("tags-index-"+case+"-"+variant+".explain-error.txt").write_text(str(e)+"\n")
        result["cases"][case]={"matchers":labels,"variants":variations,
                              "all_full_ID_sets_equal":True}
        print("TAGS_INDEX_TIMING",case,json.dumps({
            "n_ids":len(expected),"p50_ms":{k:v["p50_ms"] for k,v in variations.items()},
            "all_ids_equal":True},sort_keys=True),flush=True)
    sql("SYSTEM FLUSH LOGS")
    _,logs=sql("SELECT query_id,read_rows,read_bytes,memory_usage,query_duration_ms,exception_code "
               "FROM system.query_log WHERE type='QueryFinish' "
               f"AND startsWith(query_id,'tslab_tagsidx_{run}') FORMAT JSONEachRow")
    rawrows={x["query_id"]:x for line in logs.splitlines() if line.strip() for x in [json.loads(line)]}
    for case,item in result["cases"].items():
        for variant,stats in item["variants"].items():
            ids=[f"tslab_tagsidx_{run}_{case}_{variant}_{i}"
                 for i in range(a.warmups,a.warmups+a.repeats)]
            rows=[rawrows[k] for k in ids if k in rawrows]
            if len(rows)!=a.repeats:raise AssertionError(("missing query_log",case,variant,len(rows)))
            if any(x["exception_code"] for x in rows):raise AssertionError(("query exception",case,variant))
            stats["read_rows_p50"]=pct([r["read_rows"] for r in rows],.5)
            stats["read_bytes_p50"]=pct([r["read_bytes"] for r in rows],.5)
            stats["query_memory_p95_bytes"]=pct([r["memory_usage"] for r in rows],.95)
        print("TAGS_INDEX_READS",case,json.dumps({
            k:{"read_rows":v["read_rows_p50"],"read_bytes":v["read_bytes_p50"],
               "p50_ms":v["p50_ms"]} for k,v in item["variants"].items()},sort_keys=True),flush=True)
    root.write_text(json.dumps(result,indent=2)+"\n")
    print("TAGS_INDEX_DIAG_DONE",json.dumps({"cases":len(cases),"index_DDL":matches,
        "same_series_ids":True},sort_keys=True),flush=True)


if __name__=="__main__":
    main()
