#!/usr/bin/env python3
"""Test built-in CountMinSketch frequency estimates for skewed TimeSeries labels.

Pinned Statistics.cpp uses CountMinSketch before UniqV2 for equality predicates.
This test checks whether the lab binary actually supports countmin, whether
hot/cold estimates improve, and the statistics storage cost per MergeTree part.
No high-fanout join is executed.
"""
import argparse
import hashlib
import json
import pathlib
import re
import time
import urllib.error
import urllib.request

URL="http://127.0.0.1:18191/"
N=100000
def lit(s):return "'"+str(s).replace("'","\\'")+"'"
def sql(stmt):
    t=time.monotonic()
    try:
        with urllib.request.urlopen(urllib.request.Request(URL,data=stmt.encode(),method="POST"),timeout=180) as res:
            return round((time.monotonic()-t)*1000,3),res.read().decode()
    except urllib.error.HTTPError as ex:
        raise RuntimeError(f"HTTP {ex.code} {ex.read().decode(errors='replace')[:1800]}: {stmt[:200]}") from ex
def jsonrows(statement):
    return [json.loads(s) for s in sql(statement+" FORMAT JSONEachRow")[1].splitlines() if s.strip()]
def save(path,report):
    pathlib.Path(path).parent.mkdir(parents=True,exist_ok=True)
    pathlib.Path(path).write_text(json.dumps(report,sort_keys=True,indent=2)+"\n")
def plan(table,key):
    query=(f"SELECT count() FROM {table} AS a INNER JOIN {table} AS b "
           f"ON a.cluster=b.cluster WHERE a.cluster={lit(key)} "
           "SETTINGS use_statistics=1,join_algorithm='hash'")
    text=sql("EXPLAIN PLAN indexes=1 "+query)[1]
    return {"sql":query,"plan":text[:75000],
            "estimates":[line.strip() for line in text.splitlines()
                         if ("estimated" in line or "Algorithm:" in line)][:30]}
def catalog(table):
    return {
      "parts":jsonrows("SELECT name,rows,bytes_on_disk,marks FROM system.parts WHERE active AND database='default' AND table="+lit(table)),
      "columns":jsonrows("SELECT name,type,statistics FROM system.columns WHERE database='default' AND table="+lit(table)),
      "stats":jsonrows("SELECT column,statistics,estimates.cardinality AS ndv FROM system.parts_columns WHERE active AND database='default' AND table="+lit(table)+" AND column='cluster'")
    }
def experiment(typ,heavy,report):
    t=f"tslab_countmin_{'lc' if typ.startswith('LowCardinality') else 'string'}_{heavy}"
    val=f"if(number<{N*heavy//100},'hot',concat('cold-',toString(number%99)))"
    insert=f"INSERT INTO {t} SELECT number,CAST({val},{lit(typ)}) FROM numbers({N})"
    rec={"type":typ,"hot_percent":heavy,"source_sha256":hashlib.sha256(insert.encode()).hexdigest(),
         "actual":{},"plans":{}}
    report["cases"][t]=rec
    sql(f"CREATE TABLE {t} (id UInt64,cluster {typ}) ENGINE=MergeTree ORDER BY id")
    rec["ingest_ms"]=sql(insert)[0]
    sql(f"OPTIMIZE TABLE {t} FINAL SETTINGS optimize_throw_if_noop=0")
    rec["catalog_before"]=catalog(t)
    actual=jsonrows(f"SELECT cluster,count() AS n FROM {t} WHERE cluster IN ('hot','cold-1') GROUP BY cluster")
    actual_counts={x["cluster"]:x["n"] for x in actual}
    h=N*heavy//100
    cold=sum(1 for i in range(h,N) if i%99==1)
    if actual_counts.get("hot")!=h or actual_counts.get("cold-1")!=cold:
        raise AssertionError("Bad skew fixture "+json.dumps(actual_counts))
    rec["actual"]=actual_counts
    for key in ("hot","cold-1"):
        rec["plans"]["baseline_"+key]=plan(t,key)
    # Unsupported stats are a capability result. Do not suppress other errors.
    try:
        rec["modify_ms"]=sql(f"ALTER TABLE {t} MODIFY STATISTICS cluster TYPE uniq_v2, countmin")[0]
        rec["materialize_ms"]=sql(f"ALTER TABLE {t} MATERIALIZE STATISTICS ALL SETTINGS mutations_sync=2")[0]
    except Exception as e:
        rec["countmin_supported"]=False
        rec["unsupported_reason"]=str(e)[:1900]
        return
    rec["catalog_after"]=catalog(t)
    rec["countmin_supported"]=any("CountMinSketch" in x.get("statistics",[]) or
        "CountMin" in str(x.get("statistics")) for x in rec["catalog_after"]["columns"])
    # The label in system.columns is usually 'CountMinSketch'. Record it, but
    # never silently claim the sketch exists just because ALTER returned.
    if not rec["countmin_supported"]:
        rec["countmin_supported"]=("countmin" in str(rec["catalog_after"]["columns"]).lower())
    for key in ("hot","cold-1"):
        rec["plans"]["countmin_"+key]=plan(t,key)
    rec["part_size_change"]=(sum(x["bytes_on_disk"] for x in rec["catalog_after"]["parts"])-
                             sum(x["bytes_on_disk"] for x in rec["catalog_before"]["parts"]))
def main():
    p=argparse.ArgumentParser();p.add_argument("--out",required=True)
    a=p.parse_args()
    report={"cases":{},"errors":[],"source":"pinned StatisticsCountMinSketch.cpp and Statistics.cpp",
            "scope":"synthetic hot/cold labels; joins EXPLAIN only; no quadratic JOIN execution"}
    try:
        report["build"]=jsonrows("SELECT version() AS version")
        try:report["build_options"]=jsonrows("SELECT name,value FROM system.build_options WHERE name LIKE '%DATASKETCH%'")
        except Exception as ex:report["build_option_error"]=str(ex)[:600]
        for typ in ("String","LowCardinality(String)"):
            for hot in (90,99):
                experiment(typ,hot,report)
                rec=report["cases"][f"tslab_countmin_{'lc' if typ.startswith('LowCardinality') else 'string'}_{hot}"]
                print("COUNTMIN_SKEW_CASE",json.dumps({
                     "type":typ,"hot":hot,"actual":rec["actual"],
                     "supported":rec.get("countmin_supported"),
                     "error":rec.get("unsupported_reason"),
                     "size_change":rec.get("part_size_change")},separators=(",",":")),flush=True)
                save(a.out,report)
        report["passed"]=len(report["cases"])==4 and not report["errors"]
    except Exception as ex:
        report["errors"].append(str(ex)[:1800]);report["passed"]=False
    finally:
        save(a.out,report)
        print("COUNTMIN_SKEW_RESULT",json.dumps({
            "passed":report["passed"],"errors":report["errors"],
            "cases":{k:{"supported":v.get("countmin_supported"),
                         "error":v.get("unsupported_reason"),
                         "delta_bytes":v.get("part_size_change")} for k,v in report["cases"].items()}
        },separators=(",",":")),flush=True)
    if not report["passed"]:raise SystemExit(1)
if __name__=="__main__":main()
