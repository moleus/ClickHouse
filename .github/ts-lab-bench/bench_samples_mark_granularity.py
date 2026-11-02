#!/usr/bin/env python3
"""Falsify sparse exact-tag mark-granularity speed model using same data and binary."""
import argparse,json,os,pathlib,random
from bench_bucket_cxx_abca import normalize_any,assert_same,pct,post
from bench_filtered_selector_v4_sql import sql_string
HOST="http://127.0.0.1:18191"
DAY=86400
GS=(256,64,16,4)
CASES={
 "whole":"",
 "stage":'{stage="stage-0007"}',
 "project":'{project="project-00"}',
 "branch_regex":'{branch=~"main|feature-0[0-9]"}',
}

def call(sql,qid=None):
    ms,raw=post(HOST,sql,qid)
    return ms,raw.decode("utf-8","replace")

def save(path,obj):
    pathlib.Path(path).write_text(json.dumps(obj,indent=2)+"\n")

def main():
    ap=argparse.ArgumentParser()
    ap.add_argument("--manifest",required=True)
    ap.add_argument("--import-report",required=True)
    ap.add_argument("--output",required=True)
    a=ap.parse_args()
    m=json.loads(pathlib.Path(a.manifest).read_text())
    imported=json.loads(pathlib.Path(a.import_report).read_text())
    assert {int(x) for x in imported["variants"]}==set(GS)
    end=int(m["end"])
    run=os.getenv("GITHUB_RUN_ID","local")
    work={}
    for kind in ("sparse","dense"):
        metric=f"tslab_{kind}_40d"
        for days in (3,40):
            start=end-(days-1)*DAY
            for label,suffix in CASES.items():
                key=f"{kind}_{label}_e{days}"
                expr=f"avg by(stage)(avg_over_time({metric}{suffix}[1d]))"
                work[key]={}
                for gran in GS:
                    table="cost_ts" if gran==256 else f"cost_g{gran}"
                    base=f"SELECT * FROM prometheusQueryRange({table},{sql_string(expr)},{start},{end},86400)"
                    for mode in ("off","on"):
                        work[key][f"{gran}_{mode}"]=base+f" SETTINGS max_threads=4,promql_enable_native_one_point_lab={1 if mode=='on' else 0} FORMAT JSONEachRow"
    refs={}
    measured={k:{variant:[] for variant in queries} for k,queries in work.items()}
    # Exact oracle from the original schema, opt-out. No short-circuit by sample count.
    for case,queries in work.items():
        _,raw=call(queries["256_off"])
        refs[case]=normalize_any(raw.encode())
        assert refs[case],("empty fixture query",case)
    # Warm all paths once, then interleave six balanced repetitions.
    for case,queries in work.items():
        for variant,stmt in queries.items():
            _,raw=call(stmt)
            assert_same(refs[case],normalize_any(raw.encode()),case,variant+"_warmup")
    rng=random.Random(1977481)
    for rep in range(6):
        order=list(work);rng.shuffle(order)
        for case in order:
            variants=list(work[case]);rng.shuffle(variants)
            for variant in variants:
                qid=f"mark_{run}_{case}_{variant}_{rep}"
                ms,raw=call(work[case][variant],qid)
                now=normalize_any(raw.encode())
                assert_same(refs[case],now,case,variant)
                measured[case][variant].append((qid,ms))
    call("SYSTEM FLUSH LOGS")
    _,body=call("SELECT query_id,query_duration_ms,read_rows,read_bytes,memory_usage,ProfileEvents "
                "FROM system.query_log WHERE type='QueryFinish' AND "
                f"startsWith(query_id,'mark_{run}_') FORMAT JSONEachRow")
    logs={x["query_id"]:x for line in body.splitlines() if line.strip() for x in [json.loads(line)]}
    outcomes=[]
    for case,variants in measured.items():
        stats={}
        for variant,rows in variants.items():
            ids=[x[0] for x in rows]
            if not all(x in logs for x in ids):
                raise AssertionError(("missing querylog rows",case,variant))
            entries=[logs[x] for x in ids]
            stats[variant]={
                "p50_ms":pct([x[1] for x in rows],.5),
                "p95_ms":pct([x[1] for x in rows],.95),
                "times_ms":[x[1] for x in rows],
                "read_rows_p50":pct([x["read_rows"] for x in entries],.5),
                "read_bytes_p50":pct([x["read_bytes"] for x in entries],.5),
                "memory_p95_bytes":pct([x["memory_usage"] for x in entries],.95),
                "selected_marks_p50":pct([x["ProfileEvents"].get("SelectedMarks",0) for x in entries],.5),
                "selected_parts_p50":pct([x["ProfileEvents"].get("SelectedParts",0) for x in entries],.5),
                "cpu_us_p50":pct([x["ProfileEvents"].get("OSCPUVirtualTimeMicroseconds",0) for x in entries],.5)
            }
        base=stats["256_on"]
        for gran in GS:
            row=stats[f"{gran}_on"]
            label={"case":case,"granularity":gran,"result_points":len(refs[case]),
                   "on_p50_ms":row["p50_ms"],"native_off_p50_ms":stats[f"{gran}_off"]["p50_ms"],
                   "on_vs_256":row["p50_ms"]/max(base["p50_ms"],1e-5),
                   "read_rows":row["read_rows_p50"],"selected_marks":row["selected_marks_p50"],
                   "memory_p95_bytes":row["memory_p95_bytes"],
                   "cpu_us":row["cpu_us_p50"]}
            print("MARK_GRANULARITY_CASE",json.dumps(label,separators=(",",":")),flush=True)
            outcomes.append(label)
    plans={}
    # Query planning index/mark proof after the measured timings.
    for gran in GS:
        for case in ("sparse_stage_e40","sparse_whole_e40","dense_stage_e40"):
            stmt=work[case][f"{gran}_on"].replace(" FORMAT JSONEachRow","")
            try:
                _,text=call("EXPLAIN indexes=1 "+stmt)
                plans[f"{case}_g{gran}"]=text
            except Exception as exc:
                plans[f"{case}_g{gran}"]="ERROR "+str(exc)
    save(pathlib.Path(a.output).with_name("mark-granularity-plans.json"),plans)
    save(pathlib.Path(a.output).with_name("mark-granularity-querylog.json"),logs)
    report={"all_exact":True,"fixtures":imported,"cases":outcomes,
            "detailed":{key:{variant: {
               "p50_ms":pct([x[1] for x in values],.5),
               "trials_ms":[x[1] for x in values]}
               for variant,values in variants.items()}
               for key,variants in measured.items()}}
    save(a.output,report)
    print("MARK_GRANULARITY_COMPLETE",json.dumps({
        "cases":len(work),"variant_results":len(outcomes),
        "all_exact":True},separators=(",",":")),flush=True)

if __name__=="__main__":main()
