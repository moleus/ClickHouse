#!/usr/bin/env python3
"""Compare translated native one-point with/without SQL dedup on adversarial data."""
import argparse,json,pathlib,statistics,time
from bench_native_one_point_sql import post,parse,compare,translated_query,translated_query_no_sql_dedup

def main():
    p=argparse.ArgumentParser()
    p.add_argument("--manifest",required=True)
    p.add_argument("--output",required=True)
    a=p.parse_args()
    m=json.loads(pathlib.Path(a.manifest).read_text())
    start,end=m["start"],m["end"]
    expr="avg by(stage)(avg_over_time(tslab_numeric_precision[1d]))"
    queries=[
        ("original_before","SELECT * FROM prometheusQueryRange(precision_ts,'%s',%d,%d,86400) FORMAT JSONEachRow"%(expr,start,end)),
        ("with_explicit_dedup",translated_query(start,end,86400)),
        ("native_dedup_only",translated_query_no_sql_dedup(start,end,86400)),
        ("original_after","SELECT * FROM prometheusQueryRange(precision_ts,'%s',%d,%d,86400) FORMAT JSONEachRow"%(expr,start,end))
    ]
    ref=None
    records=[]
    for name,sql in queries:
        durations=[]
        output=None
        for i in range(6):
            ms,raw=post(sql)
            actual=parse(raw)
            assert actual,(name,"empty")
            if ref is None:ref=actual
            if output is None:output=actual
            else:
                d=compare(output,actual)
                assert not any(d.values()),(name,"unstable",d)
            if i>=2:durations.append(ms)
        diffs=compare(ref,output)
        if name=="original_after":assert not any(diffs.values()),("original drift",diffs)
        record={"name":name,"p50_ms":statistics.median(durations),
                "trials_ms":durations,"result_points":len(output),
                "all_equal":not any(diffs.values()),"differences":diffs,
                "results":[{"tags":list(k[0]),"timestamp":k[1],"value":v} for k,v in sorted(output.items())]}
        records.append(record)
        print("NATIVE_DEDUP_TEST",json.dumps(record,separators=(",",":")),flush=True)
    out={"all_semantics_equal":all(x["all_equal"] for x in records),"phases":records}
    pathlib.Path(a.output).write_text(json.dumps(out,indent=2)+"\n")
    print("NATIVE_DEDUP_RESULT",json.dumps({"all_semantics_equal":out["all_semantics_equal"]}),flush=True)

if __name__=="__main__":
    main()
