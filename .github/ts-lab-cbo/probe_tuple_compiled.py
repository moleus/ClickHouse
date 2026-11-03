#!/usr/bin/env python3
"""Evaluate compiled opt-in Tuple UniqV2 statistics on two immutable partitions.

This checks correctness and per-part NDV, NOT query latency or native PromQL.
"""
import argparse,hashlib,json,pathlib,time,urllib.error,urllib.request
URL="http://127.0.0.1:18191/"
SOURCE="52529f0c711566d696917678bf15e7068772fdde"
def sql(q):
    started=time.monotonic()
    try:
        with urllib.request.urlopen(urllib.request.Request(URL,data=q.encode(),method="POST"),timeout=180) as r:
            return round((time.monotonic()-started)*1000,3),r.read().decode()
    except urllib.error.HTTPError as e:
        raise RuntimeError(f"HTTP {e.code}: {e.read().decode(errors='replace')[:1400]} | {q[:240]}") from e
def rows(q):
    return [json.loads(line) for line in sql(q+" FORMAT JSONEachRow")[1].splitlines() if line.strip()]
def save(path,report):
    pathlib.Path(path).parent.mkdir(parents=True,exist_ok=True)
    pathlib.Path(path).write_text(json.dumps(report,indent=2)+"\n")
def one(k,n,report):
    name=f"tslab_tuple_compiled_{k}"
    # Each even/odd id set lives in a separate immutable partition.
    # For k=1, a naive sum of per-part NDV is 2, not global NDV 1.
    schema=f"CREATE TABLE {name} (id UInt64, key Tuple(UInt64,UInt64)) ENGINE=MergeTree PARTITION BY toUInt8(id%2) ORDER BY id"
    fixture=(f"INSERT INTO {name} SELECT number,tuple(toUInt64(number%{k}),"
             f"toUInt64((number%{k})*13+7)) FROM numbers({n})")
    v={"k":k,"rows":n,"schema":schema,"fixture_sha256":hashlib.sha256(fixture.encode()).hexdigest()}
    report["cases"][str(k)]=v
    sql(schema)
    v["add_stats_ms"]=sql(f"ALTER TABLE {name} ADD STATISTICS key TYPE uniq_v2")[0]
    v["insert_ms"]=sql(fixture)[0]
    v["materialize_ms"]=sql(f"ALTER TABLE {name} MATERIALIZE STATISTICS ALL SETTINGS mutations_sync=2")[0]
    v["actual"]=rows(f"SELECT count() AS n,uniqExact(key) AS ndv,uniqCombined64(key) AS approx FROM {name}")[0]
    v["per_partition_actual"]=rows(f"SELECT toUInt8(id%2) AS partition,count() AS n,uniqExact(key) AS ndv FROM {name} GROUP BY partition ORDER BY partition")
    v["parts"]=rows("SELECT name,partition,rows,statistics,estimates.cardinality AS ndv "
                    f"FROM system.parts_columns WHERE active AND database='default' AND table='{name}' "
                    "AND column='key' ORDER BY partition")
    v["part_storage"]=rows(f"SELECT partition,rows,bytes_on_disk FROM system.parts WHERE active AND database='default' AND table='{name}' ORDER BY partition")
    _,plan=sql("EXPLAIN PLAN indexes=1 "+f"SELECT count() FROM {name} a INNER JOIN {name} b ON a.key=b.key "
               "WHERE a.id<10 AND b.id<10 SETTINGS use_statistics=1,join_algorithm='hash'")
    v["join_plan"]=plan[:35000]
    got=v["actual"]
    if got["n"]!=n or got["ndv"]!=k:raise AssertionError(f"Wrong Tuple input {k}: {got}")
    if len(v["parts"])!=2 or len(v["per_partition_actual"])!=2:
        raise AssertionError(f"Expected two active Tuple parts for {k}: {v['parts']}")
    for got_part,exact_part in zip(v["parts"],v["per_partition_actual"]):
        est=got_part["ndv"]; exact=exact_part["ndv"]
        if est is None or abs(est-exact)>max(2,0.07*exact):
            raise AssertionError(f"Incorrect Tuple part NDV: {k}, est {est}, exact {exact}")
    if abs(got["approx"]-k)>max(2,0.07*k):
        raise AssertionError(f"Unexpected SQL uniqCombined64 estimate at {k}: {got}")
    return v
def main():
    p=argparse.ArgumentParser();p.add_argument("--out",required=True)
    a=p.parse_args();report={"source":SOURCE,"scope":"compiled Tuple UniqV2 opt-in SQL statistics, not native PromQL","cases":{},"errors":[]}
    try:
        report["version"]=rows("SELECT version() AS version")
        for k in (1,2,8,38,152,150000):
            case=one(k,150000 if k==150000 else 30000,report)
            print("COMPILED_TUPLE_NDV",json.dumps({"k":k,"actual":case["actual"],"parts":case["parts"]},separators=(",",":")),flush=True)
            save(a.out,report)
        report["passed"]=len(report["cases"])==6
    except Exception as e:
        report["errors"].append(str(e)[:1900]);report["passed"]=False
    finally:
        save(a.out,report)
        print("COMPILED_TUPLE_DONE",json.dumps({"passed":report["passed"],"errors":report["errors"]},separators=(",",":")),flush=True)
    if not report["passed"]:raise SystemExit(1)
if __name__=="__main__":main()
