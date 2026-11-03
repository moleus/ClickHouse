#!/usr/bin/env python3
"""R07 stock TimeSeries: stale-NaN range-selector vs raw SQL, duplicate stamps.

Does not measure speed, modify C++ or claim general semantic equivalence.
Proves explicit semantic admission is needed before any native SQL fastpath.
"""
import argparse
import json
import os
import pathlib
import sys
import traceback

sys.path[:0]=[".github/ts-lab-perf",".github/ts-lab-bench",".github/ts-lab-cpu"]
from bench_granularity_v11 import sql,inner,optimize_table,wait_no_merges
from bench_bucket_cxx_abca import post,normalize_any
from bench_filtered_selector_v4_sql import sql_string
from bench_native_multiclient_stock_20261103 import save

TABLE="tslab_r07_semantic_probes"
STALE_BITS=0x7ff0000000000002
RUN=os.getenv("GITHUB_RUN_ID","local")
BASE=1785801600  # 2026-08-04 00:00:00 UTC
END=BASE+180
SOURCE_SHA="52529f0c711566d696917678bf15e7068772fdde"
BINARY_SHA="f0a2fa1f814c58853f43423705cac10ab9df3bfe5887e27ebe9620ba7cb9660e"

def create():
    sql(
        "CREATE TABLE "+TABLE+" ENGINE=TimeSeries "
        "SETTINGS version=11,recent_samples_ttl_seconds=0,"
        "samples_bucket_step_seconds=3600 "
        "SAMPLES INNER COLUMNS (id Tuple(UInt64,UUID)) "
        "TAGS INNER COLUMNS (id Tuple(UInt64,UUID) DEFAULT "
        "tuple(sipHash64(metric_name),reinterpretAsUUID(sipHash128(tags))),"
        "metric_name LowCardinality(String),"
        "tags Map(LowCardinality(String),String),"
        "INDEX tags_idx tags TYPE text(tokenizer = 'keyValuePairs') "
        "GRANULARITY 100000000) "
        "TIME RANGES INNER COLUMNS (id Tuple(UInt64,UUID))"
    )

def put():
    t1="toDateTime64('2026-08-04 00:01:00',3,'UTC')"
    t2="toDateTime64('2026-08-04 00:02:00',3,'UTC')"
    stale=f"reinterpretAsFloat64(toUInt64({STALE_BITS}))"
    q=(
        f"INSERT INTO {TABLE} (metric_name,tags,samples) "
        f"SELECT 'tslab_r07_semantic',map('stage','stale'),"
        f"[({t1},toFloat64(5.0)),({t2},{stale})] "
        "UNION ALL "
        f"SELECT 'tslab_r07_semantic',map('stage','duplicate'),"
        f"[({t1},toFloat64(1.0)),({t1},toFloat64(7.0)),"
        f"({t2},toFloat64(3.0))]"
    )
    post("http://127.0.0.1:18191",q,f"r07semantic_insert_{RUN}")
    opt=optimize_table(TABLE)
    if any(isinstance(x,dict) for x in opt.values()):
        raise AssertionError(("Optimize did not finish",opt))
    wait_no_merges()
    return {"insert_sql":q,"optimize":opt}

def source_data():
    # Compare each stored sample's bit representation and its per-stage mean.
    # Using isNaN avoids JSON encoding issues for special float payloads.
    ts=inner(TABLE,"tags")
    ss=inner(TABLE,"samples")
    q=(
        "SELECT t.tags['stage'] AS stage,count() AS physical_points,"
        "countIf(reinterpretAsUInt64(tupleElement(s.point,2))="
        +str(STALE_BITS)+") AS stale_count,"
        "toUInt8(isNaN(avg(tupleElement(s.point,2)))) AS raw_avg_is_nan,"
        "avgIf(tupleElement(s.point,2),"
        "reinterpretAsUInt64(tupleElement(s.point,2))!="
        +str(STALE_BITS)+") AS stale_aware_avg "
        "FROM (SELECT id,arrayJoin(samples) AS point FROM "+ss+") AS s "
        "INNER JOIN "+ts+" AS t ON s.id=t.id "
        "GROUP BY stage ORDER BY stage"
    )
    rows=[json.loads(x) for x in sql(q+" FORMAT JSONEachRow").splitlines() if x.strip()]
    values={x["stage"]:x for x in rows}
    if set(values)!={"stale","duplicate"}:
        raise AssertionError(("Missing expected distinct stage groups",values))
    return {"query":q,"rows":values}

def native():
    expr="avg by(stage)(avg_over_time(tslab_r07_semantic[1h]))"
    q=(f"SELECT * FROM prometheusQueryRange({TABLE},{sql_string(expr)},"
       f"{END},{END},3600) SETTINGS max_threads=4,log_queries=1 "
       "FORMAT JSONEachRow")
    _,body=post("http://127.0.0.1:18191",q,f"r07semantic_native_{RUN}")
    parsed=normalize_any(body)
    return {"query":q,"rows":sorted([
        {"stage":dict(tags).get("stage"),"timestamp":t,"value":v}
        for (tags,t),v in parsed.items()
    ],key=lambda x:x["stage"])}

def main():
    p=argparse.ArgumentParser()
    p.add_argument("--output",required=True)
    args=p.parse_args()
    report={"question_id":"R07","status":"STARTED","source_sha":SOURCE_SHA,
            "binary_sha256":BINARY_SHA,"stale_bits":hex(STALE_BITS),
            "errors":[]}
    save(args.output,report)
    try:
        create()
        report["ingest"]=put()
        report["stored"]=source_data()
        report["native"]=native()
        stored=report["stored"]["rows"]
        assert stored["stale"]["physical_points"]==2,stored
        assert stored["stale"]["stale_count"]==1,stored
        assert stored["stale"]["raw_avg_is_nan"]==1,stored
        assert abs(stored["stale"]["stale_aware_avg"]-5.)<1e-10,stored
        assert stored["duplicate"]["physical_points"]==2,stored
        assert stored["duplicate"]["stale_count"]==0,stored
        assert stored["duplicate"]["raw_avg_is_nan"]==0,stored
        assert abs(stored["duplicate"]["stale_aware_avg"]-5.)<1e-10,stored
        outputs={x["stage"]:x for x in report["native"]["rows"]}
        if set(outputs)!={"stale","duplicate"}:
            raise AssertionError(("Wrong native output set",outputs))
        if any(x["timestamp"]!=END or abs(x["value"]-5.)>1e-10 for x in outputs.values()):
            raise AssertionError(("Unexpected native avg range semantics",outputs))
        report["passed"]=True
        report["status"]="COUNTEREXAMPLE_RAW_SQL_STALE_NAN_NEEDS_FILTER"
        report["conclusion"]=(
            "Native PromQL excludes stale marker exactly by Float64 bits. "
            "Original direct R07 SQL avg gives NaN; adding avgIf with this bit "
            "filter recovers native 5.0. The TimeSeries ingestion/merge path "
            "keeps max value at a duplicate timestamp; this fixture stores "
            "two points and returns avg 5.0."
        )
    except Exception as exc:
        report["passed"]=False
        report["status"]="FAILED_OR_UNSUPPORTED"
        report["errors"].append({"fatal":str(exc)[:2200],"trace":traceback.format_exc()[-5000:]})
    finally:
        save(args.output,report)
        print("R07_SEMANTIC_DONE",json.dumps({"passed":report.get("passed"),
            "status":report["status"],"errors":report["errors"],
            "stored":report.get("stored",{}).get("rows"),
            "native":report.get("native",{}).get("rows")},
            separators=(",",":"),sort_keys=True),flush=True)
    if not report["passed"]:raise SystemExit(1)

if __name__=="__main__":
    main()
