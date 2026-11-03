#!/usr/bin/env python3
"""LowCardinality(UUID) on-disk codec experiment with forced WIDE parts.

Same 200k sorted series from fixed canonical 1.33m source. Force two Wide
parts and change only low_cardinality_max_dictionary_size on each INSERT.
Hash actual .bin files before and after OPTIMIZE FINAL. Time ID reads on the
unchanged two-part layout. Reject no-op experimental interventions.
"""
import argparse,hashlib,json,os,pathlib,statistics,sys
sys.path[:0]=[".github/ts-lab-perf",".github/ts-lab-bench"]
from bench_granularity_v11 import sql,inner,querylogs
from bench_bucket_cxx_abca import post
from bench_filtered_selector_v4_sql import sql_string
SOURCE="8f3a36846f80d6be5a8604d142e934399f268709476e57bf105e43977f52858"
# Use the exact canonical frozen manifest SHA from the experiment.
SOURCE="8f3a36846f80d6be5a8604d142e934399f2687094766c57bf105e43977f52858"
SIZES={"none":0,"tiny":128,"default":8192,"large":65536}
def save(p,obj):
    x=pathlib.Path(p);x.parent.mkdir(parents=True,exist_ok=True)
    x.write_text(json.dumps(obj,indent=2,sort_keys=True,default=str)+"\n")
def rows(q):
    return [json.loads(x) for x in sql(q+" FORMAT JSONEachRow").splitlines() if x.strip()]
def parts(table):
    q=("SELECT name,path,part_type,rows,bytes_on_disk FROM system.parts "
       "WHERE active AND database='default' AND table="+sql_string(table)+
       " ORDER BY name FORMAT JSONEachRow")
    return [json.loads(s) for s in sql(q).splitlines() if s.strip()]
def hash_files(entries):
    h=hashlib.sha256();manifest=[]
    for e in entries:
        root=pathlib.Path(e["path"])
        files=sorted(list(root.glob("*.bin"))+list(root.glob("*.mrk*")))
        if not files:raise AssertionError("Missing Wide part bin and mark files "+str(root))
        for f in files:
            digest=hashlib.sha256(f.read_bytes()).hexdigest()
            h.update(f.name.encode());h.update(bytes.fromhex(digest))
            manifest.append({"part":e["name"],"file":f.name,
                             "sha256":digest,"size":f.stat().st_size})
    return {"part_type":[x["part_type"] for x in entries],
            "parts":entries,"content_sha256":h.hexdigest(),"files":manifest}
def measure(table,prefix):
    stmt=f"SELECT id FROM {table} SETTINGS max_threads=4,"
    stmt+="query_profiler_cpu_time_period_ns=1000000 FORMAT Null"
    trials=[]
    for ix in range(6):
        qid=prefix+"_"+str(ix)
        ms,_=post("http://127.0.0.1:18191",stmt,qid)
        trials.append({"qid":qid,"ms":round(ms,3)})
    logs={x["query_id"]:x for x in querylogs(prefix+"_") if x.get("type")=="QueryFinish"}
    for t in trials:
        if t["qid"] not in logs:raise AssertionError("Missing query_log "+t["qid"])
        ev=logs[t["qid"]].get("ProfileEvents",{})
        t["cpu_ms"]=ev.get("OSCPUVirtualTimeMicroseconds",0)/1000
        t["read_rows"]=logs[t["qid"]]["read_rows"]
    if any(t["read_rows"]!=200000 for t in trials):raise AssertionError("Unexpected row count")
    return {"trials":trials,"median_wall_ms":statistics.median(t["ms"] for t in trials[2:]),
            "median_cpu_ms":statistics.median(t["cpu_ms"] for t in trials[2:])}
def main():
    p=argparse.ArgumentParser()
    p.add_argument("--manifest",required=True);p.add_argument("--output",required=True)
    a=p.parse_args()
    m=json.loads(pathlib.Path(a.manifest).read_text())
    assert m.get("sha256")==SOURCE and m.get("series")==1330000
    source=inner("churn_ts","tags")
    raw=source.split(chr(96))[1]
    types={x["name"]:x["type"] for x in rows(
        "SELECT name,type FROM system.columns WHERE database='default' AND table="+sql_string(raw))}
    cols=", ".join(k+" "+types[k] for k in ("id","metric_name","tags"))
    result={"source_sha":SOURCE,"variant":{},"errors":[]}
    save(a.output,result)
    try:
        sql("SYSTEM STOP MERGES")
        for name,size in SIZES.items():
            target="tscpu_wide_dict_"+name
            sql(f"CREATE TABLE {target} ({cols}) ENGINE=MergeTree "
                "ORDER BY (metric_name,id) SETTINGS "
                "index_granularity=8192,min_rows_for_wide_part=0,min_bytes_for_wide_part=0")
            for offset in (0,100000):
                sql(f"INSERT INTO {target} (id,metric_name,tags) "
                    f"SELECT id,metric_name,tags FROM {source} ORDER BY metric_name,id "
                    f"LIMIT 100000 OFFSET {offset} SETTINGS "
                    f"max_threads=1,low_cardinality_max_dictionary_size={size},"
                    "low_cardinality_use_single_dictionary_for_part=0")
            digest=rows("SELECT count() AS n,sum(sipHash64(toString(id))) AS h FROM "+target)[0]
            if digest["n"]!=200000:raise AssertionError("Wrong fixed source rows")
            if name!="none" and digest!=result["variant"]["none"]["digest"]:
                raise AssertionError("Source IDs changed "+name)
            ps=parts(target)
            if len(ps)!=2 or any(x["part_type"]!="Wide" for x in ps):
                raise AssertionError("Expected exactly two WIDE parts: "+str(ps))
            r={"dict_size":size,"table":target,"digest":digest,
               "before":hash_files(ps),
               "measure_before":measure(target,f"cpupolicy_before_{name}_{os.getenv('GITHUB_RUN_ID','local')}")}
            result["variant"][name]=r;save(a.output,result)
            print("POLICY_WIDE_BEFORE",json.dumps({"variant":name,"size":size,
                 "content":r["before"]["content_sha256"],
                 "wall_ms":r["measure_before"]["median_wall_ms"],
                 "cpu_ms":r["measure_before"]["median_cpu_ms"]}),flush=True)
        before_hashes={v["before"]["content_sha256"] for v in result["variant"].values()}
        result["different_before"]=len(before_hashes)>1
        sql("SYSTEM START MERGES")
        for name,r in result["variant"].items():
            sql("OPTIMIZE TABLE "+r["table"]+" FINAL SETTINGS optimize_throw_if_noop=0")
            ps=parts(r["table"])
            if any(x["part_type"]!="Wide" for x in ps):
                raise AssertionError("Wide part became Compact unexpectedly")
            r["after"]=hash_files(ps)
            r["measure_after"]=measure(r["table"],f"cpupolicy_after_{name}_{os.getenv('GITHUB_RUN_ID','local')}")
            save(a.output,result)
        result["different_after"]=len({x["after"]["content_sha256"] for x in result["variant"].values()})>1
        if not result["different_before"]:
            result["errors"].append("The codec intervention produced byte-identical WIDE parts BEFORE merge; investigate settings propagation")
        result["passed"]=not result["errors"]
    except Exception as e:
        result["errors"].append(str(e)[:1800]);result["passed"]=False
    finally:
        save(a.output,result)
        compact={"passed":result.get("passed"),"errors":result["errors"],
                 "different_before":result.get("different_before"),
                 "different_after":result.get("different_after"),
                 "variants":{k:{"dict_size":v["dict_size"],"before_sha":v.get("before",{}).get("content_sha256"),
                                "after_sha":v.get("after",{}).get("content_sha256"),
                                "before":{q:v.get("measure_before",{}).get(q) for q in ("median_wall_ms","median_cpu_ms")},
                                "after":{q:v.get("measure_after",{}).get(q) for q in ("median_wall_ms","median_cpu_ms")}}
                             for k,v in result["variant"].items()}}
        save(str(pathlib.Path(a.output).with_suffix(".compact.json")),compact)
        print("POLICY_WIDE_RESULT",json.dumps(compact,separators=(",",":")),flush=True)
    if not result["passed"]:raise SystemExit(1)
if __name__=="__main__":main()
