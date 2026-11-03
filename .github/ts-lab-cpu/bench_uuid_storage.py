#!/usr/bin/env python3
"""Test whether LowCardinality(UUID) harms high-cardinality TAGS read CPU.

Compare same logical TimeSeries TAGS dataset in two standalone MergeTree tables,
identical ORDER BY/parts, with only UUID child storage different:
 Tuple(UInt64, LowCardinality(UUID)) vs Tuple(UInt64, UUID).
Report physical parts/disk size, repeated A/B/B/A CPU/wall/memory, full CPU
trace taxonomy. FORMAT Null discards network serialization deliberately.
"""
import argparse,json,os,pathlib,statistics,sys
sys.path.insert(0,".github/ts-lab-perf")
sys.path.insert(0,".github/ts-lab-bench")
from bench_granularity_v11 import inner,sql,querylogs,wait_no_merges
from bench_bucket_cxx_abca import post
from bench_filtered_selector_v4_sql import sql_string
FIXTURE="8f3a36846f80d6be5a8604d142e934399f2687094766c57bf105e43977f52858"
KIND={"scan_id":"id","scan_all":"id,metric_name,tags",
      "stage_id":"id","stage_all":"id,tags"}
def save(p,obj):
    f=pathlib.Path(p);f.parent.mkdir(parents=True,exist_ok=True)
    f.write_text(json.dumps(obj,indent=2,sort_keys=True,default=str)+"\n")
def select(q):
    return [json.loads(s) for s in sql(q+" FORMAT JSONEachRow").splitlines() if s.strip()]
def attempt(q,obj,key):
    try:obj[key]=select(q)
    except Exception as e:obj[key+"_error"]=str(e)[:1800]
def profile(qids):
    sql("SYSTEM FLUSH LOGS")
    result={}
    quoted="("+",".join(sql_string(q) for q in qids)+")"
    attempt("SELECT query_id,plan_step_name,name,count() AS instances,"
            "sum(elapsed_us) AS work_us,sum(input_wait_elapsed_us) AS in_wait_us,"
            "sum(output_wait_elapsed_us) AS out_wait_us,sum(output_rows) AS output_rows "
            "FROM system.processors_profile_log WHERE query_id IN "+quoted+
            " GROUP BY query_id,plan_step_name,name ORDER BY query_id,work_us DESC LIMIT 240",result,"processors")
    attempt("SELECT query_id,trace_type,arrayFirst(s -> s != '',symbols) AS leaf,"
            "count() AS samples,sum(size) AS bytes FROM system.trace_log "
            "WHERE query_id IN "+quoted+" AND trace_type IN ('CPU','Memory') "
            "GROUP BY query_id,trace_type,leaf ORDER BY query_id,samples DESC LIMIT 300",result,"leaf_stacks")
    attempt("SELECT query_id,"
            "multiIf(arrayExists(s -> position(s,'ReverseIndex')>0,symbols),'reverse_index',"
            "arrayExists(s -> position(s,'ColumnUnique')>0,symbols),'column_unique',"
            "arrayExists(s -> position(s,'SerializationLowCardinality')>0,symbols),'lowcard_decode',"
            "arrayExists(s -> position(s,'MergeTreeReader')>0,symbols),'mergetree_read',"
            "'other') AS component,count() AS samples "
            "FROM system.trace_log WHERE query_id IN "+quoted+" AND trace_type='CPU'"
            " GROUP BY query_id,component ORDER BY query_id,samples DESC LIMIT 50",result,"cpu_components")
    return result
def query(table,case):
    cond=(" WHERE tags['stage']='stage-0007'" if case.startswith("stage") else "")
    return f"SELECT {KIND[case]} FROM {table}{cond} SETTINGS max_threads=4,max_memory_usage=3500000000,query_profiler_cpu_time_period_ns=1000000,query_profiler_real_time_period_ns=1000000 FORMAT Null"
def setup(table,coltypes):
    cols=",".join(name+" "+coltypes[name] for name in ("id","metric_name","tags"))
    sql(f"CREATE TABLE {table} ({cols}) ENGINE=MergeTree ORDER BY (metric_name,id) "
        "SETTINGS index_granularity=8192")
def main():
    ap=argparse.ArgumentParser()
    ap.add_argument("--manifest",required=True)
    ap.add_argument("--output",required=True)
    args=ap.parse_args()
    manifest=json.loads(pathlib.Path(args.manifest).read_text())
    assert manifest["sha256"]==FIXTURE and manifest["series"]==1330000
    report={"fixture":FIXTURE,"tables":{},"cases":{},"errors":[]}
    save(args.output,report)
    try:
        source=inner("churn_ts","tags")
        source_name=source.split(chr(96))[1]
        rows=select("SELECT name,type FROM system.columns WHERE database='default' AND table="+sql_string(source_name))
        types={x["name"]:x["type"] for x in rows}
        if "LowCardinality(UUID)" not in types["id"]:
            raise AssertionError("Unexpected original ID schema: "+str(types["id"]))
        target_a="tscpu_tags_lc"
        target_b="tscpu_tags_uuid"
        raw_types={**types,"id":types["id"].replace("LowCardinality(UUID)","UUID")}
        setup(target_a,types)
        setup(target_b,raw_types)
        sql(f"INSERT INTO {target_a} (id,metric_name,tags) SELECT id,metric_name,tags FROM {source}")
        sql(f"INSERT INTO {target_b} (id,metric_name,tags) SELECT CAST(id AS {sql_string(raw_types['id'])}),metric_name,tags FROM {source}")
        for table in (target_a,target_b):
            sql(f"OPTIMIZE TABLE {table} FINAL SETTINGS optimize_throw_if_noop=0")
            actual=int(sql(f"SELECT count() FROM {table} FORMAT TSV").strip())
            if actual!=1330000:raise AssertionError(f"Short copy {table}: {actual}")
            digest=select(f"SELECT count() AS n, sum(sipHash64(toString(id))) AS digest,"
                          f"countIf(tags['stage']='stage-0007') AS stage_count FROM {table}")[0]
            if digest["stage_count"]!=35000:raise AssertionError("Incorrect stage source")
            stats=select("SELECT count() AS parts,sum(rows) AS rows,sum(bytes_on_disk) AS bytes,"
                         "sum(marks) AS marks FROM system.parts WHERE active AND database='default' AND table="+sql_string(table))[0]
            report["tables"][table]={"id_type":(types if table==target_a else raw_types)["id"],
                                    "digest":digest,"parts":stats}
        if report["tables"][target_a]["digest"]!=report["tables"][target_b]["digest"]:
            raise AssertionError("Logical values differ between LC and plain UUID")
        wait_no_merges()
        save(args.output,report)
        for case in KIND:
            phases=[]
            report["cases"][case]={"phases":phases}
            prefix=f"cpuid_{case}_{os.getenv('GITHUB_RUN_ID','local')}_"
            for phase,table in enumerate((target_a,target_b,target_b,target_a)):
                trials=[]
                for repeat in range(4):
                    qid=prefix+f"{phase}_{repeat}"
                    ms,_=post("http://127.0.0.1:18191",query(table,case),qid)
                    trials.append({"qid":qid,"client_ms":round(ms,3),
                                   "warmup":repeat==0})
                phases.append({"phase":phase,"table":table,"trials":trials})
                save(args.output,report)
            logs={x["query_id"]:x for x in querylogs(prefix) if x.get("type")=="QueryFinish"}
            for phase in phases:
                for trial in phase["trials"]:
                    log=logs.get(trial["qid"])
                    if log is None:raise AssertionError("Missing QueryFinish "+trial["qid"])
                    ev=log.get("ProfileEvents",{})
                    trial.update({"cpu_ms":round(ev.get("OSCPUVirtualTimeMicroseconds",0)/1000,3),
                                  "query_wall_ms":log["query_duration_ms"],
                                  "memory_bytes":log["memory_usage"],"read_rows":log["read_rows"],
                                  "read_bytes":log["read_bytes"],
                                  "compressed_bytes":ev.get("ReadCompressedBytes"),
                                  "selected_marks":ev.get("SelectedMarks")})
                sample=phase["trials"][1:]
                phase["median_wall_ms"]=statistics.median(x["client_ms"] for x in sample)
                phase["median_cpu_ms"]=statistics.median(x["cpu_ms"] for x in sample)
            a=[x for x in phases if x["table"]==target_a]
            b=[x for x in phases if x["table"]==target_b]
            aa=statistics.mean(x["median_wall_ms"] for x in a)
            bb=statistics.mean(x["median_wall_ms"] for x in b)
            drift=abs(a[0]["median_wall_ms"]-a[1]["median_wall_ms"])/aa
            summary={"lc_ms":round(aa,3),"uuid_ms":round(bb,3),
                     "lc_over_uuid_speedup":round(aa/bb,4),
                     "lc_cpu_ms":round(statistics.mean(x["median_cpu_ms"] for x in a),3),
                     "uuid_cpu_ms":round(statistics.mean(x["median_cpu_ms"] for x in b),3),
                     "baseline_drift":round(drift,4),"valid":drift<=0.25}
            report["cases"][case]["summary"]=summary
            report["cases"][case]["profiles"]=profile([phases[0]["trials"][1]["qid"],
                                                          phases[1]["trials"][1]["qid"]])
            if drift>0.25:report["errors"].append({"case":case,"drift":drift})
            print("CPUID_CASE",json.dumps({"case":case,**summary},separators=(",",":")),flush=True)
            save(args.output,report)
        report["passed"]=not report["errors"]
    except Exception as e:
        report["errors"].append({"fatal":str(e)[:2000]})
        report["passed"]=False
    finally:
        save(args.output,report)
        compact={"passed":report.get("passed"),"errors":report["errors"],
                 "tables":report["tables"],
                 "cases":{k:v.get("summary") for k,v in report["cases"].items()}}
        save(str(pathlib.Path(args.output).with_suffix(".compact.json")),compact)
        print("CPUID_DONE",json.dumps(compact,separators=(",",":")),flush=True)
    if not report["passed"]:raise SystemExit(1)
if __name__=="__main__":main()
