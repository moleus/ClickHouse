#!/usr/bin/env python3
"""Native ClickHouse TimeSeries v11: normalized label postings compared to existing text(tags).

Proof-of-concept index table derived from the SAME TS tags target, no
external event storage. Build once, then test exact ID filtering before
reading TS samples. Keep exact original tags predicate in every query
so a stale/advisory index cannot produce false positives. A
production design MUST maintain index transactionally and prove no
false negatives after late writes, replacement and retention; this
prototype does not satisfy that requirement.
"""
import argparse
import json
import os
import pathlib
import time

from bench_jenkins_querypath import (
    compare,normalized_ch,normalized_events,normalized_vm,pct,post,query_vm
)


def sql(query,query_id=None):
    _,body=post("http://127.0.0.1:18191",query,query_id)
    return body.decode().strip()


def one_inner(kind):
    names=sql("SELECT name FROM system.tables WHERE database='default' "
              f"AND startsWith(name,'.inner_id.{kind}.') FORMAT TSVRaw").splitlines()
    assert len(names)==1,(kind,names)
    return "default.`"+names[0].replace("`","``")+"`"


def main():
    p=argparse.ArgumentParser()
    p.add_argument("--manifest",required=True)
    p.add_argument("--output",required=True)
    p.add_argument("--repeats",type=int,default=4)
    p.add_argument("--warmups",type=int,default=1)
    a=p.parse_args()
    m=json.loads(pathlib.Path(a.manifest).read_text())
    samples,tags=one_inner("samples"),one_inner("tags")
    assert m["total_samples"]==m["total_series"],"this comparison only uses one-sample series"
    metric=m["metric"]
    start,end,step=(m[k] for k in ("query_start_epoch","query_end_epoch","step_seconds"))
    t0=time.perf_counter()
    # A logical (metric,key,value)->IDs index. IDs are still CH TimeSeries native IDs.
    # An independent index target is being studied; it is not connected to the TS
    # insert path in this experiment.
    ddl=(f"CREATE TABLE ts_lab_label_postings ENGINE=MergeTree "
         f"ORDER BY (metric_name, label_key, label_value, id) "
         f"SETTINGS index_granularity=8192 AS "
         f"SELECT metric_name,tupleElement(kv,1) AS label_key,"
         f"tupleElement(kv,2) AS label_value,id "
         f"FROM {tags} ARRAY JOIN arrayZip(mapKeys(tags),mapValues(tags)) AS kv")
    sql(ddl)
    index_build_sec=round(time.perf_counter()-t0,3)
    input_ids=int(sql(f"SELECT uniqExact(id) FROM {tags}"))
    indexed_ids=int(sql("SELECT uniqExact(id) FROM ts_lab_label_postings"))
    assert indexed_ids==input_ids==m["total_series"],(indexed_ids,input_ids,m["total_series"])
    entry_count=int(sql("SELECT count() FROM ts_lab_label_postings"))
    print("LABEL_POSTINGS_BUILT",json.dumps({"ids":indexed_ids,"rows":entry_count,
         "seconds":index_build_sec,"rows_per_id":entry_count/indexed_ids}),flush=True)
    a_query=f"SELECT table,sum(rows) rows,sum(bytes_on_disk) bytes,sum(marks_bytes) marks FROM system.parts WHERE database='default' AND active AND (table='ts_lab_label_postings' OR startsWith(table,'.inner_id.tags.')) GROUP BY table FORMAT JSONEachRow"
    index_parts=[json.loads(ln) for ln in sql(a_query).splitlines()]
    cases=[
        ("all","", "",[]),
        ("branch_main",'{branch="main"}',"AND t.tags['branch']='main'",[("branch","main")]),
        ("project_00",'{project="project-00"}',"AND t.tags['project']='project-00'",[("project","project-00")]),
        ("stage_exact",'{stage="stage-0007"}',"AND t.tags['stage']='stage-0007'",[("stage","stage-0007")]),
        ("stage_branch",'{stage="stage-0007",branch="main"}',
           "AND t.tags['stage']='stage-0007' AND t.tags['branch']='main'",
           [("stage","stage-0007"),("branch","main")]),
    ]
    prefix="tslab_labelpostings_"+str(os.getenv("GITHUB_RUN_ID","local"))
    result={"run":os.getenv("GITHUB_RUN_ID"),"manifest":m,
            "schema_ddl":ddl,"index_build_seconds":index_build_sec,
            "label_index_parts":index_parts,"indexed_ids":indexed_ids,
            "posting_rows":entry_count,"cases":{},
            "warning":"This is an advisory snapshot index, not a production-consistent transactional MV"}
    for name,matcher,original_filters,lookups in cases:
        q=f"avg by(stage)(avg_over_time({metric}{matcher}[1d]))"
        epoch="toUInt64(toUnixTimestamp(toStartOfDay(ts - INTERVAL 1 MILLISECOND) + INTERVAL 1 DAY))"
        source=(f"SELECT s.id AS series_id, t.tags['stage'] AS stage,"
                f" tupleElement(sample,1) AS ts, tupleElement(sample,2) AS value"
                f" FROM {samples} AS s ANY INNER JOIN {tags} AS t ON s.id=t.id "
                f"ARRAY JOIN s.samples AS sample "
                f"WHERE t.metric_name='{metric}' {original_filters}"
                f" AND ts>fromUnixTimestamp({start-step}) AND ts<=fromUnixTimestamp({end})")
        candidates={}
        queries={}
        for mode in ("inner_baseline","posting_intersect"):
            suffix=""
            if mode=="posting_intersect":
                for key,value in lookups:
                    suffix+=(f" AND s.id IN (SELECT id FROM ts_lab_label_postings WHERE "
                             f"metric_name='{metric}' AND label_key='{key}' AND label_value='{value}')")
            src=source+suffix
            query=(f"SELECT stage,epoch,avg(series_avg) AS value FROM "
                   f"(SELECT series_id,stage,{epoch} AS epoch,avg(value) AS series_avg "
                   f"FROM ({src}) GROUP BY series_id,stage,epoch)"
                   f" GROUP BY stage,epoch ORDER BY stage,epoch FORMAT JSONEachRow")
            queries[mode]=query
        # Independent event oracle for every case, including combined filters.
        conditions={"all":"","branch_main":"AND branch='main'",
                    "project_00":"AND project='project-00'",
                    "stage_exact":"AND stage='stage-0007'",
                    "stage_branch":"AND stage='stage-0007' AND branch='main'"}
        event_sql=(f"SELECT toUInt64(toUnixTimestamp(toStartOfDay(ts))+86400) AS epoch,stage,"
                   f"avg(duration) AS value FROM jenkins_events "
                   f"WHERE ts>=fromUnixTimestamp({start-step}) AND ts<fromUnixTimestamp({end}) "
                   f"{conditions[name]} GROUP BY stage,epoch FORMAT JSONEachRow")
        canonical=normalized_events(post("http://127.0.0.1:18191",event_sql)[1])
        for mode,query in queries.items():
            times=[]
            for i in range(a.warmups+a.repeats):
                qid=f"{prefix}_{name}_{mode}_{i}"
                ms,body=post("http://127.0.0.1:18191",query,qid)
                compare(canonical,normalized_events(body),name,mode)
                if i>=a.warmups:times.append(ms)
            candidates[mode]={"p50_ms":pct(times,.5),"p95_ms":pct(times,.95),
                              "timings_ms":times,"all_values_exact":True,
                              "query_id_prefix":f"{prefix}_{name}_{mode}"}
        vm_times=[]
        for i in range(a.warmups+a.repeats):
            ms,payload=query_vm("http://127.0.0.1:8430",q,start,end,step)
            compare(canonical,normalized_vm(payload),name,"VM")
            if i>=a.warmups:vm_times.append(ms)
        candidates["vm"]={"p50_ms":pct(vm_times,.5),"p95_ms":pct(vm_times,.95)}
        result["cases"][name]={"points":len(canonical),"expr":q,"filter_postings":lookups,
                              "variants":candidates,"queries":queries}
        print("LABEL_POSTINGS_CASE",name,json.dumps({
            "p50_ms":{k:v["p50_ms"] for k,v in candidates.items()},
            "output_points":len(canonical),
            "all_CH_and_VM_values_exact":True},sort_keys=True),flush=True)
    sql("SYSTEM FLUSH LOGS")
    logs=[json.loads(ln) for ln in sql("SELECT query_id,read_rows,read_bytes,memory_usage,query_duration_ms,exception_code "
         "FROM system.query_log WHERE type='QueryFinish' "
         f"AND startsWith(query_id,'{prefix}') FORMAT JSONEachRow").splitlines()]
    by_id={x["query_id"]:x for x in logs}
    for name,row in result["cases"].items():
        for mode in ("inner_baseline","posting_intersect"):
            stats=row["variants"][mode]
            ids=[stats["query_id_prefix"]+"_"+str(i) for i in range(a.warmups,a.warmups+a.repeats)]
            rows=[by_id[x] for x in ids if x in by_id]
            if len(rows)!=a.repeats:raise AssertionError(("query_log incomplete",name,mode,len(rows)))
            if any(x["exception_code"] for x in rows):raise AssertionError(("query exception",name,mode))
            stats["read_rows_p50"]=pct([x["read_rows"] for x in rows],.5)
            stats["read_bytes_p50"]=pct([x["read_bytes"] for x in rows],.5)
            stats["query_memory_p95_bytes"]=pct([x["memory_usage"] for x in rows],.95)
    out=pathlib.Path(a.output);out.parent.mkdir(parents=True,exist_ok=True)
    out.write_text(json.dumps(result,indent=2)+"\n")
    print("LABEL_POSTINGS_DONE",json.dumps({"cases":len(cases),"all_exact":True,
         "new_index_rows":entry_count,"build_seconds":index_build_sec},sort_keys=True),flush=True)


if __name__=="__main__":
    main()
