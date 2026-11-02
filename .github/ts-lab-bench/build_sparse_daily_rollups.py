#!/usr/bin/env python3
"""Build exact-data-contract daily stage and facet sum/count rollups from event source."""
import argparse,json,pathlib,time
from bench_bucket_cxx_abca import post

HOST="http://127.0.0.1:18191"
DAY=86400

def sql(q):
    start=time.perf_counter()
    _,raw=post(HOST,q)
    return 1000*(time.perf_counter()-start),raw.decode("utf-8","replace").strip()

def main():
    ap=argparse.ArgumentParser()
    ap.add_argument("--event-manifest",required=True)
    ap.add_argument("--output",required=True)
    a=ap.parse_args()
    manifest=json.loads(pathlib.Path(a.event_manifest).read_text())
    assert manifest["one_sample_per_series_verified"] and manifest["unique_series"]==40000
    tables={
      "rollup_stage":{"cols":
       "metric_name LowCardinality(String),stage LowCardinality(String),"
       "epoch UInt32,sum_v Float64,n UInt64",
       "keys":"metric_name,stage,epoch","select":"metric_name,stage"},
      "rollup_facets":{"cols":
       "metric_name LowCardinality(String),stage LowCardinality(String),"
       "project LowCardinality(String),branch LowCardinality(String),"
       "epoch UInt32,sum_v Float64,n UInt64",
       "keys":"metric_name,stage,project,branch,epoch",
       "select":"metric_name,stage,project,branch"},
    }
    cases={}
    epoch="toUInt32(intDiv(toUnixTimestamp64Milli(ts),86400000)*86400+86400)"
    for table,item in tables.items():
        _,_=sql(f"CREATE TABLE {table} ({item['cols']}) "
                 f"ENGINE=SummingMergeTree((sum_v,n)) "
                 f"PARTITION BY intDiv(epoch,2678400) "
                 f"ORDER BY ({item['keys']}) SETTINGS index_granularity=256")
        started=time.perf_counter()
        duration,_=sql(
            f"INSERT INTO {table} SELECT {item['select']},{epoch} AS epoch, "
            "sum(value) AS sum_v,count() AS n "
            f"FROM events_stage GROUP BY {item['select']},epoch")
        _,s=sql(f"SELECT count(),sum(n),sum(sum_v),uniqExact(epoch) "
                f"FROM {table} FORMAT TSV")
        rows,n,mean_sum,days=s.split("\t")
        assert int(n)==40000 and int(days)==40,(table,s)
        _,stored=sql("SELECT sum(bytes_on_disk),sum(marks),count() FROM "
            "system.parts WHERE database='default' AND active "
            f"AND table='{table}' FORMAT TSV")
        disk,marks,parts=(int(x) for x in stored.split("\t"))
        record={"rows":int(rows),"n":int(n),"total_sum":float(mean_sum),
                "days":int(days),"build_ms":duration,
                "disk_bytes":disk,"marks":marks,"parts":parts}
        cases[table]=record
        print("ROLLUP_BUILD_LAYOUT",json.dumps({"table":table,**record},
              separators=(",",":")),flush=True)
    # Cross-check against raw per-day-stage output; fail for mismatched groups.
    _,s=sql("SELECT stage,epoch,sum(sum_v)/sum(n) AS avg_v "
            "FROM rollup_stage GROUP BY stage,epoch ORDER BY stage,epoch FORMAT JSONEachRow")
    _,r=sql("SELECT stage, "
            "toUInt32(intDiv(toUnixTimestamp64Milli(ts),86400000)*86400+86400) AS epoch,"
            "avg(value) AS avg_v FROM events_stage "
            "GROUP BY stage,epoch ORDER BY stage,epoch FORMAT JSONEachRow")
    a1={(x["stage"],int(x["epoch"])):float(x["avg_v"]) for l in s.splitlines()
        if l.strip() for x in [json.loads(l)]}
    a2={(x["stage"],int(x["epoch"])):float(x["avg_v"]) for l in r.splitlines()
        if l.strip() for x in [json.loads(l)]}
    assert set(a1)==set(a2) and len(a1)==1520,(len(a1),len(a2))
    assert max(abs(a1[k]-a2[k]) for k in a1)<1e-10
    report={"one_sample_contract":True,"builds":cases,"stage_days":len(a1),
            "max_raw_difference":max(abs(a1[k]-a2[k]) for k in a1)}
    pathlib.Path(a.output).write_text(json.dumps(report,indent=2)+"\n")
    print("ROLLUP_BUILD_COMPLETE",json.dumps({"stage_days":len(a1),
        "rollups":len(cases),"max_raw_difference":report["max_raw_difference"]},
        separators=(",",":")),flush=True)
if __name__=="__main__":main()
