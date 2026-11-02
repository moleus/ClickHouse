#!/usr/bin/env python3
"""Native TDigest equality-frequency statistics on numeric label IDs.

The pinned optimizer uses TDigest before UniqV2 for equality if NDV < 2048.
Compare skewed 100-key numeric labels to ordinary UniqV2. This checks an
existing optimizer feature without adding C++ code or reading production data.
"""
import argparse,hashlib,json,pathlib,re,time,urllib.error,urllib.request
URL="http://127.0.0.1:18191/"
N=100000
def sql(stmt):
    start=time.monotonic()
    try:
        with urllib.request.urlopen(urllib.request.Request(URL,data=stmt.encode(),method="POST"),timeout=180) as r:
            return (time.monotonic()-start)*1000,r.read().decode()
    except urllib.error.HTTPError as e:
        raise RuntimeError(f"HTTP {e.code}: {e.read().decode(errors='replace')[:1800]}: {stmt[:200]}") from e
def lit(s):return "'"+str(s).replace("'","\\'")+"'"
def rows(q):
    return [json.loads(x) for x in sql(q+" FORMAT JSONEachRow")[1].splitlines() if x.strip()]
def save(path,data):
    pathlib.Path(path).parent.mkdir(parents=True,exist_ok=True)
    pathlib.Path(path).write_text(json.dumps(data,indent=2)+"\n")
def plan(table,val):
    query=(f"SELECT count() FROM {table} AS a INNER JOIN {table} AS b "
           f"ON a.label_id=b.label_id WHERE a.label_id={val} "
           "SETTINGS use_statistics=1,join_algorithm='hash'")
    text=sql("EXPLAIN PLAN indexes=1 "+query)[1]
    return {"sql":query,"text":text[:65000],
            "estimates":[line.strip() for line in text.splitlines()
                         if "estimated" in line or "Algorithm:" in line][:20]}
def details(table):
    return {"columns":rows("SELECT name,type,statistics FROM system.columns WHERE table="+lit(table)+" AND database='default'"),
            "parts":rows("SELECT name,rows,bytes_on_disk FROM system.parts WHERE table="+lit(table)+" AND active"),
            "estimates":rows("SELECT column,statistics,estimates.cardinality AS ndv FROM system.parts_columns WHERE table="+lit(table)+" AND active AND column='label_id'")}
def run(heavy,report):
    table=f"tslab_numeric_tdigest_{heavy}"
    h=N*heavy//100
    fixture=f"INSERT INTO {table} SELECT number,toUInt16(if(number<{h},0,1+number%99)) FROM numbers({N})"
    out={"hot":heavy,"fixture_sha256":hashlib.sha256(fixture.encode()).hexdigest()}
    report["cases"][table]=out
    sql(f"CREATE TABLE {table} (id UInt64,label_id UInt16) ENGINE=MergeTree ORDER BY id")
    out["insert_ms"]=sql(fixture)[0]
    sql(f"OPTIMIZE TABLE {table} FINAL SETTINGS optimize_throw_if_noop=0")
    out["actual"]=rows(f"SELECT label_id,count() AS n FROM {table} WHERE label_id IN (0,2) GROUP BY label_id")
    actual={x["label_id"]:x["n"] for x in out["actual"]}
    want_cold=sum(1 for i in range(h,N) if i%99==1)
    if actual.get(0)!=h or actual.get(2)!=want_cold:
        raise AssertionError("Incorrect numeric skew: "+json.dumps(actual))
    out["before"]=details(table)
    out["baseline"]={v:plan(table,v) for v in (0,2)}
    out["materialize_ms"]=sql(f"ALTER TABLE {table} MODIFY STATISTICS label_id TYPE uniq_v2, tdigest")[0]
    out["stats_build_ms"]=sql(f"ALTER TABLE {table} MATERIALIZE STATISTICS ALL SETTINGS mutations_sync=2")[0]
    out["after"]=details(table)
    if not any("TDigest" in str(x["statistics"]) for x in out["after"]["columns"]):
        raise AssertionError("TDigest absent from actual catalog")
    out["with_tdigest"]={v:plan(table,v) for v in (0,2)}
    before_bytes=sum(x["bytes_on_disk"] for x in out["before"]["parts"])
    after_bytes=sum(x["bytes_on_disk"] for x in out["after"]["parts"])
    out["stats_disk_delta"]=after_bytes-before_bytes
    out["actual_join_pairs"]={str(v):actual[v]**2 for v in (0,2)}
    return out
def main():
    p=argparse.ArgumentParser();p.add_argument("--out",required=True)
    a=p.parse_args();r={"cases":{},"errors":[],
        "scope":"synthetic numeric categorical IDs, SQL EXPLAIN, no quadratic JOIN execution",
        "source":"ColumnStatistics::estimateEqual uses TDigest when UniqV2 NDV is <2048"}
    try:
        r["version"]=rows("SELECT version() AS version")
        for heavy in (90,99):
            out=run(heavy,r)
            print("TDIGEST_SKEW_CASE",json.dumps({
                "hot_percent":heavy,
                "actual":out["actual"],"stats_disk_delta":out["stats_disk_delta"],
                "after_stats":out["after"]["columns"],
                "join_estimates":{str(k):v["estimates"][:7] for k,v in out["with_tdigest"].items()}
            },separators=(",",":")),flush=True)
            save(a.out,r)
        r["passed"]=len(r["cases"])==2 and not r["errors"]
    except Exception as ex:
        r["passed"]=False;r["errors"].append(str(ex)[:1900])
    finally:
        save(a.out,r)
        print("TDIGEST_SKEW_RESULT",json.dumps({"passed":r["passed"],"errors":r["errors"]},separators=(",",":")),flush=True)
    if not r["passed"]:raise SystemExit(1)
if __name__=="__main__":
    main()
