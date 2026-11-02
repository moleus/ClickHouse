#!/usr/bin/env python3
"""Check invoker TAGS row policy on original/v4 TimeSeries PromQL converter."""
import argparse,json,math,os,pathlib,statistics,time
from bench_bucket_cxx_abca import normalize_any,assert_same,post,pct
from setup_v4_acl_fixture import reader_post
PHASES=("base_before","v4_on","v4_off","base_after")
TESTS={
    "whole":"tslab_acl_metric",
    "allowed":'tslab_acl_metric{project="allowed"}',
    "denied":'tslab_acl_metric{project="denied"}',
    "both_regex":'tslab_acl_metric{project=~"allowed|denied"}',
}

def verify_scope(rows,case):
    if case=="denied":
        assert not rows,("denied project unexpectedly visible",case,rows)
        return
    assert rows,(case,"allowed project unexpectedly absent")
    stages={dict(tags).get("stage") for (tags,_),_ in rows.items()}
    assert stages=={"stage-allowed"},("row policy leaked a stage",case,stages)
    assert all(math.isclose(value,4.0,rel_tol=0,abs_tol=1e-12)
               for value in rows.values()),("wrong allowed values",case,rows)

def enc(rows):
    return [[list([k,v] for k,v in tags),ts,value]
            for (tags,ts),value in sorted(rows.items())]

def dec(data):
    return {(tuple(tuple(x) for x in tags),int(ts)):float(value)
            for tags,ts,value in data}

def main():
    p=argparse.ArgumentParser()
    p.add_argument("--phase",required=True,choices=PHASES)
    p.add_argument("--manifest",required=True)
    p.add_argument("--canonical",required=True)
    p.add_argument("--output",required=True)
    args=p.parse_args()
    m=json.loads(pathlib.Path(args.manifest).read_text())
    target=m["tags_target"]
    tick=chr(96)
    fq="default."+tick+target.replace(tick,tick+tick)+tick
    visible_raw=reader_post(
       f"SELECT tags['project'] AS project FROM {fq} "
       "WHERE metric_name='tslab_acl_metric' FORMAT JSONEachRow")
    direct=[json.loads(line) for line in visible_raw.splitlines() if line.strip()]
    assert {x["project"] for x in direct}=={"allowed"},("row policy no longer active",direct)
    canonical_path=pathlib.Path(args.canonical)
    if args.phase=="base_before":
        admin_sql=("SELECT * FROM prometheusQueryRange(access_ts,"
                   "'avg by(stage)(avg_over_time(tslab_acl_metric[1d]))',"
                   f"{m['start']},{m['end']},86400) FORMAT JSONEachRow")
        _,raw=post("http://127.0.0.1:18191",admin_sql)
        admin=normalize_any(raw)
        assert {dict(k[0]).get("stage") for k in admin}=={
            "stage-allowed","stage-denied"},admin
        print("ACL_ADMIN_CONTROL",json.dumps({"projects":2,
              "stages":sorted({dict(k[0]).get('stage') for k in admin})}),flush=True)
    canonical={} if args.phase=="base_before" else json.loads(canonical_path.read_text())
    results={}
    for case,selector in TESTS.items():
        expr=f"avg by(stage)(avg_over_time({selector}[1d]))"
        sql=f"SELECT * FROM prometheusQueryRange(access_ts,'{expr}',{m['start']},{m['end']},86400)"
        if args.phase=="v4_on":
            sql+=" SETTINGS promql_enable_native_one_point_lab=1"
        sql+=" FORMAT JSONEachRow"
        first=None
        first_set=False
        times=[]
        for i in range(7):
            began=time.perf_counter()
            raw=reader_post(sql)
            ms=1000*(time.perf_counter()-began)
            rows=normalize_any(raw)
            verify_scope(rows,case)
            if not first_set:
                first=rows;first_set=True
                if args.phase!="base_before":
                    assert_same(dec(canonical[case]),first,case,args.phase)
            else:assert_same(first,rows,case,args.phase)
            if i>=2:times.append(ms)
        if args.phase=="base_before":canonical[case]=enc(first)
        results[case]={"phase":args.phase,"points":len(first),
                       "p50_ms":pct(times,.5),"trials_ms":times,
                       "sql":sql}
        print("ACL_INVOKER_PHASE",json.dumps({k:v for k,v in results[case].items()
            if k!="sql"},separators=(",",":")),flush=True)
    if args.phase=="base_before":
        canonical_path.write_text(json.dumps(canonical,separators=(",",":"))+"\n")
    pathlib.Path(args.output).write_text(json.dumps({
        "phase":args.phase,"row_policy_confirmed":True,"cases":results},indent=2)+"\n")
    print("ACL_INVOKER_RESULT",json.dumps({"phase":args.phase,
        "all_equal":True,"allowed_only":True},separators=(",",":")),flush=True)
if __name__=="__main__":main()
