#!/usr/bin/env python3
"""Prove built-in NDV support and estimated JOIN cardinality for Tuple keys.

A 150k-row isolated MergeTree lab is enough for this schema capability check.
No production data, direct SQL only. Distinguish estimator errors from runtime.
"""
import argparse,json,pathlib,time,urllib.request,urllib.parse,urllib.error
URL="http://127.0.0.1:18191/"
def literal(x):return "'"+str(x).replace("'","\\'")+"'"
def sql(q):
    start=time.perf_counter()
    try:
        with urllib.request.urlopen(urllib.request.Request(URL,data=q.encode(),method="POST"),timeout=150) as p:
            return {"ms":round((time.perf_counter()-start)*1000,3),"body":p.read().decode()}
    except urllib.error.HTTPError as e:
        raise RuntimeError(e.read().decode(errors="replace")[:1800]) from e
def jsonrows(q):
    return [json.loads(x) for x in sql(q+" FORMAT JSONEachRow")["body"].splitlines() if x.strip()]
def attempt(q,out,key,raw=False):
    try:out[key]=sql(q)["body"][:80000] if raw else jsonrows(q)
    except Exception as ex:out[key+"_error"]=str(ex)[:1800]
def save(path,d):
    pathlib.Path(path).parent.mkdir(parents=True,exist_ok=True)
    pathlib.Path(path).write_text(json.dumps(d,indent=2)+"\n")
def main():
    ap=argparse.ArgumentParser()
    ap.add_argument("--out",required=True)
    ap.add_argument("--rows",type=int,default=150000)
    a=ap.parse_args()
    assert 1000<=a.rows<=500000
    out=pathlib.Path(a.out)
    t="cbo_tuple_ndv"
    report={"rows":a.rows,"errors":[],"source":"synthetic deterministic tuple keys"}
    save(out,report)
    try:
        sql(f"CREATE TABLE {t} (id_tuple Tuple(UInt64,UInt64), "
            "id_fingerprint UInt64 MATERIALIZED sipHash64(id_tuple)) "
            "ENGINE=MergeTree ORDER BY id_tuple")
        sql(f"INSERT INTO {t} (id_tuple) SELECT tuple(toUInt64(number%10),toUInt64(number)) "
            f"FROM numbers({a.rows})")
        attempt(f"SELECT count() AS rows,uniqExact(id_tuple) AS exact,"
                "uniqCombined64(id_tuple) AS estimated,"
                "uniqCombined64(id_fingerprint) AS fingerprint_estimated "
                f"FROM {t}",report,"ndv")
        # This failure is the expected current tuple statistic capability:
        # do not assume adding a statistic to a Tuple column is supported.
        try:
            sql(f"ALTER TABLE {t} ADD STATISTICS id_tuple TYPE uniq_v2")
            report["tuple_uniq_v2_accepted"]=True
        except Exception as ex:
            report["tuple_uniq_v2_accepted"]=False
            report["tuple_uniq_v2_rejection"]=str(ex)[:1800]
        sql(f"ALTER TABLE {t} ADD STATISTICS id_fingerprint TYPE uniq_v2")
        sql(f"ALTER TABLE {t} MATERIALIZE STATISTICS ALL SETTINGS mutations_sync=2")
        attempt("SELECT column,statistics,estimates.cardinality AS ndv,rows "
                "FROM system.parts_columns WHERE active AND database='default' "
                f"AND table={literal(t)}",report,"parts_stats")
        for col in ("id_tuple","id_fingerprint"):
            query=(f"SELECT count() FROM {t} AS a INNER JOIN {t} AS b "
                   f"ON a.{col}=b.{col} SETTINGS use_statistics=1,join_algorithm='hash'")
            attempt("EXPLAIN PLAN indexes=1 "+query,report,"explain_"+col,raw=True)
        report["passed"]=bool(report.get("ndv")) and bool(report.get("parts_stats"))
    except Exception as ex:
        report["errors"].append(str(ex)[:1800]);report["passed"]=False
    finally:
        save(out,report)
        print("TUPLE_NDV_RESULT",json.dumps({"passed":report.get("passed"),
              "tuple_stats_accepted":report.get("tuple_uniq_v2_accepted"),
              "ndv":report.get("ndv"),"parts_stats":report.get("parts_stats"),
              "errors":report["errors"]},separators=(",",":")),flush=True)
    if not report["passed"]:raise SystemExit(1)
if __name__=="__main__":main()
