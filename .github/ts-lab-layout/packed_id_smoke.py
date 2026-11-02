#!/usr/bin/env python3
"""Test packed prefix in existing Tuple(UInt64,LowCardinality(UUID)) ID width.

Experimental ID truncates 128-bit tag hash to112 bits and reserves upper16
bits for a selected tag. Never use in production without collision/migration,
ACL, replication, missing-tag and compound-index tests.
"""
import argparse,hashlib,json,pathlib,time,sys
sys.path.insert(0,".github/ts-lab-layout")
sys.path.insert(0,".github/ts-lab-bench")
from layout_id_smoke import (query,insert,data_rows,data_bytes,METRIC,START,DAY)
from bench_bucket_cxx_abca import normalize_any,assert_same
CANDIDATES=("default","stage","project")

def generator(tag):
    # The underlying source's 128-bit hash is truncated from 128 to112 bits.
    # A 16-bit tag-value hash goes to high UInt128 bits, preserving ID width.
    trunc="subtract(bitShiftLeft(toUInt128(1),112),toUInt128(1))"
    hi=f"bitShiftLeft(bitAnd(toUInt128(sipHash64(tags['{tag}'])),toUInt128(65535)),112)"
    lo=f"bitAnd(reinterpretAsUInt128(sipHash128(tags)),{trunc})"
    return (f"tuple(sipHash64(metric_name),toLowCardinality("
            f"reinterpretAsUUID(bitOr({hi},{lo}))))")
def schema(table,kind):
    if kind=="default":
        return f"CREATE TABLE {table} ENGINE=TimeSeries SETTINGS recent_samples_ttl_seconds=0"
    # No change to actual ID type in TAGS/SAMPLES/TIMERANGES.
    idtype="Tuple(UInt64, LowCardinality(UUID))"
    return (f"CREATE TABLE {table} ENGINE=TimeSeries SETTINGS recent_samples_ttl_seconds=0 "
      f"SAMPLES INNER COLUMNS (id {idtype}) "
      f"TAGS INNER COLUMNS (id {idtype} DEFAULT {generator(kind)}) "
      f"TIME RANGES INNER COLUMNS (id {idtype})")
def tbl(table,kind):
    u=query(f"SELECT toString(uuid) FROM system.tables WHERE name='{table}' AND database='default' FORMAT TSV").strip()
    return f"default.\x60.inner_id.{kind}.{u}\x60"
def main():
    p=argparse.ArgumentParser();p.add_argument("--out",required=True)
    a=p.parse_args();out=pathlib.Path(a.out);out.mkdir(parents=True,exist_ok=True)
    records=data_rows();data=data_bytes(records)
    report={"schema":1,"source_sha256":hashlib.sha256(data).hexdigest(),"rows":len(records),
            "collision_policy":"112-bit remaining tag hash, 16-bit packed chosen label; not collision-free",
            "cases":{},"errors":[],"warnings":[
                "This ID is NOT stable against a change in per-metric chosen tag policy",
                "The Type remains Tuple(UInt64,LowCardinality(UUID)); content and ID registration semantics change",
                "This is a prototype with SHA truncation, NOT a supported default identity",
                "Missing tags map to one deterministic empty prefix; all unique tag maps must remain distinct"]}
    (out/"packed-smoke.json").write_text(json.dumps(report,indent=2)+"\n")
    queries={
      "whole":f"avg by(stage)(avg_over_time({METRIC}[1d]))",
      "stage":f"avg by(stage)(avg_over_time({METRIC}{{stage=\"stage-01\"}}[1d]))",
      "project":f"avg by(stage)(avg_over_time({METRIC}{{project=\"project-01\"}}[1d]))",
      "both":f"avg by(stage)(avg_over_time({METRIC}{{stage=\"stage-01\",project=\"project-01\"}}[1d]))",
      "regex":f"avg by(stage)(avg_over_time({METRIC}{{job=~\"job-0[1-4]\"}}[1d]))"}
    baseline={}
    for kind in CANDIDATES:
        table="tsl_packed_"+kind
        result={"ddl":schema(table,kind),"queries":{}}
        report["cases"][kind]=result
        try:
            query(result["ddl"])
            start=time.monotonic()
            insert(table,data_bytes(records[:len(records)//2]))
            insert(table,data_bytes(records[len(records)//2:]))
            result["ingest_s"]=round(time.monotonic()-start,3)
            query("OPTIMIZE TABLE "+table+" FINAL",timeout=100)
            result["id_type"]=query(f"SELECT type FROM system.columns WHERE database='default' "
                  f"AND table='.inner_id.tags.{tbl(table,'tags').split('.')[-1].replace(chr(96),'')}' "
                  "AND name='id' FORMAT TSV").strip()
            id_unique=int(query("SELECT uniqExact(id) FROM "+tbl(table,"tags")+" FORMAT TSV").strip())
            # Both metrics share different IDs; exactly one record for each
            # unique series in this smoke fixture.
            if id_unique!=len(records):raise RuntimeError("ID hash collision or incorrect fixture count")
            result["distinct_ids"]=id_unique
            for case,expr in queries.items():
                q=(f"SELECT * FROM prometheusQueryRange({table},'{expr}',"
                   f"{START+DAY},{START+4*DAY},{DAY}) SETTINGS max_threads=4 FORMAT JSONEachRow")
                values=normalize_any(query(q,timeout=100).encode())
                if kind=="default":baseline[case]=values
                else:assert_same(baseline[case],values,case,kind)
                result["queries"][case]={"groups":len(values),"parity":True}
                print("PACKED_ID_CASE",json.dumps({"variant":kind,"case":case,
                   "groups":len(values),"pass":True},separators=(",",":")),flush=True)
        except Exception as exc:
            result["error"]=str(exc)[:2800]
            report["errors"].append({"variant":kind,"error":str(exc)[:2800]})
        (out/"packed-smoke.json").write_text(json.dumps(report,indent=2)+"\n")
    report["passed"]=not report["errors"] and len(report["cases"])==len(CANDIDATES)
    (out/"packed-smoke.json").write_text(json.dumps(report,indent=2)+"\n")
    print("PACKED_ID_DONE",json.dumps({"passed":report["passed"],"error_count":len(report["errors"]),
        "cases":list(report["cases"]),"errors":report["errors"]},separators=(",",":")),flush=True)
    if not report["passed"]:raise SystemExit(1)
if __name__=="__main__":main()
