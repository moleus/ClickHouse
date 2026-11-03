#!/usr/bin/env python3
"""TimeSeries v11 INSERT/merge CPU: default LC UUID versus raw UUID.

Fixed 100k-record prefix of canonical 1.33m synthetic Jenkins data. Each
of four independent tables receives byte-for-byte identical JSON records,
in A/B/B/A order. Stop merges during batch INSERT so background compaction
cannot distort query CPU. Then run OPTIMIZE on each inner table and collect
part counts, bytes, aggregate CPU and peak query memory. Not steady ingestion.
"""
import argparse,hashlib,json,os,pathlib,statistics,sys,time
sys.path[:0]=[".github/ts-lab-perf",".github/ts-lab-bench"]
from bench_granularity_v11 import sql,inner,querylogs
from bench_bucket_cxx_abca import post
from bench_filtered_selector_v4_sql import sql_string
SHA="8f3a36846f80d6be5a8604d142e934399f2687094766c57bf105e43977f52858"
def save(p,r):
    q=pathlib.Path(p);q.parent.mkdir(parents=True,exist_ok=True)
    q.write_text(json.dumps(r,indent=2,default=str)+"\n")
def records(q):
    return [json.loads(s) for s in sql(q+" FORMAT JSONEachRow").splitlines() if s.strip()]
def make_table(name,plain):
    if plain:
        sql("CREATE TABLE "+name+" ENGINE=TimeSeries SETTINGS recent_samples_ttl_seconds=0 "
            "SAMPLES INNER COLUMNS (id Tuple(UInt64,UUID)) "
            "TAGS INNER COLUMNS (id Tuple(UInt64,UUID) "
            "DEFAULT tuple(sipHash64(metric_name),reinterpretAsUUID(sipHash128(tags))),"
            "metric_name LowCardinality(String),tags Map(LowCardinality(String),String),"
            "INDEX tags_idx tags TYPE text(tokenizer = 'keyValuePairs') GRANULARITY 100000000) "
            "TIME RANGES INNER COLUMNS (id Tuple(UInt64,UUID))")
    else:
        sql("CREATE TABLE "+name+" ENGINE=TimeSeries SETTINGS recent_samples_ttl_seconds=0")
def metrics(name):
    r={}
    for k in ("tags","timeranges","samples"):
        n=inner(name,k).split(chr(96))[1]
        r[k]=records("SELECT count() AS parts,sum(rows) AS rows,sum(bytes_on_disk) AS bytes "
                     "FROM system.parts WHERE active AND database='default' AND table="+sql_string(n))[0]
    return r
def main():
    p=argparse.ArgumentParser()
    for k in ("fixture","manifest","output"):p.add_argument("--"+k,required=True)
    p.add_argument("--records",type=int,default=100000)
    a=p.parse_args()
    m=json.loads(pathlib.Path(a.manifest).read_text())
    assert m["sha256"]==SHA and m["series"]==1330000 and a.records in (30000,100000)
    chunks=[];buffer=[];digest=hashlib.sha256()
    with open(a.fixture,"rb") as source:
        for n in range(a.records):
            line=source.readline()
            if not line:raise AssertionError("Insufficient source records")
            digest.update(line);buffer.append(line)
            if len(buffer)==2000:
                chunks.append(b"".join(buffer).decode());buffer=[]
    if buffer:chunks.append(b"".join(buffer).decode())
    report={"canonical_sha":SHA,"prefix_sha":digest.hexdigest(),"records":a.records,
            "batch_size":2000,"phases":[],"errors":[]}
    save(a.output,report)
    try:
        sql("SYSTEM STOP MERGES")
        for idx,kind in enumerate(("lc","raw","raw","lc")):
            name="tscpu_insert_"+str(idx)+"_"+kind
            make_table(name,kind=="raw")
            phase={"table":name,"kind":kind,"phase":idx,"insert_traces":[]}
            prefix=f"tscpu_insert_{idx}_{os.getenv('GITHUB_RUN_ID','local')}_"
            report["phases"].append(phase)
            start=time.perf_counter()
            for n,data in enumerate(chunks):
                qid=prefix+str(n)
                ms,_=post("http://127.0.0.1:18191",
                           "INSERT INTO "+name+" (metric_name,tags,samples) FORMAT JSONEachRow\n"+data,qid)
                phase["insert_traces"].append({"qid":qid,"wall_ms":round(ms,3)})
            phase["insert_total_wall_s"]=round(time.perf_counter()-start,3)
            entries={z["query_id"]:z for z in querylogs(prefix) if z.get("type")=="QueryFinish"}
            if len(entries)!=len(chunks):raise AssertionError("Missing INSERT query logs "+str(len(entries)))
            for x in phase["insert_traces"]:
                q=entries[x["qid"]];ev=q.get("ProfileEvents",{})
                x.update({"cpu_ms":ev.get("OSCPUVirtualTimeMicroseconds",0)/1000,
                          "peak_query_memory":q["memory_usage"],"written_rows":q.get("written_rows"),
                          "read_rows":q.get("read_rows"),"read_bytes":q.get("read_bytes")})
            phase["insert_total_cpu_ms"]=round(sum(x["cpu_ms"] for x in phase["insert_traces"]),3)
            phase["insert_peak_query_memory"]=max(x["peak_query_memory"] for x in phase["insert_traces"])
            count=int(sql("SELECT count() FROM "+name+" FORMAT TSV").strip())
            if count!=a.records:raise AssertionError("Missing INSERT rows "+str(count))
            phase["before_merge"]=metrics(name)
            phase["id_digest"]=records("SELECT count() AS n,sum(sipHash64(toString(id))) AS h "
                                       "FROM "+inner(name,"tags"))[0]
            if idx and phase["id_digest"]!=report["phases"][0]["id_digest"]:
                raise AssertionError("ID mismatch across tables")
            print("INSERT_CPU_PHASE",json.dumps({"phase":idx,"kind":kind,
                  "wall_s":phase["insert_total_wall_s"],
                  "cpu_ms":phase["insert_total_cpu_ms"],
                  "peak_memory":phase["insert_peak_query_memory"]}),flush=True)
            save(a.output,report)
        sql("SYSTEM START MERGES")
        for phase in report["phases"]:
            optimize={}
            name=phase["table"]
            for layer in ("tags","timeranges","samples"):
                start=time.perf_counter()
                qid=f"cpu_optimize_{layer}_{phase['phase']}_{os.getenv('GITHUB_RUN_ID','local')}"
                sql("OPTIMIZE TABLE "+inner(name,layer)+" FINAL SETTINGS optimize_throw_if_noop=0",qid)
                optimize[layer]={"qid":qid,"wall_ms":round((time.perf_counter()-start)*1000,3)}
            entries={x["query_id"]:x for x in querylogs("cpu_optimize_") if x.get("type")=="QueryFinish"}
            for entry in optimize.values():
                row=entries.get(entry["qid"],{})
                entry["cpu_ms"]=row.get("ProfileEvents",{}).get("OSCPUVirtualTimeMicroseconds",0)/1000
                entry["peak_memory"]=row.get("memory_usage",0)
            phase["optimize"]=optimize
            phase["after_merge"]=metrics(name)
        baseline=[x for x in report["phases"] if x["kind"]=="lc"]
        candidate=[x for x in report["phases"] if x["kind"]=="raw"]
        b=statistics.mean(x["insert_total_wall_s"] for x in baseline)
        v=statistics.mean(x["insert_total_wall_s"] for x in candidate)
        drift=abs(baseline[0]["insert_total_wall_s"]-baseline[1]["insert_total_wall_s"])/b
        report["summary"]={"lc_insert_wall_s":round(b,3),"raw_insert_wall_s":round(v,3),
           "insert_speedup":round(b/v,3),
           "lc_insert_cpu_ms":round(statistics.mean(x["insert_total_cpu_ms"] for x in baseline),3),
           "raw_insert_cpu_ms":round(statistics.mean(x["insert_total_cpu_ms"] for x in candidate),3),
           "control_drift":round(drift,4),"valid":drift<=.25}
        if drift>.25:report["errors"].append({"control_drift":drift})
        report["passed"]=not report["errors"]
    except Exception as e:
        report["errors"].append({"fatal":str(e)[:2000]});report["passed"]=False
    finally:
        save(a.output,report)
        result={"passed":report.get("passed"),"summary":report.get("summary"),
                "errors":report["errors"],"prefix_sha":report["prefix_sha"],
                "phases":[{"kind":p["kind"],"phase":p["phase"],
                           "total_cpu_ms":p.get("insert_total_cpu_ms"),
                           "merge":p.get("optimize"),
                           "size":p.get("after_merge")}
                          for p in report["phases"]]}
        save(str(pathlib.Path(a.output).with_suffix(".compact.json")),result)
        print("INSERT_CPU_DONE",json.dumps(result,separators=(",",":")),flush=True)
    if not report["passed"]:raise SystemExit(1)
if __name__=="__main__":
    main()
