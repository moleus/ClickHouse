#!/usr/bin/env python3
"""R07: exact stale-NaN bit filter, ordinary NaN and 5-tick native grid proof.

Stock O1 TimeSeries v11 with a tiny synthetic adversarial fixture. SQL reads
existing TAGS/SAMPLES only. No C++ patch and no performance claim.
"""
import argparse
import datetime as dt
import json
import math
import os
import pathlib
import sys
import traceback

sys.path[:0]=[".github/ts-lab/experiments",".github/ts-lab-perf",
            ".github/ts-lab-bench",".github/ts-lab-cpu"]
from r07_stale_duplicate_semantic_oracle import create
import r07_stale_duplicate_semantic_oracle as base
from bench_granularity_v11 import sql,inner,optimize_table,wait_no_merges
from bench_bucket_cxx_abca import post
from bench_filtered_selector_v4_sql import sql_string
from bench_native_multiclient_stock_20261103 import save

TABLE="tslab_r07_semantic_grid"
METRIC="tslab_r07_semantic_grid_metric"
STALE_BITS=0x7ff0000000000002
ORDINARY_NAN_BITS=0x7ff8000000000001
EPOCH=1785801600
TICKS=tuple(EPOCH+i*60 for i in range(1,6))
WINDOW=120
HOST="http://127.0.0.1:18191"
RUN=os.getenv("GITHUB_RUN_ID","local")

def timestamp(epoch):
    return dt.datetime.fromtimestamp(epoch,dt.timezone.utc).strftime("%Y-%m-%d %H:%M:%S.000")

def sample(t,expr):
    return f"(toDateTime64({sql_string(timestamp(EPOCH+t))},3,'UTC'),{expr})"

def put(stage, samples, suffix):
    expression="["+(",".join(sample(t,v) for t,v in samples))+"]"
    q=("INSERT INTO "+TABLE+" (metric_name,tags,samples) SELECT "+
       sql_string(METRIC)+",map('stage',"+sql_string(stage)+"),"+expression)
    post(HOST,q,f"r07grid_insert_{RUN}_{stage}_{suffix}")
    return q

def load_fixture():
    base.TABLE=TABLE
    create()
    stale=f"reinterpretAsFloat64(toUInt64({STALE_BITS}))"
    ordinary=f"reinterpretAsFloat64(toUInt64({ORDINARY_NAN_BITS}))"
    initial=[
        ("stale_mix",[(60,"toFloat64(5)"),(120,stale),(180,"toFloat64(9)"),(240,"toFloat64(11)")]),
        ("stale_only",[(60,stale),(240,stale)]),
        ("ordinary_nan",[(60,ordinary),(120,"toFloat64(4)"),(240,"toFloat64(6)")]),
        ("duplicate",[(60,"toFloat64(1)"),(60,"toFloat64(7)"),(120,"toFloat64(3)"),(180,"toFloat64(5)")]),
        ("boundary",[(60,"toFloat64(100)"),(120,"toFloat64(5)"),(180,"toFloat64(9)"),(240,"toFloat64(11)")]),
        ("cross_insert",[(60,"toFloat64(1)"),(120,"toFloat64(3)")]),
    ]
    queries=[put(stage,rows,"first") for stage,rows in initial]
    queries.append(put("cross_insert",[(60,"toFloat64(7)")],"late"))
    optimized=optimize_table(TABLE)
    if any(isinstance(v,dict) for v in optimized.values()):
        raise AssertionError(("Optimize incomplete",optimized))
    wait_no_merges()
    tags=inner(TABLE,"tags")
    samples=inner(TABLE,"samples")
    stages=json.loads(sql("SELECT count() AS n,uniqExact(id) AS ids FROM "+tags+
        " FORMAT JSONEachRow").strip())
    if stages!={"n":6,"ids":6}:raise AssertionError(("Lost/corrupted tag identities",stages))
    physical=json.loads(sql("SELECT count() AS rows,sum(length(samples)) AS points FROM "+
        samples+" FORMAT JSONEachRow").strip())
    return {"insert_sql":queries,"optimized":optimized,"source_id_counts":stages,
            "physical_counts":physical}

def sql_query(safe=True):
    tags=inner(TABLE,"tags")
    samples=inner(TABLE,"samples")
    evals="SELECT arrayJoin(["+(",".join(
        "toDateTime64("+sql_string(timestamp(t))+",3,'UTC')" for t in TICKS))+"]) AS eval"
    predicate=" AND reinterpretAsUInt64(p.val)!="+str(STALE_BITS) if safe else ""
    by_id=(
        "SELECT p.id AS id,e.eval AS eval,avg(p.val) AS v "
        "FROM (SELECT id,tupleElement(pt,1) AS ts,tupleElement(pt,2) AS val "
        "FROM (SELECT id,arrayJoin(samples) AS pt FROM "+samples+")) AS p "
        "CROSS JOIN ("+evals+") AS e "
        "WHERE p.ts>e.eval-INTERVAL 120 SECOND AND p.ts<=e.eval"+predicate+
        " GROUP BY p.id,e.eval"
    )
    q=(
        "SELECT t.tags['stage'] AS stage,toUnixTimestamp(i.eval) AS tick,"
        "toUInt8(isNaN(avg(i.v))) AS is_nan,"
        "if(isNaN(avg(i.v)),toFloat64(0),avg(i.v)) AS finite_value "
        "FROM ("+by_id+") AS i INNER JOIN "+tags+" AS t ON i.id=t.id "
        "WHERE t.metric_name="+sql_string(METRIC)+
        " GROUP BY stage,i.eval ORDER BY stage,tick FORMAT JSONEachRow"
    )
    return q

def normalize_sql(rows):
    result={}
    for ln in rows.splitlines():
        if not ln.strip():continue
        p=json.loads(ln)
        key=(str(p["stage"]),int(p["tick"]))
        if key in result:raise AssertionError(("duplicate SQL point",key))
        result[key]={"nan":bool(p["is_nan"]),"value":float(p["finite_value"])}
    return result

def native_query():
    expr="avg by(stage)(avg_over_time("+METRIC+"[2m]))"
    query=(
        "SELECT tags,arrayMap(p -> (toUnixTimestamp(tupleElement(p,1)),"
        "toUInt8(isNaN(tupleElement(p,2))),"
        "if(isNaN(tupleElement(p,2)),toFloat64(0),tupleElement(p,2))),"
        "samples) AS safe_samples "
        "FROM prometheusQueryRange("+TABLE+","+sql_string(expr)+","+
        str(TICKS[0])+","+str(TICKS[-1])+",60) "
        "SETTINGS max_threads=4,log_queries=1 FORMAT JSONEachRow"
    )
    return query

def normalize_native(raw):
    result={}
    for line in raw.splitlines():
        if not line.strip():continue
        row=json.loads(line)
        labels=row["tags"]
        stage=dict(labels).get("stage")
        if stage is None:raise AssertionError(("native group missing stage",row))
        for point in row["safe_samples"]:
            tick,nan,value=point
            key=(str(stage),int(tick))
            if key in result:raise AssertionError(("duplicate native tick",key))
            result[key]={"nan":bool(nan),"value":float(value)}
    return result

def compare(a,b,title):
    if set(a)!=set(b):
        raise AssertionError((title,"different labels/timestamps",
            sorted(set(a)-set(b)),sorted(set(b)-set(a))))
    for key in a:
        if a[key]["nan"]!=b[key]["nan"]:
            raise AssertionError((title,key,"nan classification",a[key],b[key]))
        if not a[key]["nan"] and not math.isclose(a[key]["value"],b[key]["value"],
                                                 rel_tol=1e-10,abs_tol=1e-10):
            raise AssertionError((title,key,"value mismatch",a[key],b[key]))
    return len(a)

def stored_bits():
    q=(
        "SELECT t.tags['stage'] AS stage,count() AS points,"
        "countIf(reinterpretAsUInt64(tupleElement(s.pt,2))="+str(STALE_BITS)+") AS stale_count,"
        "countIf(reinterpretAsUInt64(tupleElement(s.pt,2))="+str(ORDINARY_NAN_BITS)+") AS ordinary_nan_count,"
        "sumIf(toFloat64(tupleElement(s.pt,2)),NOT isNaN(tupleElement(s.pt,2))) AS finite_sum "
        "FROM (SELECT id,arrayJoin(samples) AS pt FROM "+inner(TABLE,"samples")+
        ") AS s INNER JOIN "+inner(TABLE,"tags")+" AS t ON s.id=t.id "
        "GROUP BY stage ORDER BY stage FORMAT JSONEachRow"
    )
    rows={x["stage"]:x for x in (json.loads(ln) for ln in sql(q).splitlines() if ln.strip())}
    if rows.get("ordinary_nan",{}).get("ordinary_nan_count")!=1:
        raise AssertionError(("Ordinary NaN lost",rows))
    if rows.get("stale_only",{}).get("stale_count")!=2:
        raise AssertionError(("Exact stale markers lost",rows))
    if rows.get("duplicate",{}).get("points")!=3:
        raise AssertionError(("Within-insert duplicate retained",rows))
    if rows.get("cross_insert",{}).get("points")!=2:
        raise AssertionError(("Across-insert duplicate retained",rows))
    return {"query":q,"rows":rows}

def main():
    ap=argparse.ArgumentParser()
    ap.add_argument("--output",required=True)
    args=ap.parse_args()
    report={"question_id":"R07","status":"STARTED","passed":False,
            "source_sha":"52529f0c711566d696917678bf15e7068772fdde",
            "stock_binary_sha256":"f0a2fa1f814c58853f43423705cac10ab9df3bfe5887e27ebe9620ba7cb9660e",
            "ticks":TICKS,"window_seconds":WINDOW,"stale_bits":hex(STALE_BITS),
            "ordinary_nan_bits":hex(ORDINARY_NAN_BITS),"errors":[]}
    save(args.output,report)
    try:
        report["fixture"]=load_fixture()
        report["physical_bits"]=stored_bits()
        native_q=native_query()
        _,raw=post(HOST,native_q,f"r07grid_native_{RUN}")
        nat=normalize_native(raw)
        sq=safe_q=sql_query(True)
        _,raw=post(HOST,sq,f"r07grid_sqlsafe_{RUN}")
        safe=normalize_sql(raw)
        before_q=sql_query(False)
        _,raw=post(HOST,before_q,f"r07grid_sqlold_{RUN}")
        old=normalize_sql(raw)
        report["native_sql"]=native_q
        report["new_sql"]=safe_q
        report["old_sql"]=before_q
        report["native_points"]=dict((str(k),v) for k,v in nat.items())
        report["safe_points"]=dict((str(k),v) for k,v in safe.items())
        report["old_points"]=dict((str(k),v) for k,v in old.items())
        report["strict_parity_points"]=compare(nat,safe,"native vs stale-safe SQL")
        if report["strict_parity_points"]<12:
            raise AssertionError("Adversarial grid output is too small")
        differing=[k for k in set(nat)|set(old) if nat.get(k)!=old.get(k)]
        if ("stale_mix",TICKS[1]) not in differing:
            raise AssertionError(("Failed to show original raw-SQL stale error",differing))
        if ("ordinary_nan",TICKS[1]) not in nat or not nat[("ordinary_nan",TICKS[1])]["nan"]:
            raise AssertionError(("Ordinary NaN not preserved in native range selector",nat))
        if ("ordinary_nan",TICKS[1]) not in safe or not safe[("ordinary_nan",TICKS[1])]["nan"]:
            raise AssertionError(("Safe SQL incorrectly removed ordinary NaN",safe))
        report["raw_sql_mismatch_keys"]=list(map(str,sorted(differing)))
        report["raw_sql_mismatch_count"]=len(differing)
        report["passed"]=True
        report["status"]="PASS_STALE_SAFE_NATIVE_5TICK_FULL_PARITY"
    except Exception as exc:
        report["status"]="FAILED"
        report["errors"].append({"fatal":str(exc)[:2200],"trace":traceback.format_exc()[-4800:]})
    finally:
        save(args.output,report)
        compact={"passed":report["passed"],"status":report["status"],
                 "errors":report["errors"],"exact_points":report.get("strict_parity_points"),
                 "raw_mismatch_count":report.get("raw_sql_mismatch_count"),
                 "raw_mismatch_keys":report.get("raw_sql_mismatch_keys"),
                 "physical":report.get("fixture",{}).get("physical_counts"),
                 "stored":report.get("physical_bits",{}).get("rows")}
        save(str(pathlib.Path(args.output).with_suffix(".compact.json")),compact)
        print("R07_STALE_GRID_DONE",json.dumps(compact,separators=(",",":"),
             sort_keys=True),flush=True)
    if not report["passed"]:raise SystemExit(1)

if __name__=="__main__":
    main()
