#!/usr/bin/env python3
"""R07: source-pinned native 5-tick correctness of per-bucket sum/count rewrite.

The candidate reduces 2D TimeSeries bucket arrays to (sum,count) before
per-full-ID grouping, without expanding samples via arrayJoin. Uses existing
TAGS/SAMPLES only. No native planner change or performance claim.
"""
import argparse
import json
import pathlib
import sys
import traceback

sys.path[:0]=[".github/ts-lab/experiments",".github/ts-lab-perf",
            ".github/ts-lab-bench",".github/ts-lab-cpu"]
import r07_stale_safe_grid_oracle as oracle
from bench_granularity_v11 import sql,inner
from bench_bucket_cxx_abca import post
from bench_filtered_selector_v4_sql import sql_string
from bench_native_multiclient_stock_20261103 import save

HOST="http://127.0.0.1:18191"

def bucket_sql():
    samples=inner(oracle.TABLE,"samples")
    tags=inner(oracle.TABLE,"tags")
    tick_expr="SELECT arrayJoin(["+(",".join(
        "toDateTime64("+sql_string(oracle.timestamp(t))+",3,'UTC')"
        for t in oracle.TICKS))+"]) AS eval"
    source=(
        "SELECT s.id AS id,e.eval AS eval,"
        "arrayFilter(p -> tupleElement(p,1)>e.eval-INTERVAL 120 SECOND "
        "AND tupleElement(p,1)<=e.eval "
        "AND reinterpretAsUInt64(tupleElement(p,2))!="
        +str(oracle.STALE_BITS)+",s.samples) AS filtered "
        "FROM "+samples+" AS s CROSS JOIN ("+tick_expr+") AS e"
    )
    by_id=(
        "SELECT id,eval,"
        "sum(arraySum(arrayMap(p -> tupleElement(p,2),filtered))) "
        "/ sum(length(filtered)) AS avg_value "
        "FROM ("+source+") GROUP BY id,eval "
        "HAVING sum(length(filtered))>0"
    )
    return (
        "SELECT t.tags['stage'] AS stage,toUnixTimestamp(i.eval) AS tick,"
        "toUInt8(isNaN(avg(i.avg_value))) AS is_nan,"
        "if(isNaN(avg(i.avg_value)),toFloat64(0),avg(i.avg_value)) AS finite_value "
        "FROM ("+by_id+") AS i INNER JOIN "+tags+" AS t ON i.id=t.id "
        "WHERE t.metric_name="+sql_string(oracle.METRIC)+
        " GROUP BY stage,i.eval ORDER BY stage,tick FORMAT JSONEachRow"
    )

def main():
    ap=argparse.ArgumentParser()
    ap.add_argument("--output",required=True)
    args=ap.parse_args()
    report={"question_id":"R07","passed":False,"status":"STARTED","errors":[],
            "source_sha":"52529f0c711566d696917678bf15e7068772fdde",
            "stock_o1_binary_sha256":"f0a2fa1f814c58853f43423705cac10ab9df3bfe5887e27ebe9620ba7cb9660e",
            "method":"per-bucket arrayFilter->arraySum + length, then sum(sum)/sum(count) by full ID, then label avg"}
    save(args.output,report)
    try:
        report["fixture"]=oracle.load_fixture()
        report["source_bits"]=oracle.stored_bits()
        native_q=oracle.native_query()
        candidate_q=bucket_sql()
        baseline_q=oracle.sql_query(safe=True)
        _,native_raw=post(HOST,native_q,"r07_bucket_oracle_native")
        _,safe_raw=post(HOST,baseline_q,"r07_bucket_oracle_arrayjoin")
        _,candidate_raw=post(HOST,candidate_q,"r07_bucket_oracle_reduce")
        nat=oracle.normalize_native(native_raw)
        arr=oracle.normalize_sql(safe_raw)
        by_bucket=oracle.normalize_sql(candidate_raw)
        report["queries"]={"native":native_q,"arrayjoin":baseline_q,
                           "bucket_sum_count":candidate_q}
        report["native_points"]={str(k):v for k,v in nat.items()}
        report["arrayjoin_points"]={str(k):v for k,v in arr.items()}
        report["bucket_points"]={str(k):v for k,v in by_bucket.items()}
        report["arrayjoin_parity"]=oracle.compare(nat,arr,"native vs point SQL")
        report["bucket_parity"]=oracle.compare(nat,by_bucket,"native vs bucket SQL")
        if report["bucket_parity"]!=22:
            raise AssertionError(("Expected 22 fixed-stale grid points",report["bucket_parity"]))
        report["passed"]=True
        report["status"]="PASS_5TICK_BUCKET_SUMCOUNT_NATIVE_SEMANTICS"
    except Exception as exc:
        report["status"]="FAILED"
        report["errors"].append({"fatal":str(exc)[:1800],"trace":traceback.format_exc()[-4600:]})
    finally:
        save(args.output,report)
        compact={"passed":report["passed"],"status":report["status"],
                 "errors":report["errors"],"native_points":report.get("bucket_parity"),
                 "source_points":report.get("fixture",{}).get("physical_counts"),
                 "method":report["method"]}
        save(str(pathlib.Path(args.output).with_suffix(".compact.json")),compact)
        print("R07_BUCKET_ORACLE_DONE",json.dumps(compact,separators=(",",":")),flush=True)
    if not report["passed"]:raise SystemExit(1)

if __name__=="__main__":
    main()
