#!/usr/bin/env python3
"""Proof-first sparse SAMPLES granularity study, no ClickHouse recompilation.

Compares full v11 TimeSeries with g=16/64/256 on the EXACT same 1.33m source.
No background merges during measured queries; every candidate verifies output.
"""
import argparse,hashlib,json,os,pathlib,random,statistics,time,urllib.request,sys
sys.path.insert(0,".github/ts-lab-bench")
from bench_bucket_cxx_abca import post,normalize_any,assert_same,pct
from bench_filtered_selector_v4_sql import sql_string
from bench_stage_cpp_vs_original_vm_95k import server_restart

HOST="http://127.0.0.1:18191"
NAME="jenkins_ci_95k_daily"
DAY=86400
CANONICAL_SHA="8f3a36846f80d6be5a8604d142e934399f2687094766c57bf105e43977f52858"

def sql(stmt,qid=None):
    _,body=post(HOST,stmt,qid)
    return body.decode(errors="replace")

def save(path,obj):
    pathlib.Path(path).write_text(json.dumps(obj,indent=2)+"\n")

def streamed_insert(table,fixture):
    batches=0;input_rows=0;input_bytes=0
    with open(fixture,"rb") as source:
        while True:
            chunk=[source.readline() for _ in range(2000)]
            chunk=[x for x in chunk if x]
            if not chunk:break
            request=urllib.request.Request(
                HOST+"/?wait_end_of_query=1&query="+
                __import__("urllib.parse",fromlist=["quote"]).quote(
                    "INSERT INTO "+table+" (metric_name,tags,samples) FORMAT JSONEachRow"),
                data=b"".join(chunk),method="POST")
            with urllib.request.urlopen(request,timeout=240) as resp:
                resp.read()
            batches+=1;input_rows+=len(chunk);input_bytes+=sum(map(len,chunk))
            if batches%100==0:
                print("GRANULARITY_INGEST_PROGRESS",json.dumps(
                    {"table":table,"batches":batches,"rows":input_rows}),flush=True)
    return {"batches":batches,"rows":input_rows,"bytes":input_bytes}

def uuid_for(table):
    value=sql("SELECT toString(uuid) FROM system.tables WHERE database='default' AND name="+sql_string(table)+" FORMAT TSV").strip()
    if len(value)!=36:raise RuntimeError("Bad UUID for "+table+": "+value)
    return value

def inner(table,kind):
    uuid=uuid_for(table)
    return "default."+chr(96)+".inner_id."+kind+"."+uuid+chr(96)

def stats(table):
    names=[inner(table,k) for k in ("samples","tags","timeranges")]
    out={}
    for kind,qualified in zip(("samples","tags","timeranges"),names):
        raw=sql(("SELECT count() AS parts,sum(rows) AS rows,sum(marks) AS marks,"
                 "sum(bytes_on_disk) AS bytes FROM system.parts WHERE active "
                 "AND database='default' AND table="+sql_string(qualified.split(chr(96))[1])+" FORMAT JSONEachRow"))
        out[kind]=json.loads(raw.strip())
        out[kind]["table"]=qualified
        if kind=="samples":
            out[kind]["create_sql"]=sql("SHOW CREATE TABLE "+qualified+" FORMAT TSVRaw")[:12000]
    return out

def wait_no_merges():
    sql("SYSTEM STOP MERGES")
    for n in range(30):
        raw=sql("SELECT count() FROM system.merges WHERE database='default' FORMAT TSV").strip()
        if raw=="0":return
        time.sleep(2)
    raise RuntimeError("Active data merges, cannot compare stable part layout")

def count_table(table):
    c=int(sql("SELECT count() FROM "+table+" WHERE metric_name="+sql_string(NAME)+" FORMAT TSV").strip())
    if c!=1330000:raise AssertionError((table,"count",c))
    n=int(sql("SELECT count() FROM "+table+" WHERE metric_name="+sql_string(NAME)+
              " AND tags['stage']='stage-0007' FORMAT TSV").strip())
    if n!=35000:raise AssertionError((table,"stage count",n))
    return {"series":c,"stage":n}

def optimize_table(table):
    # All three targets must use stable physical parts for the index comparison.
    optimized={}
    for k in ("samples","tags","timeranges"):
        q="OPTIMIZE TABLE "+inner(table,k)+" FINAL SETTINGS optimize_throw_if_noop=0"
        start=time.monotonic()
        try:sql(q);optimized[k]=round(time.monotonic()-start,3)
        except Exception as exc:
            optimized[k]={"error":str(exc)[:500]}
            print("GRANULARITY_OPTIMIZE_WARNING",table,k,str(exc)[:280],flush=True)
    return optimized

def querylogs(prefix):
    sql("SYSTEM FLUSH LOGS")
    result=sql(("SELECT query_id,type,query_duration_ms,memory_usage,read_rows,read_bytes,ProfileEvents "
                "FROM system.query_log WHERE startsWith(query_id,"+sql_string(prefix)+
                ") ORDER BY event_time_microseconds FORMAT JSONEachRow"))
    return [json.loads(x) for x in result.splitlines() if x.strip()]

def measure(table,case,start,end,run,phase,reference):
    t_start=end-13*DAY
    if case=="whole":
        promql="avg by(stage)(avg_over_time("+NAME+"[1d]))"
        expected=532
    else:
        promql="avg by(stage)(avg_over_time("+NAME+'{stage="stage-0007"}[1d]))'
        expected=14
    query=(f"SELECT * FROM prometheusQueryRange({table},{sql_string(promql)},"
           f"{t_start},{end},{DAY}) SETTINGS max_threads=4,"
           "promql_enable_native_one_point_lab=1 FORMAT JSONEachRow")
    data=[]
    for i in range(6):
        qid=f"gran_{run}_{case}_{phase}_{i}"
        wall,body=post(HOST,query,qid)
        got=normalize_any(body)
        if len(got)!=expected:raise AssertionError(("bad point count",table,case,len(got),expected))
        if reference is None:reference=got
        else:assert_same(reference,got,case,phase)
        data.append({"query_id":qid,"wall_ms":round(wall,3),"warmup":i<2})
    logs=querylogs(f"gran_{run}_{case}_{phase}_")
    by={z["query_id"]:z for z in logs if z.get("type")=="QueryFinish"}
    for item in data:
        if item["query_id"] in by:
            raw=by[item["query_id"]]
            item.update({"read_rows":raw.get("read_rows"),"read_bytes":raw.get("read_bytes"),
                "query_memory":raw.get("memory_usage"),
                "cpu_us":raw.get("ProfileEvents",{}).get("OSCPUVirtualTimeMicroseconds"),
                "compressed_read_bytes":raw.get("ProfileEvents",{}).get("ReadCompressedBytes")})
    measured=data[2:]
    return reference,{"table":table,"phase":phase,"case":case,
                      "timings":data,"p50_ms":pct([x["wall_ms"] for x in measured],.5),
                      "p95_ms":pct([x["wall_ms"] for x in measured],.95),
                      "read_rows_p50":pct([x.get("read_rows",0) for x in measured],.5),
                      "read_bytes_p50":pct([x.get("read_bytes",0) for x in measured],.5),
                      "cpu_us_p50":pct([x.get("cpu_us",0) for x in measured],.5)}

def probe_samples(table,run):
    target=inner(table,"samples")
    tags=inner(table,"tags")
    query=(f"SELECT sum(length(samples)) AS n FROM {target} "
           f"WHERE id IN (SELECT id FROM {tags} "
           f"WHERE metric_name={sql_string(NAME)} AND tags['stage']='stage-0007') "
           "SETTINGS max_threads=4,optimize_trivial_count_query=0 FORMAT JSONEachRow")
    _,raw=post(HOST,query,f"gran_{run}_physical_{table}")
    parsed=json.loads(raw.decode())
    if int(parsed["n"])!=35000:raise AssertionError(("samples raw mismatch",table,parsed))
    plan=sql("EXPLAIN PLAN indexes=1 "+query.replace(" FORMAT JSONEachRow",""))[:120000]
    logs=querylogs(f"gran_{run}_physical_{table}")
    return {"sum_samples":int(parsed["n"]),"explain":plan,
            "query_log":[x for x in logs if x.get("type")=="QueryFinish"]}

def main():
    p=argparse.ArgumentParser()
    for item in ("manifest","fixture","projected-bin","restart-helper","output-dir"):
        p.add_argument("--"+item,required=True)
    args=p.parse_args()
    m=json.loads(pathlib.Path(args.manifest).read_text())
    assert m["series"]==1330000 and m["sha256"]==CANONICAL_SHA
    out=pathlib.Path(args.output_dir);out.mkdir(parents=True,exist_ok=True)
    run=os.getenv("GITHUB_RUN_ID","local")
    tables={"g256":"churn_ts","g16":"churn_g16","g64":"churn_g64"}
    report={"run":run,"fixture":m,"source_hash":CANONICAL_SHA,
            "settings":{"recent_samples_ttl_seconds":0,"max_threads":4},
            "tables":{},"data":[],"errors":[],"case_order":{},"pass":False}
    save(out/"report.json",report)
    for label in ("g16","g64"):
        table=tables[label]
        g=int(label[1:])
        try:
            sql(f"CREATE TABLE {table} ENGINE=TimeSeries SETTINGS "
                f"recent_samples_ttl_seconds=0,samples_index_granularity={g}")
            info=streamed_insert(table,args.fixture)
            report["tables"][label]={"import":info,"counts":count_table(table),
                                     "optimization":optimize_table(table)}
            save(out/"report.json",report)
        except Exception as exc:
            report["errors"].append({"phase":"import","table":table,"error":str(exc)[:1900]})
            save(out/"report.json",report)
            raise
    report["tables"]["g256"]={"counts":count_table("churn_ts"),
                                 "optimization":optimize_table("churn_ts")}
    wait_no_merges()
    for k,table in tables.items():
        report["tables"][k]["physical"]=stats(table)
    save(out/"report.json",report)
    # All three table definitions/physical layouts are fixed before the timed phases.
    for case in ("stage","whole"):
        treatments=["g16","g64"]
        random.Random(88199+(0 if case=="stage" else 1)).shuffle(treatments)
        order=["g256",*treatments,"g256"]
        report["case_order"][case]=order
        reference=None
        for seq,label in enumerate(order):
            table=tables[label]
            phase=f"{label}_{seq}"
            try:
                server_restart(args.restart_helper,args.projected_bin,f"granularity_{case}_{phase}")
                wait_no_merges()
                reference,rec=measure(table,case,m["base"],m["end"],run,phase,reference)
                rec["source"]={"kind":"native v4 stage-projected selector","table":table}
                rec["physical"]=stats(table)
                report["data"].append(rec)
                save(out/"report.json",report)
                print("GRANULARITY_CASE",json.dumps({"case":case,"phase":phase,
                      "p50_ms":rec["p50_ms"],"read_rows":rec["read_rows_p50"],
                      "read_bytes":rec["read_bytes_p50"]},separators=(",",":")),flush=True)
            except Exception as exc:
                report["errors"].append({"case":case,"phase":phase,"error":str(exc)[:2500]})
                save(out/"report.json",report)
                raise
    for label,table in tables.items():
        try:
            probe=probe_samples(table,run)
            (out/("physical-"+label+".json")).write_text(json.dumps(probe,indent=2)+"\n")
            report["tables"][label]["physical_probe"]={"sum_samples":probe["sum_samples"],
               "query_log":probe["query_log"]}
        except Exception as exc:
            report["errors"].append({"table":table,"phase":"physical_probe","error":str(exc)[:1200]})
    report["pass"]=not report["errors"] and len(report["data"])==8
    save(out/"report.json",report)
    print("GRANULARITY_DONE",json.dumps({"pass":report["pass"],
        "cases":len(report["data"]),"errors":len(report["errors"])},separators=(",",":")),flush=True)
    if not report["pass"]:raise SystemExit(1)

if __name__=="__main__":main()
