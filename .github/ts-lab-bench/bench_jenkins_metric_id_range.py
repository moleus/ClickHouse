#!/usr/bin/env python3
"""Benchmark v11 series ID prefix/range in mixed-metric TimeSeries.

Uses the EXISTING TimeSeries v11 samples and tags MergeTree target tables;
does NOT create a separate events table. One candidate preserves a per-ID
average before avg by stage (correct for multiple samples per ID/day on
disjoint day windows). The other directly averages samples, valid ONLY
when one sample per ID/day; it deliberately guards this assumption.
The measured queries exclude instant-selector, rate, offset, HA and
overlapping-grid semantics. Source-to-source C++ lowering is NOT yet implemented.
"""
import argparse
import json
import os
import pathlib
import time
import urllib.parse
import urllib.request

from bench_jenkins_querypath import (
    compare,normalized_ch,normalized_events,normalized_vm,pct,post,query_vm
)


def sqlvalue(query):
    _,raw=post("http://127.0.0.1:18191",query)
    return raw.decode().strip()


def table_name(kind):
    needle=".inner_id."+kind+"."
    sql=("SELECT name FROM system.tables WHERE database='default' "
         "AND startsWith(name,'"+needle+"') FORMAT TSVRaw")
    names=sqlvalue(sql).splitlines()
    if len(names)!=1:
        raise AssertionError(("unexpected inner table names",kind,names))
    return "default.`"+names[0].replace("`","``")+"`"


def main():
    p=argparse.ArgumentParser()
    p.add_argument("--manifest",required=True)
    p.add_argument("--output",required=True)
    p.add_argument("--repeats",type=int,default=4)
    p.add_argument("--warmups",type=int,default=1)
    a=p.parse_args()
    m=json.loads(pathlib.Path(a.manifest).read_text())
    assert m["total_samples"]==m["total_series"],"direct avg across rows only valid when one sample per series"
    assert m["step_seconds"]==86400,"only disjoint UTC days"
    start,end,step=m["query_start_epoch"],m["query_end_epoch"],m["step_seconds"]
    samples,tags=table_name("samples"),table_name("tags")
    cases=[
        ("all","",""),
        ("branch_main",'{branch="main"}',"AND t.tags['branch']='main'"),
        ("project_00",'{project="project-00"}',"AND t.tags['project']='project-00'"),
        ("stage10",'{stage=~"stage-000[0-9]"}',
         "AND t.tags['stage'] IN ('stage-0000','stage-0001','stage-0002','stage-0003','stage-0004','stage-0005','stage-0006','stage-0007','stage-0008','stage-0009')"),
    ]
    root=pathlib.Path(a.output)
    root.parent.mkdir(parents=True,exist_ok=True)
    result={"run_id":os.getenv("GITHUB_RUN_ID"),"manifest":m,
            "tables":{"samples":samples,"tags":tags},
            "sql_semantics":"two_stage_avg matches avg by stage(avg_over_time[1d]) on one-day nonoverlapping windows; fast_one_sample only for unique one-sample series",
            "cases":{}}
    for name,match,ch_where in cases:
        expr=f"avg by(stage)(avg_over_time({m['metric']}{match}[1d]))"
        source_sql=(f"SELECT s.id AS series_id, t.tags['stage'] AS stage, "
                    f"tupleElement(sample,1) AS ts,tupleElement(sample,2) AS value "
                    f"FROM {samples} AS s INNER JOIN {tags} AS t ON s.id=t.id "
                    f"ARRAY JOIN s.samples AS sample "
                    f"WHERE t.metric_name='{m['metric']}' {ch_where} "
                    f"AND ts>fromUnixTimestamp({start-step}) "
                    f"AND ts<=fromUnixTimestamp({end})")
        # Shift exact midnight samples by 1 millisecond so (t-1d,t]
        # interval maps to the midnight t (not t+1d).
        bucket="toUInt64(toUnixTimestamp(toStartOfDay(ts - INTERVAL 1 MILLISECOND) + INTERVAL 1 DAY))"
        two_stage=(f"SELECT stage, epoch, avg(series_avg) AS value FROM "
                   f"(SELECT series_id,stage,{bucket} AS epoch, avg(value) AS series_avg "
                   f"FROM ({source_sql}) GROUP BY series_id,stage,epoch) "
                   f"GROUP BY stage,epoch ORDER BY stage,epoch FORMAT JSONEachRow")
        flat=(f"SELECT stage,{bucket} AS epoch,avg(value) AS value "
              f"FROM ({source_sql}) GROUP BY stage,epoch ORDER BY stage,epoch FORMAT JSONEachRow")
        native=(f"SELECT * FROM prometheusQueryRange(jenkins_ts,'{expr}',"
                f"{start},{end},{step}) FORMAT JSONEachRow")
        sql_oracle=(f"SELECT toUInt64(toUnixTimestamp(toStartOfDay(ts))+86400) AS epoch,"
                    f"stage,avg(duration) AS value FROM jenkins_events "
                    f"WHERE ts>=fromUnixTimestamp({start-step}) AND ts<fromUnixTimestamp({end}) "
                    +({"all":"",
                       "branch_main":"AND branch='main'",
                       "project_00":"AND project='project-00'",
                       "stage10":"AND stage IN ('stage-0000','stage-0001','stage-0002','stage-0003','stage-0004','stage-0005','stage-0006','stage-0007','stage-0008','stage-0009')"}[name])+
                    " GROUP BY stage,epoch FORMAT JSONEachRow")
        _,ora=post("http://127.0.0.1:18191",sql_oracle)
        canonical=normalized_events(ora)
        id_prefix="tslab_metric_range_"+str(os.getenv("GITHUB_RUN_ID","local"))+"_"+name
        # The default v11 series ID has its first tuple component equal
        # to sipHash64(metric_name). Never assume this for custom generators.
        # The final tags JOIN retains the exact metric/label predicate.
        start_ms,end_ms,step_ms=start*1000,end*1000,step*1000
        def late_inner(extra_filter):
            return f"""
                WITH src AS (
                    SELECT s.id AS series_id, arrayJoin(s.samples) AS sample
                    FROM {samples} AS s
                    WHERE s.min_time <= fromUnixTimestamp64Milli({end_ms})
                      AND s.max_time > fromUnixTimestamp64Milli({start_ms-step_ms})
                      {extra_filter}
                ),
                flattened AS (
                    SELECT series_id,
                           toUnixTimestamp64Milli(tupleElement(sample,1)) AS sample_ms,
                           tupleElement(sample,2) AS sample_v
                    FROM src
                ),
                dedup AS (
                    SELECT series_id,sample_ms,
                           argMax(sample_v,tuple(toUInt8(not isNaN(sample_v)),
                             if(isNaN(sample_v),0,sample_v))) AS value
                    FROM flattened
                    WHERE reinterpretAsUInt64(sample_v) != 9218868437227405314
                    GROUP BY series_id,sample_ms
                ),
                indexed AS (
                    SELECT series_id,sample_ms,value,
                           intDiv(sample_ms-{start_ms}+{step_ms}-1,{step_ms}) AS gi
                    FROM dedup
                ),
                per_series AS (
                    SELECT series_id,gi,avg(value) AS series_avg
                    FROM indexed
                    WHERE gi>=0 AND gi<={(end_ms-start_ms)//step_ms}
                      AND sample_ms > {start_ms}+gi*{step_ms}-{step_ms}
                      AND sample_ms <= {start_ms}+gi*{step_ms}
                    GROUP BY series_id,gi
                ),
                per_stage AS (
                    SELECT timeSeriesTagsToGroup(
                         if(mapContains(t.tags,'stage'),
                            [tuple('stage',t.tags['stage'])],
                            CAST([],'Array(Tuple(String,String))'))) AS new_group,
                           p.gi, avg(p.series_avg) AS result_value
                    FROM per_series AS p
                    INNER JOIN {tags} AS t FINAL ON p.series_id=t.id
                    WHERE t.metric_name='{m['metric']}' {ch_where}
                    GROUP BY new_group,p.gi
                )
                SELECT timeSeriesGroupToTags(new_group) AS tags,
                       CAST(timeSeriesGroupArray(
                           fromUnixTimestamp64Milli({start_ms}+gi*{step_ms}),
                           result_value),
                            'Array(Tuple(DateTime64(3),Float64))') AS samples
                FROM per_stage GROUP BY new_group FORMAT JSONEachRow
            """
        metric=m["metric"]
        hash_expr=f"sipHash64('{metric}')"
        uuid_min="toUUID('00000000-0000-0000-0000-000000000000')"
        uuid_max="toUUID('ffffffff-ffff-ffff-ffff-ffffffffffff')"
        raw_late=late_inner("")
        first_component=late_inner(f"AND tupleElement(s.id,1)={hash_expr}")
        id_range=late_inner(
            f"AND s.id >= tuple({hash_expr},{uuid_min}) "
            f"AND s.id <= tuple({hash_expr},{uuid_max})")
        query_by_mode={"ch_promql":native,"two_stage_avg":two_stage,
                       "one_sample_flat_avg":flat,"late_full":raw_late,
                       "late_first":first_component,"late_range":id_range}
        metrics={}
        for mode,query in query_by_mode.items():
            times=[]
            comparison=None
            for iteration in range(a.warmups+a.repeats):
                qid=id_prefix+"_"+mode+"_"+str(iteration)
                ms,raw=post("http://127.0.0.1:18191",query,qid)
                normalized=(normalized_ch(raw) if mode in ("ch_promql","late_full","late_first","late_range") else normalized_events(raw))
                comparison=compare(canonical,normalized,name,mode)
                if iteration>=a.warmups:times.append(ms)
            metrics[mode]={"p50_ms":pct(times,.5),"p95_ms":pct(times,.95),
                           "times_ms":times,"comparison":comparison,"query_id_prefix":id_prefix+"_"+mode}
        vm_times=[]
        for iteration in range(a.warmups+a.repeats):
            ms,payload=query_vm("http://127.0.0.1:8430",expr,start,end,step)
            vm_norm=normalized_vm(payload)
            compare(canonical,vm_norm,name,"vm")
            if iteration>=a.warmups:vm_times.append(ms)
        metrics["vm"]={"p50_ms":pct(vm_times,.5),"p95_ms":pct(vm_times,.95),
                       "times_ms":vm_times,"values_equal_to_events":True}
        result["cases"][name]={"metric_expr":expr,"query_sql":{"two_stage_avg":two_stage,"one_sample_flat_avg":flat,"late_full":raw_late,"late_first":first_component,"late_range":id_range},
                               "cases":metrics,"output_points":len(canonical)}
        print("TS_METRIC_RANGE_QUERY",name,json.dumps({
            "points":len(canonical),"p50_ms":{x:v["p50_ms"] for x,v in metrics.items()},
            "all_results_equal_to_events":True},sort_keys=True),flush=True)
    post("http://127.0.0.1:18191","SYSTEM FLUSH LOGS")
    prefix="tslab_metric_range_"+str(os.getenv("GITHUB_RUN_ID","local"))
    logs=sqlvalue("SELECT query_id,read_rows,read_bytes,memory_usage,query_duration_ms,exception_code "
                  "FROM system.query_log WHERE type='QueryFinish' "
                  f"AND startsWith(query_id,'{prefix}') FORMAT JSONEachRow")
    logrows={row["query_id"]:row for line in logs.splitlines() if line.strip() for row in [json.loads(line)]}
    for name,entry in result["cases"].items():
        for mode in ["ch_promql","two_stage_avg","one_sample_flat_avg","late_full","late_first","late_range"]:
            sample=entry["cases"][mode]
            ids=[sample["query_id_prefix"]+"_"+str(i) for i in range(a.warmups,a.warmups+a.repeats)]
            rows=[logrows[x] for x in ids if x in logrows]
            if len(rows)!=a.repeats:raise AssertionError(("QueryFinish missing",name,mode,len(rows)))
            if any(x["exception_code"] for x in rows):raise AssertionError(("QueryFinish exception",name,mode))
            sample["query_log"]={
                "read_rows_p50":pct([x["read_rows"] for x in rows],.5),
                "read_bytes_p50":pct([x["read_bytes"] for x in rows],.5),
                "memory_p95":pct([x["memory_usage"] for x in rows],.95),
                "peak_memory":max(x["memory_usage"] for x in rows)}
    root.write_text(json.dumps(result,indent=2)+"\n")
    print("TS_METRIC_RANGE_QUERY_DONE",json.dumps({"cases":len(cases),"all_values_equal":True}),flush=True)


if __name__=="__main__":
    main()
