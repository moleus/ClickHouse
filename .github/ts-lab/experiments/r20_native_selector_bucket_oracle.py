#!/usr/bin/env python3
"""R20: source-pinned native timeSeriesSelector's ID/tag context + bucket sums.

Proves a possible safe C++ integration source rather than directly reading
inner TAGS/SAMPLES without TimeRanges. Tiny adversarial 5-tick oracle, no
new index, compilation, or performance claims.
"""
import argparse
import json
import math
import os
import pathlib
import sys
import traceback

sys.path[:0]=[".github/ts-lab/experiments",".github/ts-lab-perf",
            ".github/ts-lab-bench",".github/ts-lab-cpu"]
import r07_stale_safe_grid_oracle as oracle
from bench_granularity_v11 import sql
from bench_bucket_cxx_abca import post
from bench_filtered_selector_v4_sql import sql_string
from bench_native_multiclient_stock_20261103 import save

HOST="http://127.0.0.1:18191"
RUN=os.getenv("GITHUB_RUN_ID","local")
SOURCE_SHA="52529f0c711566d696917678bf15e7068772fdde"
BINARY_SHA="f0a2fa1f814c58853f43423705cac10ab9df3bfe5887e27ebe9620ba7cb9660e"

def selector_sql(tick):
    start=tick-oracle.WINDOW
    # Window (start,end] with millisecond table sample timestamps.
    minimum="toDateTime64("+sql_string(oracle.timestamp(start))+",3,'UTC') + toIntervalMillisecond(1)"
    maximum="toDateTime64("+sql_string(oracle.timestamp(tick))+",3,'UTC')"
    selector=(
        "timeSeriesSelector('default',"+sql_string(oracle.TABLE)+","+
        sql_string(oracle.METRIC)+","+minimum+","+maximum+",'samples')"
    )
    src=(
        "SELECT id,"
        "timeSeriesRemoveAllTagsExcept(timeSeriesIdToGroup(id),['stage']) AS grp,"
        "arrayFilter(p -> reinterpretAsUInt64(tupleElement(p,2)) != "+
        str(oracle.STALE_BITS)+",samples) AS filtered "
        "FROM "+selector
    )
    per_id=(
        "SELECT id,grp,sum(arraySum(arrayMap(p -> tupleElement(p,2),filtered))) "
        "/sum(length(filtered)) AS avg_value "
        "FROM ("+src+") GROUP BY id,grp HAVING sum(length(filtered))>0"
    )
    q=(
        "SELECT timeSeriesGroupToTags(grp) AS tags,"
        "toUnixTimestamp("+maximum+") AS tick,"
        "toUInt8(isNaN(avg(avg_value))) AS is_nan,"
        "if(isNaN(avg(avg_value)),toFloat64(0),avg(avg_value)) AS finite_value "
        "FROM ("+per_id+") GROUP BY grp ORDER BY grp "
        "SETTINGS max_threads=4,log_queries=1 FORMAT JSONEachRow"
    )
    return q

def normalize(raw):
    out={}
    for line in raw.splitlines():
        if not line.strip():continue
        row=json.loads(line)
        stage=dict(row["tags"]).get("stage")
        if stage is None:raise AssertionError(("Missing stage in collector group",row))
        k=(str(stage),int(row["tick"]))
        if k in out:raise AssertionError(("Duplicate group/tick",k))
        out[k]={"nan":bool(row["is_nan"]),"value":float(row["finite_value"])}
    return out

def main():
    ap=argparse.ArgumentParser()
    ap.add_argument("--output",required=True)
    args=ap.parse_args()
    report={"question_id":"R20","state":"STARTED","passed":False,
            "source_sha":SOURCE_SHA,"binary_sha256":BINARY_SHA,
            "errors":[],"by_tick":{}}
    save(args.output,report)
    try:
        report["fixture"]=oracle.load_fixture()
        report["stored_bits"]=oracle.stored_bits()
        native_q=oracle.native_query()
        _,raw=post(HOST,native_q,f"r20_sel_native_{RUN}")
        reference=oracle.normalize_native(raw)
        report["native_query"]=native_q
        result={}
        for t in oracle.TICKS:
            query=selector_sql(t)
            _,raw=post(HOST,query,f"r20_sel_bucket_{RUN}_{t}")
            rows=normalize(raw)
            if any(t2!=t for (_stage,t2) in rows):
                raise AssertionError(("Selector returned wrong tick",t,rows))
            for k,v in rows.items():
                if k in result:raise AssertionError(("Duplicate tick",k))
                result[k]=v
            report["by_tick"][str(t)]={"query":query,"values":rows.__str__(),
                "row_count":len(rows)}
            save(args.output,report)
        report["native_results"]={str(k):v for k,v in reference.items()}
        report["selector_results"]={str(k):v for k,v in result.items()}
        report["parity_count"]=oracle.compare(reference,result,"source selector bucket path")
        if report["parity_count"]!=22:
            raise AssertionError(("Expected exact22 native results",report["parity_count"]))
        report["passed"]=True
        report["state"]="PASS_NATIVE_TIMESERIES_SELECTOR_TAG_COLLECTOR_GRID_PARITY"
    except Exception as exc:
        report["state"]="FAILED"
        report["errors"].append({"error":str(exc)[:2400],"trace":traceback.format_exc()[-5000:]})
    finally:
        save(args.output,report)
        compact={"passed":report["passed"],"state":report["state"],
                 "errors":report["errors"],"points":report.get("parity_count"),
                 "by_tick":{k:v["row_count"] for k,v in report["by_tick"].items()},
                 "physical":report.get("fixture",{}).get("physical_counts")}
        save(str(pathlib.Path(args.output).with_suffix(".compact.json")),compact)
        print("R20_NATIVE_SELECTOR_DONE",json.dumps(compact,separators=(",",":")),flush=True)
    if not report["passed"]:raise SystemExit(1)

if __name__=="__main__":
    main()
