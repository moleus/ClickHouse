#!/usr/bin/env python3
"""Counterexample test: stable dense metrics and churned sparse metrics.

Same 40-day fixture is loaded twice, into default LowCardinality(UUID)
TimeSeries and plain Tuple(UInt64, UUID) TimeSeries. The dense population
has 1000 IDs with 960 hourly samples per ID, sparse has 40000 IDs with 1
sample per ID. Native PromQL A/B/B/A, exact label/time/numeric parity.
"""
import argparse,json,os,pathlib,statistics,sys,time
sys.path.insert(0,".github/ts-lab-perf")
sys.path.insert(0,".github/ts-lab-bench")
from bench_granularity_v11 import sql,inner,optimize_table,querylogs,wait_no_merges
from bench_bucket_cxx_abca import post,normalize_any,assert_same
from bench_filtered_selector_v4_sql import sql_string
CASES={
 "dense_all":'sum by(stage)(avg_over_time(tslab_dense_40d[24h]))',
 "dense_stage":'sum by(stage)(avg_over_time(tslab_dense_40d{stage="stage-0007"}[24h]))',
 "sparse_all":'sum by(stage)(avg_over_time(tslab_sparse_40d[1d]))',
 "sparse_stage":'sum by(stage)(avg_over_time(tslab_sparse_40d{stage="stage-0007"}[1d]))'
}
def save(p,obj):
    f=pathlib.Path(p);f.parent.mkdir(parents=True,exist_ok=True)
    f.write_text(json.dumps(obj,indent=2,sort_keys=True,default=str)+"\n")
def records(q):
    return [json.loads(line) for line in sql(q+" FORMAT JSONEachRow").splitlines() if line.strip()]
def physical(table):
    r={}
    for kind in ("tags","timeranges","samples"):
        n=inner(table,kind).split(chr(96))[1]
        r[kind]={"id_type":records("SELECT type FROM system.columns WHERE database='default' AND name='id' AND table="+sql_string(n))[0]["type"],
                 "parts":records("SELECT count() AS parts,sum(rows) AS rows,"
                        "sum(bytes_on_disk) AS disk_bytes,sum(marks) AS marks "
                        "FROM system.parts WHERE active AND database='default' AND table="+sql_string(n))[0]}
    return r
def ingest(table,fixture):
    batches=0;rows=0;start=time.perf_counter()
    batch=[]
    with pathlib.Path(fixture).open("rb") as source:
        for row in source:
            batch.append(row)
            limit=25 if rows<1000 else 1000
            if len(batch)>=limit:
                post("http://127.0.0.1:18191",
                     "INSERT INTO "+table+" (metric_name,tags,samples) FORMAT JSONEachRow\n"+
                     b"".join(batch).decode())
                rows+=len(batch);batches+=1;batch=[]
        if batch:
            post("http://127.0.0.1:18191",
                 "INSERT INTO "+table+" (metric_name,tags,samples) FORMAT JSONEachRow\n"+
                 b"".join(batch).decode())
            rows+=len(batch);batches+=1
    if rows!=41000:raise AssertionError("Wrong import "+str(rows))
    return {"rows":rows,"batches":batches,"wall_s":round(time.perf_counter()-start,3)}
def native(table,expr,start,end):
    return (f"SELECT * FROM prometheusQueryRange({table},{sql_string(expr)},"
            f"{start},{end},86400) SETTINGS max_threads=4,max_memory_usage=3500000000,"
            "query_profiler_cpu_time_period_ns=1000000,log_queries=1,log_processors_profiles=1 "
            "FORMAT JSONEachRow")
def capture(qids):
    sql("SYSTEM FLUSH LOGS")
    vals="("+",".join(sql_string(q) for q in qids)+")"
    d={}
    for key,q in {
      "processors":"SELECT query_id,name,plan_step_name,sum(elapsed_us) AS work_us,"
                   "sum(input_wait_elapsed_us) AS in_wait_us,"
                   "sum(output_wait_elapsed_us) AS out_wait_us,"
                   "sum(input_rows) AS rows_in,sum(output_rows) AS rows_out "
                   "FROM system.processors_profile_log WHERE query_id IN "+vals+
                   " GROUP BY query_id,name,plan_step_name ORDER BY query_id,work_us DESC LIMIT 60 BY query_id",
      "cpu":"SELECT query_id,multiIf("
            "arrayExists(s -> position(s,'ReverseIndex')>0 OR position(s,'ColumnUnique')>0,symbols),'lc_reverse_index',"
            "arrayExists(s -> position(s,'timeSeriesStoreTags')>0 OR position(s,'ContextTimeSeriesTagsCollector')>0,symbols),'tag_registration',"
            "arrayExists(s -> position(s,'MergeTreeReader')>0,symbols),'read_decode',"
            "arrayExists(s -> position(s,'Aggregator')>0 OR position(s,'AdaptiveBucketMergingSource')>0,symbols),'aggregation',"
            "'other') AS component,count() AS samples "
            "FROM system.trace_log WHERE trace_type='CPU' AND query_id IN "+vals+
            " GROUP BY query_id,component ORDER BY query_id,samples DESC LIMIT 30 BY query_id"
    }.items():
        try:d[key]=records(q)
        except Exception as e:d[key+"_error"]=str(e)[:1600]
    return d
def main():
    ap=argparse.ArgumentParser()
    ap.add_argument("--manifest",required=True)
    ap.add_argument("--fixture",required=True)
    ap.add_argument("--output",required=True)
    a=ap.parse_args()
    m=json.loads(pathlib.Path(a.manifest).read_text())
    assert m["dense_series"]==1000 and m["sparse_series"]==40000
    assert m["dense_samples"]==960000 and m["sparse_samples"]==40000
    report={"manifest":m,"cases":{},"errors":[]}
    save(a.output,report)
    alt="cost_ts_raw"
    try:
        # v11 does not accept the legacy id_type setting. Declare the
        # matching ID type on all three inner targets; retain the original
        # exact hash generator and key/value-pair text index on TAGS.
        sql("CREATE TABLE "+alt+" ENGINE=TimeSeries "
            "SETTINGS recent_samples_ttl_seconds=0 "
            "SAMPLES INNER COLUMNS (id Tuple(UInt64, UUID)) "
            "TAGS INNER COLUMNS ("
            "id Tuple(UInt64, UUID) DEFAULT tuple(sipHash64(metric_name), "
            "reinterpretAsUUID(sipHash128(tags))), "
            "metric_name LowCardinality(String), "
            "tags Map(LowCardinality(String), String), "
            "INDEX tags_idx tags TYPE text(tokenizer = 'keyValuePairs') GRANULARITY 100000000) "
            "TIME RANGES INNER COLUMNS (id Tuple(UInt64, UUID))")
        report["ingest_raw"]=ingest(alt,a.fixture)
        report["optimization"]={t:optimize_table(t) for t in ("cost_ts",alt)}
        wait_no_merges()
        report["physical"]={t:physical(t) for t in ("cost_ts",alt)}
        for t in ("cost_ts",alt):
            tags=inner(t,"tags")
            report.setdefault("digest",{})[t]=records(
                "SELECT count() AS n,sum(sipHash64(toString(id))) AS digest,"
                "countIf(metric_name='tslab_dense_40d') AS dense_ids,"
                "countIf(metric_name='tslab_sparse_40d') AS sparse_ids FROM "+tags)[0]
        if report["digest"]["cost_ts"]!=report["digest"][alt]:
            raise AssertionError("ID digest differs across two time-series types")
        save(a.output,report)
        start,end=int(m["start"]),int(m["end"])
        for case,expr in CASES.items():
            item={"case":case,"expr":expr,"phases":[]}
            report["cases"][case]=item
            ref=None
            for idx,table in enumerate(("cost_ts",alt,alt,"cost_ts")):
                trials=[]
                for k in range(4):
                    qid=f"cpudense_{case}_{idx}_{k}_{os.getenv('GITHUB_RUN_ID','local')}"
                    ms,body=post("http://127.0.0.1:18191",native(table,expr,start,end),qid)
                    points=normalize_any(body)
                    if not points:raise AssertionError("Empty grid "+case)
                    if ref is None:ref=points
                    else:assert_same(ref,points,case,table)
                    trials.append({"qid":qid,"wall_ms":round(ms,3),"points":len(points),"warmup":k==0})
                item["phases"].append({"phase":idx,"table":table,"trials":trials})
                save(a.output,report)
            rows={x["query_id"]:x for x in querylogs("cpudense_"+case+"_") if x.get("type")=="QueryFinish"}
            for phase in item["phases"]:
                for trial in phase["trials"]:
                    log=rows.get(trial["qid"])
                    if not log:raise AssertionError("Missing query_log "+trial["qid"])
                    ev=log.get("ProfileEvents",{})
                    trial.update({"cpu_ms":round(ev.get("OSCPUVirtualTimeMicroseconds",0)/1000,3),
                                  "read_rows":log["read_rows"],"read_bytes":log["read_bytes"],
                                  "memory_bytes":log["memory_usage"],
                                  "compressed_bytes":ev.get("ReadCompressedBytes")})
                timed=phase["trials"][1:]
                phase["p50_wall_ms"]=statistics.median(x["wall_ms"] for x in timed)
                phase["p50_cpu_ms"]=statistics.median(x["cpu_ms"] for x in timed)
            original=[x for x in item["phases"] if x["table"]=="cost_ts"]
            raw=[x for x in item["phases"] if x["table"]==alt]
            base=statistics.mean(x["p50_wall_ms"] for x in original)
            candidate=statistics.mean(x["p50_wall_ms"] for x in raw)
            drift=abs(original[0]["p50_wall_ms"]-original[1]["p50_wall_ms"])/base
            item["summary"]={"default_ms":round(base,3),"raw_id_ms":round(candidate,3),
                             "speedup":round(base/candidate,4),
                             "default_cpu_ms":round(statistics.mean(x["p50_cpu_ms"] for x in original),3),
                             "raw_cpu_ms":round(statistics.mean(x["p50_cpu_ms"] for x in raw),3),
                             "control_drift":round(drift,4),"valid":drift<=0.25,
                             "points":len(ref)}
            item["profiles"]=capture([item["phases"][0]["trials"][1]["qid"],
                                      item["phases"][1]["trials"][1]["qid"]])
            if drift>.25:report["errors"].append({"case":case,"baseline_drift":drift})
            save(a.output,report)
            print("DENSE_UUID_CASE",json.dumps({"case":case,**item["summary"]},separators=(",",":")),flush=True)
        report["passed"]=not report["errors"]
    except Exception as e:
        report["errors"].append({"fatal":str(e)[:1900]});report["passed"]=False
    finally:
        save(a.output,report)
        digest={"passed":report["passed"],"errors":report["errors"],
                "cases":{k:v.get("summary") for k,v in report["cases"].items()},
                "physical":report.get("physical")}
        save(str(pathlib.Path(a.output).with_suffix(".compact.json")),digest)
        print("DENSE_UUID_DONE",json.dumps(digest,separators=(",",":")),flush=True)
    if not report["passed"]:raise SystemExit(1)
if __name__=="__main__":main()
