#!/usr/bin/env python3
"""Restricted one-sample stage query: 3 event MergeTrees vs native TS and VM.

No unsupported query is silently accepted. All returned key/value pairs must
match both engines before timing is reported.
"""
import argparse,json,os,pathlib,random,math,time
from bench_bucket_cxx_abca import assert_same,normalize_any,pct,post
from bench_filtered_selector_v4_sql import sql_string
from bench_ch_vs_vm_dense_sparse import vm_get,vm_normalized,wait_vm

CH="http://127.0.0.1:18191"
DAY=86400
LAYOUTS=("events_time","events_stage","events_project")
SCOPES={"all":("", ""),
        "stage":('{stage="stage-0007"}'," AND stage='stage-0007'"),
        "project":('{project="project-00"}'," AND project='project-00'"),
        "regex":('{branch=~"main|feature-0[0-9]"}',
                 " AND match(toString(branch),'^(main|feature-0[0-9])$')")}

def event_sql(table,start,end,flt):
    min_ms=(start-DAY)*1000
    max_ms=end*1000
    # All timestamps are post-1970; evaluation times are exact UTC day ends.
    # The GROUP BY aligns with T_j = T0 + j*W and W=86400 seconds.
    return (
       "SELECT stage, "
       "toUInt64(intDiv(toUnixTimestamp64Milli(ts),86400000)*86400+86400) AS epoch,"
       " avg(value) AS value "
       f"FROM {table} WHERE metric_name='tslab_sparse_40d' "
       f"AND ts>fromUnixTimestamp64Milli({min_ms}) "
       f"AND ts<=fromUnixTimestamp64Milli({max_ms}) {flt} "
       "GROUP BY stage,epoch ORDER BY stage,epoch FORMAT JSONEachRow")

def parse_events(raw):
    rows={}
    for line in raw.splitlines():
        if not line.strip():continue
        o=json.loads(line)
        assert math.isfinite(float(o["value"])),("event output nonfinite",o)
        key=((("stage",o["stage"]),),int(o["epoch"]))
        assert key not in rows,("duplicate grouped stage",key)
        rows[key]=float(o["value"])
    return rows

def run_case(meta,scope,steps):
    end=int(meta["end"]);start=end-(steps-1)*DAY
    selector_suffix,sql_filter=SCOPES[scope]
    expr=f"avg by(stage)(avg_over_time(tslab_sparse_40d{selector_suffix}[1d]))"
    base=("SELECT * FROM prometheusQueryRange(cost_ts,"
          f"{sql_string(expr)},{start},{end},86400)")
    variants={
       "vm":None,
       "ts_off":base+" SETTINGS promql_enable_native_one_point_lab=0 FORMAT JSONEachRow",
       "ts_v4":base+" SETTINGS promql_enable_native_one_point_lab=1 FORMAT JSONEachRow",
    }
    for t in LAYOUTS:variants[t]=event_sql(t,start,end,sql_filter)
    return f"{scope}_e{steps}",expr,start,end,variants

def main():
    p=argparse.ArgumentParser()
    p.add_argument("--manifest",required=True)
    p.add_argument("--event-manifest",required=True)
    p.add_argument("--output",required=True)
    a=p.parse_args()
    meta=json.loads(pathlib.Path(a.manifest).read_text())
    event_manifest=json.loads(pathlib.Path(a.event_manifest).read_text())
    assert event_manifest["one_sample_per_series_verified"] is True
    assert event_manifest["sha256"]==meta["sha256"]
    assert event_manifest["unique_series"]==40000
    wait_vm(meta)
    run=os.getenv("GITHUB_RUN_ID","local")
    rng=random.Random(20261102)
    output={}
    for scope in SCOPES:
        for steps in (3,14,40):
            name,expr,start,end,queries=run_case(meta,scope,steps)
            observed={}
            stats={key:[] for key in queries}
            # Reuse the exact CH TimeSeries reference, preserve nullable semantics.
            _,raw=post(CH,queries["ts_off"])
            reference=normalize_any(raw)
            assert reference,("empty reference",name)
            # Every path is validated on warmups and measured calls.
            for rep in range(8):
                variants=list(queries)
                rng.shuffle(variants)
                for variant in variants:
                    if variant=="vm":
                        latency,payload=vm_get({"query":expr,"start":start,
                                                "end":end,"step":DAY})
                        got=vm_normalized(payload)
                    elif variant.startswith("ts_"):
                        qid=f"ev_{run}_{name}_{variant}_{rep}"
                        latency,body=post(CH,queries[variant],qid)
                        got=normalize_any(body)
                    else:
                        qid=f"ev_{run}_{name}_{variant}_{rep}"
                        latency,body=post(CH,queries[variant],qid)
                        got=parse_events(body.decode("utf-8","replace"))
                    assert_same(reference,got,name,variant)
                    if rep>=2:stats[variant].append((rep,round(latency,3)))
            per={x:{"p50_ms":pct([q[1] for q in samples],.5),
                    "p95_ms":pct([q[1] for q in samples],.95),
                    "times_ms":[q[1] for q in samples]}
                 for x,samples in stats.items()}
            case={"points":len(reference),"query":expr,"results":per}
            output[name]=case
            print("EVENT_VS_VM_CASE",json.dumps({
                "case":name,"points":len(reference),
                "vm_ms":per["vm"]["p50_ms"],"ts_v4_ms":per["ts_v4"]["p50_ms"],
                "event_time_ms":per["events_time"]["p50_ms"],
                "event_stage_ms":per["events_stage"]["p50_ms"],
                "event_project_ms":per["events_project"]["p50_ms"]},
                separators=(",",":")),flush=True)
    post(CH,"SYSTEM FLUSH LOGS")
    _,raw=post(CH,
        "SELECT query_id,query_duration_ms,read_rows,read_bytes,memory_usage,"
        "ProfileEvents FROM system.query_log WHERE type='QueryFinish' "
        f"AND startsWith(query_id,{sql_string('ev_'+run+'_')}) FORMAT JSONEachRow")
    logs={o["query_id"]:o for line in raw.splitlines() if line.strip()
          for o in [json.loads(line)]}
    for name,case in output.items():
        for variant,stats in case["results"].items():
            if variant=="vm":continue
            ids=[f"ev_{run}_{name}_{variant}_{rep}" for rep in range(2,8)]
            assert all(x in logs for x in ids),("querylog missing",name,variant)
            entries=[logs[x] for x in ids]
            stats.update({
                "server_p50_ms":pct([x["query_duration_ms"] for x in entries],.5),
                "read_rows_p50":pct([x["read_rows"] for x in entries],.5),
                "read_bytes_p50":pct([x["read_bytes"] for x in entries],.5),
                "memory_p95_bytes":pct([x["memory_usage"] for x in entries],.95),
                "cpu_us_p50":pct([
                   int(x["ProfileEvents"].get("OSCPUVirtualTimeMicroseconds",0))
                   for x in entries],.5)})
    summary={"all_exact":True,"total_queries":len(output),
             "best_layout_faster_than_vm":sum(
                min(case["results"][t]["p50_ms"] for t in LAYOUTS)
                <case["results"]["vm"]["p50_ms"] for case in output.values()),
             "all_layouts_faster_than_vm":sum(
                all(case["results"][t]["p50_ms"]<case["results"]["vm"]["p50_ms"]
                    for t in LAYOUTS) for case in output.values())}
    pathlib.Path(a.output).write_text(json.dumps({
        "manifest":meta,"event_manifest":event_manifest,
        "cases":output,"summary":summary},indent=2)+"\n")
    pathlib.Path(a.output).with_name("event-vs-vm-querylog.json").write_text(
        json.dumps(logs,indent=2)+"\n")
    print("EVENT_VS_VM_COMPLETE",json.dumps(summary,separators=(",",":")),flush=True)
if __name__=="__main__":main()
