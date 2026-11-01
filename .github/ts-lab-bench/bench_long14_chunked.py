#!/usr/bin/env python3
"""Test bounded evaluation windows against native ClickHouse PromQL on long14.

This SQL prototype is not a general PromQL engine. It is restricted to
sum(rate(one exact metric[5m])) and one TimeSeries sample table. Never
deploy it to production without dedicated C++ implementation and tests.
"""
import argparse
import datetime as dt
import json
import math
import pathlib
import random
import statistics
import time
import urllib.error
import urllib.parse
import urllib.request


def call(root, statement, timeout=180):
    url=root.rstrip("/")+"/?"+urllib.parse.urlencode({
        "max_memory_usage":1200000000,
        "max_bytes_before_external_group_by":250000000,
        "max_threads":4,
        "max_execution_time":350,
        "wait_end_of_query":1})
    start=time.perf_counter()
    req=urllib.request.Request(url,data=statement.encode("utf-8"),method="POST")
    try:
        with urllib.request.urlopen(req,timeout=timeout) as res:
            payload=res.read().decode("utf-8").strip()
    except urllib.error.HTTPError as ex:
        msg=ex.read().decode("utf-8","replace")
        raise RuntimeError(f"SQL HTTP {ex.code}: {msg[:2000]}; statement={statement[:1100]}") from ex
    return (time.perf_counter()-start)*1000,payload


def pct(vals,ratio):
    a=sorted(vals)
    k=(len(a)-1)*ratio
    lo=int(k)
    return round(a[lo]+(a[min(lo+1,len(a)-1)]-a[lo])*(k-lo),3)


def parse_timestamp(value):
    if isinstance(value,(int,float)):
        return int(value)
    return int(dt.datetime.fromisoformat(value.replace(" ","T")).replace(tzinfo=dt.timezone.utc).timestamp())


def main():
    parser=argparse.ArgumentParser()
    parser.add_argument("--manifest",required=True)
    parser.add_argument("--table",default="long_ts")
    parser.add_argument("--ch",default="http://127.0.0.1:18191")
    parser.add_argument("--output",required=True)
    parser.add_argument("--widths",default="60,180,720")
    parser.add_argument("--repeats",type=int,default=3)
    parser.add_argument("--warmups",type=int,default=1)
    args=parser.parse_args()
    m=json.loads(pathlib.Path(args.manifest).read_text())
    start=int(m["query_start_epoch"])
    end=int(m["query_end_epoch"])
    step=60
    window=300
    n=((end-start)//step)+1
    expected_timestamps=[start+i*step for i in range(n)]
    _,value=call(args.ch,"SELECT name FROM system.tables WHERE database='default' AND startsWith(name,'.inner_id.samples.') FORMAT TSVRaw")
    tables=value.splitlines()
    if len(tables)!=1:
        raise AssertionError(("inner tables",tables))
    name=tables[0]
    qualified="default."+chr(96)+name.replace(chr(96),chr(96)*2)+chr(96)
    _,t=call(args.ch,"SELECT uniqExact(id),sum(length(samples)),count() FROM "+qualified)
    raw_series,raw_samples,stored_rows=[int(x) for x in t.split()]
    assert raw_series==m["series"] and raw_samples==m["samples"],(raw_series,raw_samples,m["series"],m["samples"])
    metric=m["metric"]
    native_sql=("SELECT * FROM prometheusQueryRange("+args.table+", 'sum(rate("+metric+
                "[5m]))', "+str(start)+","+str(end)+",60) FORMAT JSONEachRow")
    def native():
        ms,raw=call(args.ch,native_sql,timeout=370)
        rows=[json.loads(x) for x in raw.splitlines() if x.strip()]
        assert len(rows)==1 and rows[0].get("tags")==[],(len(rows),raw[:300])
        output={parse_timestamp(ts):float(v) for ts,v in rows[0]["samples"]}
        assert set(output)==set(expected_timestamps),(len(output),len(expected_timestamps))
        return ms,[output[t] for t in expected_timestamps]
    _,canonical=native()

    widths=[int(x) for x in args.widths.split(",")]
    assert all(x>0 for x in widths)
    def window_query(begin_epoch,end_epoch):
        b=begin_epoch*1000
        e=end_epoch*1000
        rate=("timeSeriesRateToGrid(fromUnixTimestamp64Milli("+str(b)+
              "),fromUnixTimestamp64Milli("+str(e)+
              "),toIntervalSecond("+str(step)+
              "),toIntervalSecond("+str(window)+"))(samples)")
        selected=("SELECT "+rate+" AS grid FROM "+qualified+
                  " WHERE min_time<=fromUnixTimestamp64Milli("+str(e)+")"+
                  " AND max_time>=fromUnixTimestamp64Milli("+str(b-window*1000)+")"+
                  " GROUP BY id")
        return "SELECT sumForEach(grid) AS values FROM ("+selected+") FORMAT JSONEachRow"
    counters={}
    query_samples=[]
    def bounded(width):
        values=[]
        duration=0.0
        qcount=0
        for offset in range(0,n,width):
            size=min(width,n-offset)
            left=start+offset*step
            right=left+(size-1)*step
            q=window_query(left,right)
            ms,raw=call(args.ch,q,timeout=200)
            rows=[json.loads(s) for s in raw.splitlines() if s.strip()]
            if len(rows)!=1 or len(rows[0].get("values",[]))!=size:
                raise AssertionError((offset,width,size,raw[:500]))
            chunk=rows[0]["values"]
            values.extend([float(x) if x is not None else None for x in chunk])
            duration+=ms
            qcount+=1
            if qcount==1:
                query_samples.append({"width":width,"first_query":q,"first_query_ms":ms,
                                      "first_chunk":chunk[:8]})
        return round(duration,3),values,qcount

    keys=["native"]+["bounded_"+str(w) for w in widths]
    timings={k:[] for k in keys}
    equivalence={}
    def execute(key,record):
        if key=="native":
            total,values=native()
            calls=1
        else:
            width=int(key.split("_")[1])
            total,values,calls=bounded(width)
        if len(values)!=len(canonical):
            raise AssertionError(("size",key,len(values),len(canonical)))
        max_rel=0.0
        mismatch_count=0
        first=[]
        for i,(a,b) in enumerate(zip(canonical,values)):
            if a is None or b is None:
                if a is not b:
                    mismatch_count+=1
                    if len(first)<5:first.append([expected_timestamps[i],a,b])
                continue
            diff=abs(a-b)
            max_rel=max(max_rel,diff/max(1e-12,abs(a)))
            if not math.isclose(a,b,rel_tol=1e-10,abs_tol=1e-8):
                mismatch_count+=1
                if len(first)<5:first.append([expected_timestamps[i],a,b])
        equivalence[key]={"mismatch_count":mismatch_count,"max_relative":max_rel,"first_mismatches":first,
                          "http_requests":calls}
        if mismatch_count:
            raise AssertionError((key,mismatch_count,first))
        if record:
            timings[key].append(total)
        return total
    for key in keys:
        for _ in range(args.warmups):
            execute(key,False)
    rng=random.Random(501134)
    for iteration in range(args.repeats):
        order=list(keys)
        rng.shuffle(order)
        for key in order:
            ms=execute(key,True)
            print("CHUNK_TRIAL",iteration,key,"wall_ms",ms,flush=True)
    stats={key:{"p50_ms":pct(vals,.5),"p95_ms":pct(vals,.95),
                "p99_ms":pct(vals,.99),"individual_ms":vals,
                "speedup_vs_native_p50":round(pct(timings["native"],.5)/pct(vals,.5),3)}
           for key,vals in timings.items()}
    result={"benchmark":"long14 bounded grid sum(rate())","source":"raw inner table SQL",
            "semantics":"SQL groups by id and sums grid per window; not general PromQL",
            "manifest":m,"stored_series":raw_series,"stored_samples":raw_samples,"stored_rows":stored_rows,
            "baseline_binary":"use workflow provenance", "query_start_epoch":start,
            "evaluation_points":n,"time_range_step_seconds":step,
            "max_memory_usage_bytes":1200000000,
            "warmups":args.warmups,"repeats":args.repeats,"stats":stats,
            "equivalence_vs_native":equivalence,"first_query_samples":query_samples}
    output=pathlib.Path(args.output)
    output.parent.mkdir(parents=True,exist_ok=True)
    output.write_text(json.dumps(result,indent=2)+"\n")
    print("BOUNDED_RESULT",json.dumps({"points":n,"stats":stats,"equivalence":equivalence},indent=2),flush=True)


if __name__=="__main__":
    main()
