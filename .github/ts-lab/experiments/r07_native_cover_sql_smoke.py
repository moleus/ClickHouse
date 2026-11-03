#!/usr/bin/env python3
"""R07 SQL sidecar diagnostic for a static one-point-per-series fixture.

Not a native PromQL optimization. No C++ patch, no production writes.
"""
import argparse
import json
import pathlib
import sys
import time
import traceback
sys.path[:0]=[".github/ts-lab/experiments",".github/ts-lab-perf",".github/ts-lab-bench",".github/ts-lab-cpu"]
from r15_native_partition_probe import DAYS,PER_DAY,METRIC,END,fixture,create,table,native,identity,stamp
from bench_granularity_v11 import sql,inner,streamed_insert,optimize_table,wait_no_merges
from bench_bucket_cxx_abca import post,normalize_any,assert_same
from bench_filtered_selector_v4_sql import sql_string
from bench_native_multiclient_stock_20261103 import save

COVER="tslab_r07_cover"

def build_cover():
    sql("CREATE TABLE "+COVER+" (day Date,id Tuple(UInt64,UUID),"
        "metric_name LowCardinality(String),stage String,project String) "
        "ENGINE=ReplacingMergeTree PARTITION BY day ORDER BY(day,metric_name,id)")
    samples=inner(table("day"),"samples")
    tags=inner(table("day"),"tags")
    insert=("INSERT INTO "+COVER+
        " SELECT toDate(toDateTime(s.bucket,'UTC')) AS day,s.id AS id,"
        "t.metric_name AS metric_name,t.tags['stage'] AS stage,"
        "t.tags['project'] AS project FROM "+samples+" AS s "
        "INNER JOIN "+tags+" AS t ON s.id=t.id")
    start=time.perf_counter()
    post("http://127.0.0.1:18191",insert)
    elapsed=time.perf_counter()-start
    sql("OPTIMIZE TABLE "+COVER+" FINAL")
    wait_no_merges()
    counts=json.loads(sql("SELECT count() AS n,uniqExact(id) AS ids,"
        "uniqExact(day) AS days FROM "+COVER+" FORMAT JSONEachRow").strip())
    if counts!={"n":DAYS*PER_DAY,"ids":DAYS*PER_DAY,"days":DAYS}:
        raise AssertionError(("sidecar count mismatch",counts))
    size=json.loads(sql("SELECT count() AS active_parts,sum(bytes_on_disk) AS bytes,"
        "sum(marks) AS marks FROM system.parts WHERE active AND table="+
        sql_string(COVER)+" AND database='default' FORMAT JSONEachRow").strip())
    return {"insert_sql":insert,"build_elapsed_s":elapsed,"counts":counts,"parts":size}

def query_sidecar(days,stage=None):
    begin=END-days*86400
    begin_day=__import__("datetime").datetime.utcfromtimestamp(begin).strftime("%Y-%m-%d")
    end_day=__import__("datetime").datetime.utcfromtimestamp(END).strftime("%Y-%m-%d")
    where=("day>=toDate('"+begin_day+"') AND day<=toDate('"+end_day+
           "') AND metric_name="+sql_string(METRIC))
    if stage is not None:where+=" AND stage="+sql_string(stage)
    covering="SELECT DISTINCT id,stage FROM "+COVER+" WHERE "+where
    ids="SELECT id FROM "+COVER+" WHERE "+where
    pieces=("SELECT id,avg(tupleElement(point,2)) AS val FROM "
        "(SELECT id,arrayJoin(samples) AS point FROM "+inner(table("day"),"samples")+
        " WHERE bucket>=toDateTime64('"+stamp(begin//3600*3600)+"',3,'UTC')"
        " AND bucket<=toDateTime64('"+stamp(END)+"',3,'UTC')"
        " AND id IN ("+ids+"))"
        " WHERE tupleElement(point,1)>toDateTime64('"+stamp(begin)+"',3,'UTC')"
        " AND tupleElement(point,1)<=toDateTime64('"+stamp(END)+"',3,'UTC') GROUP BY id")
    return ("SELECT c.stage AS stage,avg(s.val) AS value FROM ("+pieces+
        ") AS s INNER JOIN ("+covering+") AS c ON s.id=c.id "
        "GROUP BY stage ORDER BY stage")

def normalize_sql(body):
    rows=[json.loads(x) for x in body.splitlines() if x.strip()]
    out={}
    for row in rows:
        key=((("stage",str(row["stage"])),),END)
        if key in out:raise AssertionError("duplicate stage SQL")
        out[key]=float(row["value"])
    return out

def main():
    p=argparse.ArgumentParser()
    p.add_argument("--fixture",required=True)
    p.add_argument("--output",required=True)
    a=p.parse_args()
    result={"state":"STARTED","status":"R07_SQL_DIAGNOSTIC_ONLY","cases":{}}
    save(a.output,result)
    try:
        result["fixture"]=fixture(a.fixture)
        result["ddl"]=create("day")
        ingest=streamed_insert(table("day"),a.fixture)
        if ingest["rows"]!=DAYS*PER_DAY:raise AssertionError("incomplete native ingest")
        result["ingest"]=ingest
        result["optimized"]=optimize_table(table("day"))
        result["source_physical"]=identity("day")
        result["cover"]=build_cover()
        for days,stage in ((1,None),(1,"stage-0001"),(3,None),(14,None)):
            selector="" if stage is None else '{stage="'+stage+'"}'
            _,raw=post("http://127.0.0.1:18191",native("day",days,selector))
            baseline=normalize_any(raw)
            if len(baseline)!=(38 if stage is None else 1):
                raise AssertionError("wrong nonempty native result")
            q=query_sidecar(days,stage)
            explain=sql("EXPLAIN indexes=1 "+q)
            begun=time.perf_counter()
            _,other=post("http://127.0.0.1:18191",q+" FORMAT JSONEachRow")
            ms=(time.perf_counter()-begun)*1000
            parity=assert_same(baseline,normalize_sql(other),days,stage)
            result["cases"][str(days)+"d"+(stage or "all")]={"sql":q,"explain":explain,
                "sidecar_client_ms_single_trial":ms,"native_points":len(baseline),"parity":parity}
            save(a.output,result)
        result["passed"]=True
        result["state"]="ORACLE_PASS_SINGLE_TRIAL_NO_ABBA"
    except Exception as exc:
        result["passed"]=False
        result["state"]="FAILED"
        result["error"]=str(exc)
        result["traceback"]=traceback.format_exc()[-4500:]
    finally:
        save(a.output,result)
        print("R07_SIDECAR_DIAGNOSTIC",json.dumps({"passed":result.get("passed"),
            "state":result["state"],"error":result.get("error"),
            "case_keys":list(result["cases"]),"cover":result.get("cover")},separators=(",",":")),flush=True)
    if not result["passed"]:raise SystemExit(1)

if __name__=="__main__":
    main()
