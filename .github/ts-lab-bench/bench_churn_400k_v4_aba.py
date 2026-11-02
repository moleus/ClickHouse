#!/usr/bin/env python3
"""400,000 one-sample series over 40 days: C++ v4 A/B/C/A correctness/cost."""
import argparse,json,os,pathlib
from bench_bucket_cxx_abca import assert_same,normalize_any,pct,post
from bench_filtered_selector_v4_sql import sql_string

PHASES=("base_before","v4_on","v4_off","base_after")
DAYS=(14,40)
METRIC="tslab_daily_churn_scale"
SELECTORS={
  "all":METRIC,
  "branch_regex":METRIC+'{branch=~"main|feature-.*"}',
}

def normalize_store(rows):
    return {(tuple(tuple(kv) for kv in labels),int(ts)):float(value)
            for labels,ts,value in rows}

def output_rows(out):
    return [[list([k,v] for k,v in labels),ts,value]
            for (labels,ts),value in sorted(out.items())]

def main():
    ap=argparse.ArgumentParser()
    ap.add_argument("--phase",required=True,choices=PHASES)
    ap.add_argument("--manifest",required=True)
    ap.add_argument("--canonical",required=True)
    ap.add_argument("--output",required=True)
    args=ap.parse_args()
    m=json.loads(pathlib.Path(args.manifest).read_text())
    end=int(m["end"])
    canonical_path=pathlib.Path(args.canonical)
    canonical=json.loads(canonical_path.read_text()) if args.phase!="base_before" else {}
    cases={}
    prefix="churn_"+os.environ.get("GITHUB_RUN_ID","local")+"_"+args.phase
    for days in DAYS:
        start=end-(days-1)*86400
        for case,selector in SELECTORS.items():
            name=f"{case}_{days}"
            expr=f"avg by(stage)(avg_over_time({selector}[1d]))"
            sql=(f"SELECT * FROM prometheusQueryRange(churn_ts,{sql_string(expr)},"
                 f"{start},{end},86400)")
            if args.phase=="v4_on":
                sql+=" SETTINGS promql_enable_native_one_point_lab=1"
            sql+=" FORMAT JSONEachRow"
            reference=None
            reference_set=False
            trials=[]
            ids=[]
            for i in range(7):
                qid=f"{prefix}_{name}_{i}"
                elapsed,raw=post("http://127.0.0.1:18191",sql,qid)
                observed=normalize_any(raw)
                if not reference_set:
                    reference=observed
                    reference_set=True
                    if args.phase!="base_before":
                        assert_same(normalize_store(canonical[name]),reference,name,args.phase)
                else:
                    assert_same(reference,observed,name,args.phase)
                if i>=2:
                    trials.append(elapsed)
                    ids.append(qid)
            if args.phase=="base_before":
                canonical[name]=output_rows(reference)
            stat={"phase":args.phase,"case":name,"days":days,
                  "selector":selector,"points":len(reference),
                  "p50_ms":pct(trials,.5),"p95_ms":pct(trials,.95),
                  "trials_ms":trials,"ids":ids}
            cases[name]=stat
    post("http://127.0.0.1:18191","SYSTEM FLUSH LOGS")
    _,raw=post("http://127.0.0.1:18191",
        "SELECT query_id,query_duration_ms,read_rows,read_bytes,memory_usage "
        "FROM system.query_log WHERE type='QueryFinish' "
        f"AND startsWith(query_id,{sql_string(prefix)}) FORMAT JSONEachRow")
    records={o["query_id"]:o for line in raw.splitlines() if line.strip() for o in [json.loads(line)]}
    for case,stat in cases.items():
        items=[records[q] for q in stat.pop("ids")]
        stat.update({"server_p50_ms":pct([x["query_duration_ms"] for x in items],.5),
                     "read_rows_p50":pct([x["read_rows"] for x in items],.5),
                     "read_bytes_p50":pct([x["read_bytes"] for x in items],.5),
                     "memory_p95_bytes":pct([x["memory_usage"] for x in items],.95)})
        print("LONG_CHURN_V4_PHASE",json.dumps(stat,separators=(",",":")),flush=True)
    if args.phase=="base_before":
        canonical_path.write_text(json.dumps(canonical,separators=(",",":"))+"\n")
    pathlib.Path(args.output).write_text(json.dumps(
        {"all_exact":True,"phase":args.phase,"cases":cases},indent=2)+"\n")
    print("LONG_CHURN_V4_DONE",json.dumps({"phase":args.phase,"cases":len(cases)}),flush=True)
if __name__=="__main__":
    main()
