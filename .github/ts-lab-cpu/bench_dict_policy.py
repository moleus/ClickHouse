#!/usr/bin/env python3
"""LowCardinality UUID read overhead vs dictionary size/part policy.

Each table has same UUID identity, same sorted TAGS source and index granularity.
Only INSERT low_cardinality_max_dictionary_size or single-dictionary changes.
Compare FORMAT Null scans by A/B/B/A CPU, wall, allocated memory and traces.
"""
import argparse,json,os,pathlib,statistics,sys
sys.path.insert(0,".github/ts-lab-perf")
sys.path.insert(0,".github/ts-lab-bench")
from bench_granularity_v11 import sql,inner,querylogs,wait_no_merges
from bench_bucket_cxx_abca import post
from bench_filtered_selector_v4_sql import sql_string

SHA="8f3a36846f80d6be5a8604d142e934399f2687094766c57bf105e43977f52858"
SETTINGS={"baseline":(8192,0),"no_shared":(0,0),"small":(128,0),"large":(65536,0),"single":(8192,1)}
def save(p,v):
    f=pathlib.Path(p);f.parent.mkdir(parents=True,exist_ok=True)
    f.write_text(json.dumps(v,indent=2,default=str)+"\n")
def rows(q):
    return [json.loads(line) for line in sql(q+" FORMAT JSONEachRow").splitlines() if line.strip()]
def inspect(qids):
    out={}; ids="("+",".join(sql_string(q) for q in qids)+")"
    for key,query in {
        "processors":"SELECT query_id,plan_step_name,name,sum(elapsed_us) AS cpu_work_us,"
                     "sum(input_wait_elapsed_us) AS input_wait_us,sum(output_wait_elapsed_us) AS output_wait_us "
                     "FROM system.processors_profile_log WHERE query_id IN "+ids+
                     " GROUP BY query_id,plan_step_name,name ORDER BY query_id,cpu_work_us DESC LIMIT 100",
        "samples":"SELECT query_id,multiIf("
                  "arrayExists(s -> position(s,'ReverseIndex')>0,symbols),'reverse_index',"
                  "arrayExists(s -> position(s,'SerializationLowCardinality')>0,symbols),'lowcard_decoder',"
                  "arrayExists(s -> position(s,'MergeTreeReader')>0,symbols),'reader','other') AS cpu_class,"
                  "count() AS samples FROM system.trace_log WHERE query_id IN "+ids+
                  " AND trace_type='CPU' GROUP BY query_id,cpu_class LIMIT 30",
        "leaves":"SELECT query_id,trace_type,arrayFirst(s -> s!='',symbols) AS leaf,"
                 "count() AS samples,sum(size) AS bytes FROM system.trace_log "
                 "WHERE query_id IN "+ids+" AND trace_type IN ('CPU','Memory') "
                 "GROUP BY query_id,trace_type,leaf ORDER BY query_id,samples DESC LIMIT 100"
    }.items():
        try:out[key]=rows(query)
        except Exception as e:out[key+"_error"]=str(e)[:1500]
    return out
def main():
    p=argparse.ArgumentParser()
    p.add_argument("--manifest",required=True)
    p.add_argument("--output",required=True)
    a=p.parse_args()
    m=json.loads(pathlib.Path(a.manifest).read_text())
    assert m.get("sha256")==SHA and m.get("series")==1330000
    report={"source_sha":SHA,"table_info":{},"cases":{},"errors":[]}
    save(a.output,report)
    try:
        source=inner("churn_ts","tags")
        physical=source.split(chr(96))[1]
        types={x["name"]:x["type"] for x in rows(
            "SELECT name,type FROM system.columns WHERE database='default' AND table="+sql_string(physical))}
        cols=", ".join(k+" "+types[k] for k in ("id","metric_name","tags"))
        for name,(size,single) in SETTINGS.items():
            table="cpu_dict_"+name
            sql(f"CREATE TABLE {table} ({cols}) ENGINE=MergeTree ORDER BY (metric_name,id) "
                "SETTINGS index_granularity=8192")
            sql(f"INSERT INTO {table} (id,metric_name,tags) "
                f"SELECT id,metric_name,tags FROM {source} ORDER BY metric_name,id "
                f"SETTINGS max_threads=1,low_cardinality_max_dictionary_size={size},"
                f"low_cardinality_use_single_dictionary_for_part={single}")
            sql(f"OPTIMIZE TABLE {table} FINAL SETTINGS optimize_throw_if_noop=0")
            digest=rows("SELECT count() AS n,sum(sipHash64(toString(id))) AS h,"
                        "countIf(tags['stage']='stage-0007') AS st FROM "+table)[0]
            parts=rows("SELECT count() AS parts,sum(rows) AS rows,sum(bytes_on_disk) AS bytes,"
                       "sum(marks) AS marks FROM system.parts "
                       "WHERE active AND database='default' AND table="+sql_string(table))[0]
            report["table_info"][name]={"table":table,"max_dict":size,
                                        "single":single,"digest":digest,"parts":parts}
            if digest["n"]!=1330000 or digest["st"]!=35000 or (
                name!="baseline" and digest!=report["table_info"]["baseline"]["digest"]):
                raise AssertionError("Value mismatch: "+name)
            save(a.output,report)
        wait_no_merges()
        for case,columns in (("id_only","id"),("id_tags","id,tags")):
            out={}
            report["cases"][case]=out
            for alternative in list(SETTINGS)[1:]:
                phases=[];out[alternative]={"phases":phases}
                prefix=f"cpudict_{case}_{alternative}_{os.getenv('GITHUB_RUN_ID','local')}_"
                for ph,variant in enumerate(("baseline",alternative,alternative,"baseline")):
                    table=report["table_info"][variant]["table"]
                    stmt=f"SELECT {columns} FROM {table} SETTINGS max_threads=4," \
                         "log_processors_profiles=1,query_profiler_cpu_time_period_ns=1000000 FORMAT Null"
                    arr=[]
                    for trial in range(4):
                        qid=prefix+f"{ph}_{trial}"
                        ms,_=post("http://127.0.0.1:18191",stmt,qid)
                        arr.append({"qid":qid,"ms":round(ms,3),"warmup":trial==0})
                    phases.append({"variant":variant,"phase":ph,"trials":arr})
                    save(a.output,report)
                logs={x["query_id"]:x for x in querylogs(prefix) if x.get("type")=="QueryFinish"}
                for ph in phases:
                    for t in ph["trials"]:
                        r=logs.get(t["qid"])
                        if r is None:raise AssertionError("Missing querylog "+t["qid"])
                        ev=r.get("ProfileEvents",{})
                        t.update({"cpu_ms":ev.get("OSCPUVirtualTimeMicroseconds",0)/1000,
                                  "memory_bytes":r["memory_usage"],"read_rows":r["read_rows"],
                                  "read_bytes":r["read_bytes"],"marks":ev.get("SelectedMarks")})
                    ph["wall_ms"]=statistics.median(t["ms"] for t in ph["trials"][1:])
                    ph["cpu_ms"]=statistics.median(t["cpu_ms"] for t in ph["trials"][1:])
                b=[x for x in phases if x["variant"]=="baseline"]
                v=[x for x in phases if x["variant"]==alternative]
                bm=statistics.mean(x["wall_ms"] for x in b)
                vm=statistics.mean(x["wall_ms"] for x in v)
                drift=abs(b[0]["wall_ms"]-b[1]["wall_ms"])/bm
                summary={"baseline_ms":round(bm,3),"variant_ms":round(vm,3),
                         "wall_speedup":round(bm/vm,4),
                         "baseline_cpu_ms":round(statistics.mean(x["cpu_ms"] for x in b),3),
                         "variant_cpu_ms":round(statistics.mean(x["cpu_ms"] for x in v),3),
                         "baseline_drift":round(drift,4),"valid":drift<=.25}
                out[alternative]["summary"]=summary
                out[alternative]["profile"]=inspect([phases[0]["trials"][1]["qid"],phases[1]["trials"][1]["qid"]])
                if drift>.25:report["errors"].append({"case":case,"variant":alternative,"drift":drift})
                print("CPUDICT_CASE",json.dumps({"case":case,"variant":alternative,**summary}),flush=True)
                save(a.output,report)
        report["passed"]=not report["errors"]
    except Exception as e:
        report["errors"].append({"fatal":str(e)[:1800]});report["passed"]=False
    finally:
        save(a.output,report)
        compact={"passed":report.get("passed"),"table_info":report["table_info"],
                 "cases":{k:{name:item.get("summary") for name,item in c.items()}
                          for k,c in report["cases"].items()},"errors":report["errors"]}
        save(str(pathlib.Path(a.output).with_suffix(".compact.json")),compact)
        print("CPUDICT_DONE",json.dumps(compact,separators=(",",":")),flush=True)
    if not report["passed"]:raise SystemExit(1)
if __name__=="__main__":main()
