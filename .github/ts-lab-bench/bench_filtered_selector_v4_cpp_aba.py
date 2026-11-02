#!/usr/bin/env python3
"""Real C++ v4 opt-in / opt-out filtered selector parity on the same dataset."""
import argparse,json,os,pathlib
from bench_bucket_cxx_abca import assert_same,normalize_any,pct,post
from bench_filtered_selector_v4_sql import METRIC,CASES,STEPS,native_filtered

PHASES=["base_before","v4_on","v4_off","base_after"]

def parse_rows(serialized):
    return {(tuple(tuple(kv) for kv in labels),int(ts)):float(val)
            for labels,ts,val in serialized}

def write_rows(rows):
    return [[list([k,v] for k,v in labels),ts,val]
            for (labels,ts),val in sorted(rows.items())]

def main():
    p=argparse.ArgumentParser()
    p.add_argument("--phase",required=True,choices=PHASES)
    p.add_argument("--manifest",required=True)
    p.add_argument("--canonical",required=True)
    p.add_argument("--output",required=True)
    a=p.parse_args()
    m=json.loads(pathlib.Path(a.manifest).read_text())
    end=int(m["query_end_epoch"])
    canonpath=pathlib.Path(a.canonical)
    canonical=json.loads(canonpath.read_text()) if a.phase!="base_before" else {}
    prefix="v4cpp_"+os.environ.get("GITHUB_RUN_ID","local")+"_"+a.phase
    report={}
    url="http://127.0.0.1:18191"
    for steps in STEPS:
        start=end-(steps-1)*86400
        for name,selector in CASES:
            case=f"{name}_e{steps}"
            sql=native_filtered(start,end,selector)
            if a.phase=="v4_on":
                sql=sql.replace(" FORMAT JSONEachRow",
                                " SETTINGS promql_enable_native_one_point_lab=1 FORMAT JSONEachRow")
            first=None
            first_set=False
            times=[]
            ids=[]
            for i in range(7):
                qid=f"{prefix}_{case}_{i}"
                elapsed,raw=post(url,sql,qid)
                observed=normalize_any(raw)
                if not first_set:
                    first=observed
                    first_set=True
                    if a.phase!="base_before":
                        assert_same(parse_rows(canonical[case]),first,case,a.phase)
                else:
                    assert_same(first,observed,case,a.phase)
                if i>=2:
                    times.append(elapsed)
                    ids.append(qid)
            if a.phase=="base_before":
                canonical[case]=write_rows(first)
            report[case]={"phase":a.phase,"selector":selector,"steps":steps,
                "points":len(first),"p50_ms":pct(times,.5),"p95_ms":pct(times,.95),
                "times_ms":times,"ids":ids}
    post("http://127.0.0.1:18191/","SYSTEM FLUSH LOGS")
    _,raw=post("http://127.0.0.1:18191/",
               "SELECT query_id,query_duration_ms,read_rows,read_bytes,memory_usage "
               "FROM system.query_log WHERE type='QueryFinish' AND startsWith(query_id,'%s') "
               "FORMAT JSONEachRow"%prefix)
    logs={o["query_id"]:o for line in raw.splitlines() if line.strip() for o in [json.loads(line)]}
    for case,data in report.items():
        qs=[logs[q] for q in data.pop("ids")]
        data["server_p50_ms"]=pct([q["query_duration_ms"] for q in qs],.5)
        data["read_rows_p50"]=pct([q["read_rows"] for q in qs],.5)
        data["read_bytes_p50"]=pct([q["read_bytes"] for q in qs],.5)
        data["memory_p95_bytes"]=pct([q["memory_usage"] for q in qs],.95)
        print("FILTERED_V4_CPP_PHASE",json.dumps({"case":case,**data},separators=(",",":")),flush=True)
    if a.phase=="base_before":
        canonpath.write_text(json.dumps(canonical,separators=(",",":"))+"\n")
    pathlib.Path(a.output).write_text(json.dumps(
        {"all_exact":True,"phase":a.phase,"cases":report},indent=2)+"\n")
    print("FILTERED_V4_CPP_PHASE_DONE",json.dumps({"phase":a.phase,"n":len(report)}),flush=True)
if __name__=="__main__":
    main()
