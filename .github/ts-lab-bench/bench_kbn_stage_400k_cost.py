#!/usr/bin/env python3
"""400k one-point stage AVG versus named streaming KBN with exact fsum oracle."""
import argparse,json,math,os,pathlib,statistics
from bench_bucket_cxx_abca import normalize_any,assert_same,pct,post
from bench_native_one_point_sql import translated_query_no_sql_dedup

PHASES=("ordinary_before","kbn","ordinary_after")
METRIC="tslab_daily_churn_scale"
DAYS=(14,40)

def oracle(m,days):
    start_day=m["days"]-days
    result={}
    for day in range(start_day,m["days"]):
        eval_time=m["base"]+(day+1)*86400
        for stage in range(38):
            vals=[(i%1900+1)*.1+day*.001 for i in range(m["per_day"]) if i%38==stage]
            result[((("stage",f"stage-{stage:04d}"),),eval_time)]=math.fsum(vals)/len(vals)
    return result

def main():
    p=argparse.ArgumentParser()
    p.add_argument("--manifest",required=True)
    p.add_argument("--output",required=True)
    a=p.parse_args()
    m=json.loads(pathlib.Path(a.manifest).read_text())
    report={"all_finite":True,"all_equal":True,"runs":{}}
    prefix="kbn400k_"+os.environ.get("GITHUB_RUN_ID","local")
    for days in DAYS:
        end=m["end"]
        start=end-(days-1)*86400
        query=translated_query_no_sql_dedup(start,end,86400,table="churn_ts",metric=METRIC)
        anchor="gi,avg(series_avg) AS stage_avg"
        assert query.count(anchor)==1
        expected=oracle(m,days)
        current=None
        for phase in PHASES:
            sql=query
            if phase=="kbn":
                sql=query.replace(anchor,"gi,avgKBNLab(series_avg) AS stage_avg")
            times=[]
            ids=[]
            first=None
            for trial in range(7):
                qid=f"{prefix}_{days}_{phase}_{trial}"
                ms,raw=post("http://127.0.0.1:18191",sql,qid)
                observed=normalize_any(raw)
                if first is None:
                    first=observed
                else:
                    assert_same(first,observed,f"{phase}_{days}",f"repeat{trial}")
                assert set(observed)==set(expected),(days,phase,"wrong stage/time keys",
                    len(set(expected)-set(observed)),len(set(observed)-set(expected)))
                assert all(math.isfinite(x) for x in observed.values())
                if trial>=2:times.append(ms);ids.append(qid)
            if current is None:current=first
            else:assert_same(current,first,f"{days}",phase)
            error=max(abs(first[k]-expected[k]) for k in expected)
            rows={"days":days,"phase":phase,"stage_points":len(first),
                  "p50_ms":pct(times,.5),"trials_ms":times,
                  "max_error_fsum":error,"query_ids":ids,
                  "sql":sql}
            report["runs"][f"{days}_{phase}"]=rows
            print("KBN_STAGE_400K_PHASE",json.dumps({k:v for k,v in rows.items()
                if k not in ("sql","query_ids")},separators=(",",":")),flush=True)
    post("http://127.0.0.1:18191","SYSTEM FLUSH LOGS")
    _,raw=post("http://127.0.0.1:18191",
        "SELECT query_id,query_duration_ms,read_rows,read_bytes,memory_usage "
        "FROM system.query_log WHERE type='QueryFinish' "
        f"AND startsWith(query_id,'{prefix}') FORMAT JSONEachRow")
    logs={item["query_id"]:item for line in raw.splitlines() if line.strip()
          for item in [json.loads(line)]}
    for r in report["runs"].values():
        rows=[logs[qid] for qid in r.pop("query_ids")]
        r.update({"server_p50_ms":pct([x["query_duration_ms"] for x in rows],.5),
                  "read_rows_p50":pct([x["read_rows"] for x in rows],.5),
                  "read_bytes_p50":pct([x["read_bytes"] for x in rows],.5),
                  "memory_p95_bytes":pct([x["memory_usage"] for x in rows],.95)})
    for days in DAYS:
        plain=report["runs"][f"{days}_ordinary_after"]
        kbn=report["runs"][f"{days}_kbn"]
        print("KBN_STAGE_400K_SUMMARY",json.dumps({
             "days":days,"stage_points":plain["stage_points"],
             "ordinary_p50_ms":plain["p50_ms"],"kbn_p50_ms":kbn["p50_ms"],
             "time_ratio":kbn["p50_ms"]/plain["p50_ms"],
             "ordinary_memory_p95":plain["memory_p95_bytes"],
             "kbn_memory_p95":kbn["memory_p95_bytes"],
             "ordinary_fsum_max_error":plain["max_error_fsum"],
             "kbn_fsum_max_error":kbn["max_error_fsum"]},separators=(",",":")),flush=True)
    pathlib.Path(a.output).write_text(json.dumps(report,indent=2)+"\n")

if __name__=="__main__":main()
