#!/usr/bin/env python3
"""Dense long-lived vs sparse daily-new grouped AVG A/B/C/A with C++ v4."""
import argparse,json,os,pathlib
from bench_bucket_cxx_abca import assert_same,normalize_any,pct,post
from bench_filtered_selector_v4_sql import sql_string
DAYS=(3,14,40)
METRICS={"dense":"tslab_dense_40d","sparse":"tslab_sparse_40d"}
FILTERS={"all":"","project":"{project=\"project-00\"}"}
PHASES=("base_before","v4_on","v4_off","base_after")

def encode(rows):
    return [[list([k,v] for k,v in labels),ts,v] for (labels,ts),v in sorted(rows.items())]

def decode(rows):
    return {(tuple(tuple(x) for x in labels),int(ts)):float(v)
            for labels,ts,v in rows}

def main():
    p=argparse.ArgumentParser()
    p.add_argument("--manifest",required=True)
    p.add_argument("--canonical",required=True)
    p.add_argument("--phase",required=True,choices=PHASES)
    p.add_argument("--output",required=True)
    a=p.parse_args()
    manifest=json.loads(pathlib.Path(a.manifest).read_text())
    end=manifest["end"]
    canonpath=pathlib.Path(a.canonical)
    canonical={} if a.phase=="base_before" else json.loads(canonpath.read_text())
    reports={}
    prefix="densparse_"+os.environ.get("GITHUB_RUN_ID","local")+"_"+a.phase
    for kind,metric in METRICS.items():
        for e in DAYS:
            start=end-(e-1)*86400
            for filter_name,filter_text in FILTERS.items():
                name=f"{kind}_{filter_name}_e{e}"
                expression=f"avg by(stage)(avg_over_time({metric}{filter_text}[1d]))"
                sql=(f"SELECT * FROM prometheusQueryRange(cost_ts,{sql_string(expression)},"
                     f"{start},{end},86400)")
                if a.phase=="v4_on":
                    sql+=" SETTINGS promql_enable_native_one_point_lab=1"
                sql+=" FORMAT JSONEachRow"
                first=None
                first_set=False
                timings=[]
                qids=[]
                for i in range(7):
                    qid=f"{prefix}_{name}_{i}"
                    ms,raw=post("http://127.0.0.1:18191",sql,qid)
                    got=normalize_any(raw)
                    if not first_set:
                        first=got;first_set=True
                        if a.phase!="base_before":
                            assert_same(decode(canonical[name]),first,name,a.phase)
                    else:
                        assert_same(first,got,name,a.phase)
                    if i>=2:timings.append(ms);qids.append(qid)
                if a.phase=="base_before":canonical[name]=encode(first)
                reports[name]={"phase":a.phase,"workload":kind,"filter":filter_name,
                               "days":e,"points":len(first),"client_p50_ms":pct(timings,.5),
                               "client_p95_ms":pct(timings,.95),"trials_ms":timings,
                               "query_ids":qids}
                print("DENSE_SPARSE_CLIENT",json.dumps({"phase":a.phase,"case":name,
                      "p50_ms":pct(timings,.5),"points":len(first)},separators=(",",":")),flush=True)
    post("http://127.0.0.1:18191","SYSTEM FLUSH LOGS")
    _,raw=post("http://127.0.0.1:18191",
        "SELECT query_id,query_duration_ms,read_rows,read_bytes,memory_usage "
        "FROM system.query_log WHERE type='QueryFinish' "
        f"AND startsWith(query_id,{sql_string(prefix)}) FORMAT JSONEachRow")
    logs={row["query_id"]:row for line in raw.splitlines() if line.strip()
          for row in [json.loads(line)]}
    for name,report in reports.items():
        selected=[logs[q] for q in report.pop("query_ids")]
        report.update({
            "server_p50_ms":pct([x["query_duration_ms"] for x in selected],.5),
            "read_rows_p50":pct([x["read_rows"] for x in selected],.5),
            "read_bytes_p50":pct([x["read_bytes"] for x in selected],.5),
            "memory_p95_bytes":pct([x["memory_usage"] for x in selected],.95)
        })
    if a.phase=="base_before":
        canonpath.write_text(json.dumps(canonical,separators=(",",":"))+"\n")
    result={"all_exact":True,"phase":a.phase,"cases":reports}
    pathlib.Path(a.output).write_text(json.dumps(result,indent=2)+"\n")
    print("DENSE_SPARSE_DONE",json.dumps({"phase":a.phase,"cases":len(reports)}),flush=True)
if __name__=="__main__":main()
