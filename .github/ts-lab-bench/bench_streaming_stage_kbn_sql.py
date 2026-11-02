#!/usr/bin/env python3
"""Compare streaming avgKBNLab stage against mathematical oracle and original grouped AVG."""
import argparse,collections,json,math,pathlib,statistics
from bench_native_one_point_sql import post,parse,translated_query,translated_query_no_sql_dedup
def input_groups():
    return {
      'group_three':[1e18,1.0,-1e18],
      'group_three_reversed':[-1e18,1.0,1e18],
      'group_many_small':[1e18]+[1.0]*127+[-1e18],
      'group_many_small_reversed':[-1e18]+[1.0]*127+[1e18],
      'group_many_interleaved':[1e18,1.0,-1e18,1.0]*60,
      'group_big_split':[1e18]*50+[1.0]*101+[-1e18]*50,
      'group_small_exact':[1.0]*101,
    }
def stats(xs):
    mean=statistics.mean(xs)
    return {"minimum":min(xs),"maximum":max(xs),"mean":mean,"median":statistics.median(xs),
            "distinct_rounded":len(set(round(x,10) for x in xs)),
            "examples":xs[:12]}
def stage_kbn(start,end,threads):
    sql=translated_query_no_sql_dedup(start,end,86400)
    old="gi,avg(series_avg) AS stage_avg"
    assert sql.count(old)==1
    sql=sql.replace(old,"gi,avgKBNLab(series_avg) AS stage_avg")
    old="FORMAT JSONEachRow"
    assert sql.count(old)==1
    return sql.replace(old,f"SETTINGS max_threads={threads} FORMAT JSONEachRow")

def main():
    p=argparse.ArgumentParser()
    p.add_argument("--manifest",required=True)
    p.add_argument("--output",required=True)
    p.add_argument("--repeats",type=int,default=15)
    a=p.parse_args()
    m=json.loads(pathlib.Path(a.manifest).read_text())
    start,end=m["start"],m["end"]
    metric="tslab_numeric_precision"
    native=("SELECT * FROM prometheusQueryRange(precision_ts,"
            "'avg by(stage)(avg_over_time(%s[1d]))',%d,%d,86400) FORMAT JSONEachRow"%(metric,start,end))
    cases={
       "original_before":native,
       "onepoint_native_dedup":translated_query_no_sql_dedup(start,end,86400),
       "stream_kbn_t1":stage_kbn(start,end,1),
       "stream_kbn_t4":stage_kbn(start,end,4),
       "stream_kbn_t8":stage_kbn(start,end,8),
       "original_after":native
    }
    oracle={k:math.fsum(v)/len(v) for k,v in input_groups().items()}
    results={}
    for case,sql in cases.items():
        measured=collections.defaultdict(list)
        timings=[]
        reference_keys=None
        for i in range(a.repeats+2):
            ms,raw=post(sql)
            output=parse(raw)
            keys=set(output)
            if reference_keys is None:reference_keys=keys
            else:assert keys==reference_keys,("different output labels",case)
            if i<2:continue
            timings.append(ms)
            for (labels,t),value in output.items():
                label=dict(labels)
                stage=label.get("stage","")
                measured[stage].append(value)
        if case.startswith("stream_kbn"):
            for stage, expected in oracle.items():
                assert stage in measured,(case,stage,"missing")
                assert all(math.isclose(v,expected,rel_tol=1e-10,abs_tol=1e-9)
                           for v in measured[stage]),(
                           "streaming KBN numerical mismatch",case,stage,expected,measured[stage])
        results[case]={"query_p50_ms":statistics.median(timings),
                       "stages":{key:{**stats(vals),"reference_math_fsum":oracle.get(key),
                                       "median_abs_error":abs(statistics.median(vals)-oracle[key]) if key in oracle else None}
                                 for key,vals in measured.items()}}
        print("GROUP_AVG_ORDER_DISTRIBUTION",json.dumps({"phase":case,"result":results[case]},separators=(",",":")),flush=True)
    summary={"oracle":oracle,"phases":results}
    pathlib.Path(a.output).write_text(json.dumps(summary,indent=2)+"\n")
    print("GROUP_AVG_ORDER_SUMMARY",json.dumps({"baseline_unstable":any(
       results["original_before"]["stages"][k]["distinct_rounded"]>1 for k in oracle),
       "stages":len(oracle)}),flush=True)
if __name__=="__main__":
    main()
