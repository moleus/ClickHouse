#!/usr/bin/env python3
"""R15: stock ClickHouse native SAMPLES partition experiment, no C++ build.

Same v11, raw UUID, 1-hour buckets, no RecentSamples, identical synthetic
14-day one-point-series workload. Only SAMPLES partitioning changes.
"""
import argparse
import datetime as dt
import hashlib
import json
import os
import pathlib
import statistics
import sys
import time
import traceback

sys.path[:0]=[".github/ts-lab-perf",".github/ts-lab-bench",".github/ts-lab-cpu"]
from bench_granularity_v11 import sql,inner,streamed_insert,optimize_table,wait_no_merges
from bench_bucket_cxx_abca import post,normalize_any,assert_same,pct
from bench_filtered_selector_v4_sql import sql_string
from bench_native_multiclient_stock_20261103 import complete_logs,physical,save

DAY=86400
BASE=int(dt.datetime(2026,8,4,tzinfo=dt.timezone.utc).timestamp())
END=BASE+13*DAY+3600
DAYS=14
PER_DAY=5000
METRIC="tslab_r15_stage_seconds"
RUN=os.getenv("GITHUB_RUN_ID","local")
HOST="http://127.0.0.1:18191"
PART={"month":"toYYYYMM(toDateTime(bucket,'UTC'))",
      "day":"toDate(toDateTime(bucket,'UTC'))"}

def table(v):
    return "tslab_r15_"+v

def stamp(t):
    return dt.datetime.fromtimestamp(t,dt.timezone.utc).strftime("%Y-%m-%d %H:%M:%S.000")

def fixture(path):
    h=hashlib.sha256()
    with open(path,"wb") as f:
        for d in range(DAYS):
            for i in range(PER_DAY):
                row={"metric_name":METRIC,
                    "tags":{"stage":f"stage-{i%38:04d}","job":f"job-{i%10:02d}",
                            "project":f"project-{i%5:02d}","branch":f"branch-{i%20:03d}",
                            "build":f"build-{d:02d}-{i:05d}"},
                    "samples":[[stamp(BASE+d*DAY+3600),float((d*97+i%1000)/10)]]}
                data=(json.dumps(row,sort_keys=True,separators=(",",":"))+"\n").encode()
                h.update(data)
                f.write(data)
    return {"sha256":h.hexdigest(),"rows":PER_DAY*DAYS,"days":DAYS,
            "one_sample_per_series":True}

def create(v):
    stmt=(f"CREATE TABLE {table(v)} ENGINE=TimeSeries "
        "SETTINGS version=11,recent_samples_ttl_seconds=0,samples_bucket_step_seconds=3600 "
        "SAMPLES INNER COLUMNS (id Tuple(UInt64,UUID)) "
        f"SAMPLES INNER ENGINE = AggregatingMergeTree PARTITION BY {PART[v]} ORDER BY (id,bucket) "
        "TAGS INNER COLUMNS (id Tuple(UInt64,UUID) DEFAULT tuple(sipHash64(metric_name),"
        "reinterpretAsUUID(sipHash128(tags))),metric_name LowCardinality(String),"
        "tags Map(LowCardinality(String),String),"
        "INDEX tags_idx tags TYPE text(tokenizer = 'keyValuePairs') GRANULARITY 100000000) "
        "TIME RANGES INNER COLUMNS (id Tuple(UInt64,UUID))")
    sql(stmt)
    actual=sql("SHOW CREATE TABLE "+inner(table(v),"samples")+" FORMAT TSVRaw")
    compact="".join(actual.lower().split())
    if "orderby(id,bucket)" not in compact:
        raise AssertionError(("Unexpected sample key",v,actual))
    if (v=="month" and "toyyyymm(" not in compact) or (v=="day" and "partitionbytodate(" not in compact):
        raise AssertionError(("Partition key mismatch",v,actual))
    return {"declared":stmt,"normalized":actual}

def partition_stats(v):
    key=inner(table(v),"samples").split(chr(96))[1]
    return [json.loads(x) for x in sql(
        "SELECT partition,sum(rows) AS rows,sum(marks) AS marks,"
        "sum(bytes_on_disk) AS bytes,count() AS parts FROM system.parts "
        "WHERE active AND database='default' AND table="+sql_string(key)+
        " GROUP BY partition ORDER BY partition FORMAT JSONEachRow").splitlines() if x.strip()]

def identity(v):
    return {"tags":json.loads(sql(
        "SELECT count() AS n,groupBitXor(sipHash64(toString(id))) AS digest "
        "FROM "+inner(table(v),"tags")+" FORMAT JSONEachRow").strip()),
        "samples":json.loads(sql(
        "SELECT count() AS n,sum(length(samples)) AS points "
        "FROM "+inner(table(v),"samples")+" FORMAT JSONEachRow").strip()),
        "partition_stats":partition_stats(v),"physical":physical(table(v))}

def direct(v,days):
    stmt=(f"SELECT sum(length(samples)) AS n FROM {inner(table(v),'samples')} "
        f"WHERE bucket > toDateTime64('{stamp(END-days*DAY)}',3,'UTC') "
        f"AND bucket <= toDateTime64('{stamp(END)}',3,'UTC')")
    plan=sql("EXPLAIN indexes=1 "+stmt)
    qid=f"r15direct_{RUN}_{v}_{days}"
    _,body=post(HOST,stmt+" FORMAT JSONEachRow",qid)
    n=int(json.loads(body.strip())["n"])
    row=complete_logs(qid,{qid})[qid]
    ev=row.get("ProfileEvents",{})
    if n!=days*PER_DAY:raise AssertionError(("Wrong points",v,days,n))
    return {"points":n,"plan":plan,"selected_marks":ev.get("SelectedMarks"),
        "read_rows":row["read_rows"],"read_bytes":row["read_bytes"],
        "compressed_read_bytes":ev.get("ReadCompressedBytes"),
        "cpu_ms":ev.get("OSCPUVirtualTimeMicroseconds",0)/1000}

def native(v,days,selector):
    expr=f"avg by(stage)(avg_over_time({METRIC}{selector}[{days}d]))"
    return (f"SELECT * FROM prometheusQueryRange({table(v)},{sql_string(expr)},"
        f"{END},{END},86400) SETTINGS max_threads=4,max_memory_usage=1800000000,"
        "log_queries=1 FORMAT JSONEachRow")

def phase(v,days,selector,order,oracle):
    q=native(v,days,selector)
    prefix=f"r15native_{RUN}_{days}_{len(selector)}_{order}_{v}_"
    data=[]
    for i in range(6):
        qid=prefix+str(i)
        ms,body=post(HOST,q,qid)
        assert_same(oracle,normalize_any(body),str(days),v)
        data.append({"qid":qid,"client_ms":ms,"warmup":i<2})
    logs=complete_logs(prefix,{x["qid"] for x in data})
    for x in data:
        row=logs[x["qid"]]
        ev=row.get("ProfileEvents",{})
        x.update({"cpu_ms":ev.get("OSCPUVirtualTimeMicroseconds",0)/1000,
            "selected_marks":ev.get("SelectedMarks",0),
            "read_rows":row["read_rows"],"read_bytes":row["read_bytes"],
            "memory":row["memory_usage"]})
    chosen=data[2:]
    med=lambda key:statistics.median(x[key] for x in chosen)
    return {"variant":v,"phase":order,"trials":data,"summary":{
        "p50_ms":pct([x["client_ms"] for x in chosen],.5),
        "p95_ms":pct([x["client_ms"] for x in chosen],.95),
        "cpu_ms":med("cpu_ms"),"selected_marks":med("selected_marks"),
        "read_rows":med("read_rows"),"read_bytes":med("read_bytes"),
        "query_memory_max":max(x["memory"] for x in chosen)}}

def compare(days,selector):
    _,body=post(HOST,native("month",days,selector),f"r15oracle_{RUN}_{days}_{len(selector)}")
    oracle=normalize_any(body)
    expected=1 if selector.startswith('{stage=') else 38
    if len(oracle)!=expected:
        raise AssertionError(("Empty or wrong output",days,selector,len(oracle),expected))
    phases=[phase(v,days,selector,i,oracle) for i,v in enumerate(("month","day","day","month"))]
    a=[x["summary"] for x in phases if x["variant"]=="month"]
    b=[x["summary"] for x in phases if x["variant"]=="day"]
    avg=lambda rows,key:statistics.mean(x[key] for x in rows)
    drift=abs(a[0]["p50_ms"]-a[1]["p50_ms"])/avg(a,"p50_ms")
    summary={"control_drift":drift,"valid":drift<=.25}
    for key in ("p50_ms","cpu_ms","selected_marks","read_rows","read_bytes"):
        summary["month_"+key]=avg(a,key)
        summary["day_"+key]=avg(b,key)
    summary["wall_speedup_month_over_day"]=avg(a,"p50_ms")/avg(b,"p50_ms")
    return {"case":{"days":days,"selector":selector},"phases":phases,"summary":summary}

def main():
    p=argparse.ArgumentParser()
    p.add_argument("--fixture",required=True)
    p.add_argument("--output",required=True)
    p.add_argument("--smoke-only",action="store_true")
    a=p.parse_args()
    report={"question_id":"R15","status":"STARTED","errors":[],"direct":{},"native":{}}
    save(a.output,report)
    try:
        report["fixture"]=fixture(a.fixture)
        report["tables"]={}
        for v in ("month","day"):
            created=create(v)
            start=time.perf_counter()
            inserted=streamed_insert(table(v),a.fixture)
            inserted["wall_s"]=time.perf_counter()-start
            if inserted["rows"]!=DAYS*PER_DAY:
                raise AssertionError(("Bad ingest",v,inserted))
            optimized=optimize_table(table(v))
            if any(isinstance(x,dict) for x in optimized.values()):
                raise AssertionError(("Incomplete OPTIMIZE",v,optimized))
            report["tables"][v]={"ddl":created,"ingest":inserted,"optimize":optimized}
            save(a.output,report)
        wait_no_merges()
        report["physical"]={v:identity(v) for v in ("month","day")}
        for v,item in report["physical"].items():
            if int(item["samples"]["points"])!=DAYS*PER_DAY or int(item["tags"]["n"])!=DAYS*PER_DAY:
                raise AssertionError(("Bad physical counts",v,item))
            if len(item["partition_stats"])!=(1 if v=="month" else DAYS):
                raise AssertionError(("Wrong partition count",v,item["partition_stats"]))
        if report["physical"]["month"]["tags"]!=report["physical"]["day"]["tags"]:
            raise AssertionError("Logical ID digests differ")
        save(a.output,report)
        for days in (1,3,7,14):
            report["direct"][str(days)]={v:direct(v,days) for v in ("month","day")}
            save(a.output,report)
        if not a.smoke_only:
            for days,sel in [(1,""),(3,""),(7,""),(14,""),
                             (1,'{stage="stage-0001"}'),(14,'{stage="stage-0001"}')]:
                key=f"{days}d"+("-stage" if sel else "-all")
                report["native"][key]=compare(days,sel)
                save(a.output,report)
        report["passed"]=all(x["summary"]["valid"] for x in report["native"].values())
        report["status"]="PASS" if report["passed"] else "INVALID_CONTROL"
    except Exception as exc:
        report["passed"]=False
        report["status"]="FAILED"
        report["errors"].append({"exception":str(exc)[:2400],"trace":traceback.format_exc()[-4000:]})
    finally:
        save(a.output,report)
        summary={"passed":report.get("passed"),"status":report["status"],
            "errors":report["errors"],"fixture":report.get("fixture"),
            "physical":report.get("physical"),"direct":report["direct"],
            "native":{k:v["summary"] for k,v in report["native"].items()}}
        save(str(pathlib.Path(a.output).with_suffix(".compact.json")),summary)
        print("R15_NATIVE_DONE",json.dumps(summary,separators=(",",":")),flush=True)
    if not report["passed"]:raise SystemExit(1)

if __name__=="__main__":main()
