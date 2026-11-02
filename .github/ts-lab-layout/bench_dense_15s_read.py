#!/usr/bin/env python3
"""Dense 15-second synthetic scrape reading locality, same O2 binary and data.

10k stable series, 240 samples per series over 1h, all same metric. Emits
one row with 240 samples for ingest efficiency: DOES NOT model live ingestion
cadence or repeated individual remote-write batches. Read workload only.
"""
import argparse,datetime,hashlib,json,os,pathlib,random,statistics,sys,time
sys.path.insert(0,'.github/ts-lab-layout')
sys.path.insert(0,'.github/ts-lab-perf')
sys.path.insert(0,'.github/ts-lab-bench')
from layout_id_smoke import definition
from bench_granularity_v11 import streamed_insert,optimize_table,wait_no_merges,stats,inner
from bench_stage_cpp_vs_original_vm_95k import monitored,server_restart,post,assert_same,pct,sql_string

METRIC="tslab_dense_container_cpu_seconds"
BASE=1793491200
SERIES=10000
SAMPLES=240
TABLES={"default":"layout_dense_default","cluster_namespace":"layout_dense_cluster_namespace"}
QUERIES={
 "whole":f"avg by(cluster)(avg_over_time({METRIC}[1h]))",
 "cluster":f"avg by(namespace)(avg_over_time({METRIC}{{cluster=\"cluster-01\"}}[1h]))",
 "namespace":f"avg by(cluster)(avg_over_time({METRIC}{{namespace=\"namespace-03\"}}[1h]))",
 "both":f"avg by(job)(avg_over_time({METRIC}{{cluster=\"cluster-01\",namespace=\"namespace-03\"}}[1h]))",
}
def sql(q):
    return post("http://127.0.0.1:18191",q)[1].decode(errors="replace")
def save(path,obj):
    pathlib.Path(path).write_text(json.dumps(obj,indent=2)+"\n")
def fixture(path):
    times=[datetime.datetime.fromtimestamp(BASE+15*(k+1),datetime.timezone.utc).strftime("%Y-%m-%d %H:%M:%S.000")
           for k in range(SAMPLES)]
    begin=time.perf_counter()
    h=hashlib.sha256();size=0
    with open(path,"wb") as f:
        for i in range(SERIES):
            tags={"cluster":f"cluster-{i%4:02d}","namespace":f"namespace-{(i//4)%20:02d}",
                  "job":f"job-{(i//80)%20:02d}","instance":f"instance-{i:05d}",
                  "pod":f"pod-{i:05d}"}
            row={"metric_name":METRIC,"tags":tags,
                 "samples":[[times[k],float(1+(i%17))+((k%13)/16)] for k in range(SAMPLES)]}
            raw=(json.dumps(row,separators=(",",":"),sort_keys=True)+"\n").encode()
            f.write(raw);h.update(raw);size+=len(raw)
    return {"sha256":h.hexdigest(),"rows":SERIES,"samples":SERIES*SAMPLES,"bytes":size,
            "generation_s":round(time.perf_counter()-begin,3),
            "source":"synthetic stable series with 15s timestamps but bulk ingestion per ID"}
def count_physical(table):
    sample_target=inner(table,"samples")
    n=int(sql("SELECT sum(length(samples)) FROM "+sample_target+" FORMAT TSV").strip())
    if n!=SERIES*SAMPLES:raise RuntimeError("invalid raw samples "+str(n))
    return {"raw_samples":n,"parts":stats(table)}
def sample_plan(table,selector):
    tags=inner(table,"tags");samples=inner(table,"samples")
    if selector=="cluster":tag="tags['cluster']='cluster-01'"
    elif selector=="namespace":tag="tags['namespace']='namespace-03'"
    elif selector=="both":tag="tags['cluster']='cluster-01' AND tags['namespace']='namespace-03'"
    else:raise ValueError(selector)
    q=(f"SELECT sum(length(samples)) FROM {samples} WHERE id IN "
       f"(SELECT id FROM {tags} WHERE metric_name={sql_string(METRIC)} AND {tag}) "
       "SETTINGS max_threads=4,optimize_trivial_count_query=0")
    explain=sql("EXPLAIN PLAN indexes=1 "+q)[:50000]
    count=int(sql(q+" FORMAT TSV").strip())
    return {"actual_sample_count":count,"plan":explain}
def measure(bin,restart,table,case,phase,reference,run_id):
    pid=server_restart(restart,bin,f"dense_{run_id}_{case}_{phase}")
    wait_no_merges()
    q=(f"SELECT * FROM prometheusQueryRange({table},{sql_string(QUERIES[case])},"
       f"{BASE+3600},{BASE+3600},3600) SETTINGS max_threads=4 FORMAT JSONEachRow")
    timings=[]
    for k in range(9):
        id=f"dense_{run_id}_{case}_{phase}_{k}"
        r=monitored(q,id,pid)
        if not r["ok"]:raise RuntimeError("dense native query failed: "+r["error"])
        got=r.pop("points")
        if reference is None:reference=got
        else:assert_same(reference,got,case,phase)
        if not got:raise RuntimeError("dense query unexpectedly empty")
        timings.append({"k":k,"wall_ms":r["duration_ms"],"rss_peak_kib":r["rss_peak_kib"],
                       "point_count":len(got),"warmup":k<3})
    data=timings[3:]
    return reference,{"variant":table,"phase":phase,"case":case,
       "wall_p50_ms":pct([r["wall_ms"] for r in data],.5),
       "wall_p95_ms":pct([r["wall_ms"] for r in data],.95),
       "rss_p50_mib":pct([r["rss_peak_kib"]/1024 for r in data],.5),
       "trials":timings}
def main():
    p=argparse.ArgumentParser()
    for x in ("binary","restart-helper","output","fixture"):p.add_argument("--"+x,required=True)
    a=p.parse_args()
    out=pathlib.Path(a.output);out.mkdir(parents=True,exist_ok=True)
    report={"schema":1,"cases":{},"controls":{},"errors":[],
      "limitation":"240 samples/series inserted in one batch, NOT real every-15-second remote-write ingestion"}
    save(out/"report.json",report)
    path=pathlib.Path(a.fixture)
    report["fixture"]=fixture(path)
    for name,table in TABLES.items():
        labels=[] if name=="default" else ["cluster","namespace"]
        sql(definition(table,labels))
        st=time.monotonic()
        imp=streamed_insert(table,str(path))
        assert imp["rows"]==SERIES
        report["controls"][name]={"insert_seconds":round(time.monotonic()-st,3),
             "import":imp,"optimize":optimize_table(table)}
        wait_no_merges()
        report["controls"][name]["physical"]=count_physical(table)
        save(out/"report.json",report)
    run_id=os.getenv("GITHUB_RUN_ID","local")
    cases=list(QUERIES);random.Random(100903).shuffle(cases)
    for case in cases:
        reference=None;measurements=[]
        for i,var in enumerate(("A","B","B","A")):
            name="default" if var=="A" else "cluster_namespace"
            reference,x=measure(a.binary,a.restart_helper,TABLES[name],case,f"{var}{i}",reference,run_id)
            measurements.append(x)
            report["cases"][case]={"numerical_parity":True,"measurements":measurements}
            save(out/"report.json",report)
            print("DENSE_LOCALITY_CASE",json.dumps({"case":case,"phase":f"{var}{i}",
               "variant":name,"wall_ms":x["wall_p50_ms"],"rss_mib":x["rss_p50_mib"]},
               separators=(",",":")),flush=True)
        aa=[x["wall_p50_ms"] for x in measurements if x["phase"].startswith("A")]
        bb=[x["wall_p50_ms"] for x in measurements if x["phase"].startswith("B")]
        baseline=sum(aa)/2;candidate=sum(bb)/2
        drift=abs(aa[0]-aa[1])/baseline
        if drift>.30:report["errors"].append({"case":case,"error":"baseline drift","fraction":drift})
        report["cases"][case]["compared"]={"baseline_ms":round(baseline,3),
            "candidate_ms":round(candidate,3),"speedup":round(baseline/candidate,3),
            "baseline_drift":round(drift,3)}
        save(out/"report.json",report)
    for name,table in TABLES.items():
        report["controls"][name]["sample_plans"]={}
        for selector in ("cluster","namespace","both"):
            report["controls"][name]["sample_plans"][selector]=sample_plan(table,selector)
    report["passed"]=not report["errors"] and len(report["cases"])==4
    save(out/"report.json",report)
    print("DENSE_LOCALITY_DONE",json.dumps({"passed":report["passed"],"source":report["fixture"],
         "cases":{k:v["compared"] for k,v in report["cases"].items()}},separators=(",",":")),flush=True)
    if not report["passed"]:raise SystemExit(1)
if __name__=="__main__":main()
