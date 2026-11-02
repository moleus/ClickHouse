#!/usr/bin/env python3
"""SQL-only native 1-point window aggregation proof with compensated values."""
import argparse,json,math,pathlib,statistics,time,urllib.error,urllib.request

def post(query):
    req=urllib.request.Request("http://127.0.0.1:18191/",data=query.encode("utf-8"))
    began=time.perf_counter()
    try:
        with urllib.request.urlopen(req,timeout=120) as resp: raw=resp.read()
    except urllib.error.HTTPError as e:
        body=e.read().decode("utf-8","replace")
        print("NATIVE_ONE_POINT_SQL_ERROR",json.dumps({"sql":query,"status":e.code,"body":body[:8000]}),flush=True)
        raise
    return (time.perf_counter()-began)*1000,raw

def parse(raw):
    res={}
    for line in raw.splitlines():
        if not line.strip():continue
        record=json.loads(line)
        label=record["tags"]
        tags=tuple(sorted(label.items())) if isinstance(label,dict) else tuple(sorted(tuple(x) for x in label))
        for ts,v in record["samples"]:
            key=(tags,str(ts))
            assert key not in res
            res[key]=float(v)
    return res

def compare(reference,actual):
    result={"missing":list(map(repr,set(reference)-set(actual))),
            "extra":list(map(repr,set(actual)-set(reference))),"changed":[]}
    for key in sorted(set(reference)&set(actual)):
        a,b=reference[key],actual[key]
        if math.isnan(a) and math.isnan(b):continue
        if not math.isclose(a,b,rel_tol=1e-10,abs_tol=1e-8):
            result["changed"].append({"tags":list(key[0]),"timestamp":key[1],"reference":a,"actual":b})
    return result

def translated_query(start,end,step,table='precision_ts',metric='tslab_numeric_precision'):
    start_ms=start*1000
    end_ms=end*1000
    step_ms=step*1000
    last_ms=start_ms+((end_ms-start_ms)//step_ms)*step_ms
    n=(last_ms-start_ms)//step_ms+1
    window_ms=86400000
    return f"""
    WITH
    src AS (
      SELECT id AS series_id, arrayJoin(samples) AS sample
      FROM timeSeriesSelector('default','{table}','{metric}',
                             fromUnixTimestamp64Milli({start_ms-window_ms+1}),
                             fromUnixTimestamp64Milli({last_ms}),'samples')
    ), flat AS (
      SELECT series_id,toUnixTimestamp64Milli(tupleElement(sample,1)) AS sample_ms,
             tupleElement(sample,2) AS sample_v FROM src
    ), dedup AS (
      SELECT series_id,sample_ms,
             argMax(sample_v,tuple(toUInt8(not isNaN(sample_v)),if(isNaN(sample_v),0,sample_v))) AS value
      FROM flat WHERE reinterpretAsUInt64(sample_v)!=9218868437227405314
      GROUP BY series_id,sample_ms
    ), indexed AS (
      SELECT series_id,sample_ms,value,
             intDiv(sample_ms-{start_ms}+{step_ms}-1,{step_ms}) AS gi FROM dedup
    ), per_series AS (
      SELECT series_id,gi,
             arrayElement(
               timeSeriesAvgToGrid(
                 fromUnixTimestamp64Milli(0),fromUnixTimestamp64Milli(0),
                 toDecimal64('86400',3),toDecimal64('86400',3)
               )(fromUnixTimestamp64Milli(sample_ms-({start_ms}+gi*{step_ms})),value),
               1) AS series_avg
      FROM indexed
      WHERE gi>=0 AND gi<{n}
        AND sample_ms>{start_ms}+gi*{step_ms}-{window_ms}
        AND sample_ms<={start_ms}+gi*{step_ms}
      GROUP BY series_id,gi
    ), per_stage AS (
      SELECT timeSeriesRemoveAllTagsExcept(timeSeriesIdToGroup(series_id),['stage']) AS stage_group,
             gi,avg(series_avg) AS stage_avg FROM per_series GROUP BY stage_group,gi
    )
    SELECT timeSeriesGroupToTags(stage_group) AS tags,
           CAST(timeSeriesGroupArray(fromUnixTimestamp64Milli({start_ms}+gi*{step_ms}),stage_avg),
                'Array(Tuple(DateTime64(3),Float64))') AS samples
    FROM per_stage GROUP BY stage_group FORMAT JSONEachRow
    """


def translated_query_no_sql_dedup(start,end,step,table='precision_ts',metric='tslab_numeric_precision'):
    """Keep native timestamp dedup inside timeSeriesAvgToGrid; remove SQL argMax GROUP BY."""
    q=translated_query(start,end,step,table=table,metric=metric)
    a=q.index("    ), dedup AS (")
    b=q.index("    ), per_series AS (",a)
    start_ms=start*1000
    step_ms=step*1000
    replacement=f"""    ), indexed AS (
      SELECT series_id,sample_ms,sample_v AS value,
             intDiv(sample_ms-{start_ms}+{step_ms}-1,{step_ms}) AS gi
      FROM flat
      WHERE reinterpretAsUInt64(sample_v)!=9218868437227405314
"""
    return q[:a]+replacement+q[b:]


def main():
    p=argparse.ArgumentParser()
    p.add_argument("--manifest",required=True)
    p.add_argument("--output",required=True)
    a=p.parse_args()
    m=json.loads(pathlib.Path(a.manifest).read_text())
    start,end=m["start"],m["end"]
    baseline=("SELECT * FROM prometheusQueryRange(precision_ts,"
        "'avg by(stage)(avg_over_time(tslab_numeric_precision[1d]))',"
        f"{start},{end},86400) FORMAT JSONEachRow")
    candidate=translated_query(start,end,86400)
    reference=None
    report={"all_equal":True,"queries":{}}
    for name,sql in [("baseline_before",baseline),("native_one_point",candidate),("baseline_after",baseline)]:
        trial_ms=[]
        for i in range(4):
            ms,raw=post(sql)
            cur=parse(raw)
            assert cur,(name,"empty")
            if reference is None:reference=cur
            mismatch=compare(reference,cur)
            if any(mismatch.values()):report["all_equal"]=False
            if i>0:trial_ms.append(ms)
        report["queries"][name]={"p50_ms":statistics.median(trial_ms),
                                  "timings_ms":trial_ms,"mismatch":mismatch,
                                  "rows":[{"tags":list(k[0]),"timestamp":k[1],"value":v} for k,v in sorted(cur.items())]}
        print("NATIVE_ONE_POINT_PHASE",json.dumps({"name":name,**report["queries"][name]}),flush=True)
    pathlib.Path(a.output).write_text(json.dumps(report,indent=2)+"\n")
    print("NATIVE_ONE_POINT_RESULT",json.dumps({"all_equal":report["all_equal"],"query_count":len(report["queries"])}),flush=True)

if __name__=="__main__":
    main()
