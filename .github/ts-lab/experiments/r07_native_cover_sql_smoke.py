#!/usr/bin/env python3
"""R07 SQL sidecar diagnostic for a static one-point-per-series fixture.

Not a native PromQL optimization. No C++ patch, no production writes.
"""
import argparse
import json
import pathlib
import statistics
import sys
import time
import traceback
sys.path[:0]=[".github/ts-lab/experiments",".github/ts-lab-perf",".github/ts-lab-bench",".github/ts-lab-cpu"]
from r15_native_partition_probe import DAYS,PER_DAY,METRIC,END,fixture,create,table,native,identity,stamp
from bench_granularity_v11 import sql,inner,streamed_insert,optimize_table,wait_no_merges
from bench_bucket_cxx_abca import post,normalize_any,assert_same,pct
from bench_filtered_selector_v4_sql import sql_string
from bench_native_multiclient_stock_20261103 import complete_logs,save

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


def run_abba(days,stage,oracle):
    """Paired same-server native vs SQL diagnostics, output parity every request.

    This is NOT proof of safe native planner integration or write-path gain.
    """
    matched="" if stage is None else '{stage="'+stage+'"}'
    statements={"native":native("day",days,matched),
                "sql":query_sidecar(days,stage)+" FORMAT JSONEachRow"}
    phases=[]
    for i,kind in enumerate(("native","sql","sql","native")):
        prefix="r07abba_"+str(os.getpid())+"_"+str(days)+"_"+str(stage)+"_"+kind+"_"+str(i)+"_"
        trials=[]
        for repeat in range(6):
            qid=prefix+str(repeat)
            ms,body=post("http://127.0.0.1:18191",statements[kind],qid)
            parsed=normalize_any(body) if kind=="native" else normalize_sql(body)
            assert_same(oracle,parsed,"R07",str((days,stage,kind,i,repeat)))
            trials.append({"id":qid,"client_ms":ms,"warmup":repeat<2})
        logs=complete_logs(prefix,{v["id"] for v in trials})
        for trial in trials:
            row=logs[trial["id"]]
            ev=row.get("ProfileEvents",{})
            trial.update({
                "cpu_ms":ev.get("OSCPUVirtualTimeMicroseconds",0)/1000,
                "marks":ev.get("SelectedMarks",0),
                "read_rows":row["read_rows"],"read_bytes":row["read_bytes"],
                "query_mem":row["memory_usage"],
            })
        m=trials[2:]
        med=lambda k:statistics.median(x[k] for x in m)
        phases.append({"kind":kind,"trials":trials,"summary":{
            "p50_ms":pct([x["client_ms"] for x in m],.5),
            "p95_ms":pct([x["client_ms"] for x in m],.95),
            "cpu_ms":med("cpu_ms"),"marks":med("marks"),
            "read_rows":med("read_rows"),"read_bytes":med("read_bytes"),
            "peak_memory_bytes":max(x["query_mem"] for x in m)}})
    a=[p["summary"] for p in phases if p["kind"]=="native"]
    b=[p["summary"] for p in phases if p["kind"]=="sql"]
    avg=lambda rows,k:statistics.mean(x[k] for x in rows)
    drift=abs(a[0]["p50_ms"]-a[1]["p50_ms"])/avg(a,"p50_ms")
    summary={"control_drift":drift,"valid_control":drift<=.25}
    for key in ("p50_ms","p95_ms","cpu_ms","marks","read_rows","read_bytes","peak_memory_bytes"):
        summary["native_"+key]=avg(a,key)
        summary["sql_"+key]=avg(b,key)
    summary["native_over_sql_p50_ratio"]=avg(a,"p50_ms")/avg(b,"p50_ms")
    return {"summary":summary,"phases":phases}

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
                "sidecar_client_ms_single_trial":ms,"native_points":len(baseline),"parity":parity,"abba":run_abba(days,stage,baseline)}
            save(a.output,result)
        result["passed"]=all(x["abba"]["summary"]["valid_control"]
                             for x in result["cases"].values())
        result["state"]="ORACLE_ABBA_PASS_SQL_NOT_NATIVE_PLANNER" if result["passed"] else "INVALID_CONTROL_DRIFT"
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
