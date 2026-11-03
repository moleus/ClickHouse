#!/usr/bin/env python3
"""Native PromQL whole-metric memory diagnostic by disjoint stage partitions.

Each stage is in exactly one partition. Aggregation is avg by(stage), hence
union of partition outputs must equal the whole metric output. For 14d the
whole query may OOM: preserve that error as evidence, do not claim parity.
No timing checksum, no inference from single-trial wall latency.
"""
import argparse,json,os,pathlib,sys,time
sys.path.insert(0,".github/ts-lab-perf")
sys.path.insert(0,".github/ts-lab-bench")
from bench_granularity_v11 import sql,querylogs
from bench_bucket_cxx_abca import normalize_any,assert_same,post
from bench_filtered_selector_v4_sql import sql_string
DAY=86400
SOURCE_SHA="8f3a36846f80d6be5a8604d142e934399f2687094766c57bf105e43977f52858"
METRIC="jenkins_ci_95k_daily"
def save(p,v):
    pathlib.Path(p).parent.mkdir(parents=True,exist_ok=True)
    pathlib.Path(p).write_text(json.dumps(v,indent=2,default=str)+"\n")
def query(table,selector,start,end):
    expr=f"avg by(stage)(avg_over_time({METRIC}{selector}[1d]))"
    return f"SELECT * FROM prometheusQueryRange({table},{sql_string(expr)},{start},{end},{DAY}) SETTINGS max_threads=4,max_memory_usage=3500000000 FORMAT JSONEachRow"
def selectors(k):
    groups=[list(range(i,min(i+k,38))) for i in range(0,38,k)]
    return [('{stage=~"'+ "|".join(f"stage-{i:04d}" for i in g) + '"}',g) for g in groups]
def run(qid,q):
    start=time.monotonic()
    ms,body=post("http://127.0.0.1:18191",q,qid)
    return {"qid":qid,"client_ms":round(ms,3),"values":normalize_any(body),"elapsed_s":round(time.monotonic()-start,3)}
def logdetails(qid):
    v=next((x for x in querylogs(qid) if x.get("type")=="QueryFinish" and x.get("query_id")==qid),None)
    if v is None:raise AssertionError("Missing QueryFinish "+qid)
    e=v.get("ProfileEvents",{})
    return {"query_ms":v["query_duration_ms"],"cpu_ms":round(e.get("OSCPUVirtualTimeMicroseconds",0)/1000,3),
            "peak_query_bytes":v.get("memory_usage"),"read_rows":v.get("read_rows"),"read_bytes":v.get("read_bytes"),
            "selected_marks":e.get("SelectedMarks")}
def main():
    p=argparse.ArgumentParser()
    p.add_argument("--manifest",required=True);p.add_argument("--days",type=int,choices=[1,7,14],required=True)
    p.add_argument("--table",default="churn_ts");p.add_argument("--out",required=True)
    args=p.parse_args();root=pathlib.Path(args.out);root.mkdir(parents=True,exist_ok=True)
    m=json.loads(pathlib.Path(args.manifest).read_text())
    if (m["sha256"],m["series"],m["days"])!=(SOURCE_SHA,1330000,14):
        raise AssertionError("Fixture SHA or cardinality mismatch")
    end=int(m["end"]);start=end-(args.days-1)*DAY
    report={"scope":"native full PromQL partitioned stage group memory diagnostic","days":args.days,
            "fixture_sha256":SOURCE_SHA,"source":"same exact frozen 1.33m series",
            "full":{},"groups":{},"errors":[],"passed":False}
    try:
        # Control full query FIRST. OOM is an expected possible outcome at 14d.
        qid=f"nativepart_full_{args.days}_{os.getenv('GITHUB_RUN_ID','local')}"
        try:
            full=run(qid,query(args.table,"",start,end))
            if len(full["values"])!=38*args.days:raise AssertionError("Full native result wrong point count")
            report["full"]={"ok":True,"count":len(full["values"]),"query_id":qid}
        except Exception as ex:
            report["full"]={"ok":False,"error":str(ex)[:2100],"query_id":qid}
            full=None
        save(root/"report.json",report)
        for width in (10,5):
            parts=selectors(width);expected=38*args.days
            joined={};runs=[];group_error=False
            for i,(selector,group) in enumerate(parts):
                part_qid=f"nativepart_{width}_{args.days}_{i}_{os.getenv('GITHUB_RUN_ID','local')}"
                try:
                    trial=run(part_qid,query(args.table,selector,start,end))
                    got=trial["values"]
                    expected_labels={f"stage-{s:04d}" for s in group}
                    if len(got)!=len(group)*args.days:
                        raise AssertionError(f"Wrong partition size {i}: got {len(got)}, expected {len(group)*args.days}")
                    for key,val in got.items():
                        # normalize_any maps a canonical (labels,timestamp) key to numeric value
                        if key in joined:raise AssertionError("Duplicate stage between disjoint partitions")
                        joined[key]=val
                    runs.append({"group":group,"selector":selector,"query_id":part_qid,
                                 "points":len(got),"client_ms":trial["client_ms"]})
                except Exception as e:
                    runs.append({"group":group,"query_id":part_qid,"error":str(e)[:1800]});group_error=True
                save(root/"report.json",report)
            try:sql("SYSTEM FLUSH LOGS")
            except Exception as e:report["errors"].append("FLUSH_LOGS "+str(e)[:350])
            for x in runs:
                if "error" not in x:
                    try:x.update(logdetails(x["query_id"]))
                    except Exception as e:x["log_error"]=str(e)[:800];group_error=True
            block={"groups":len(parts),"width":width,"trials":runs,"result_points":len(joined),
                   "passed":not group_error and len(joined)==expected}
            if full is not None and block["passed"]:
                parity=assert_same(full["values"],joined,"whole",f"stagepartition{width}")
                block["numeric_parity"]=parity
            if not block["passed"]:report["errors"].append(f"Partition width={width} failed")
            report["groups"][str(width)]=block
            print("NATIVE_PARTITION_RESULT",json.dumps({"days":args.days,"width":width,"groups":len(parts),
                  "points":len(joined),"passed":block["passed"],"full_ok":bool(full)},separators=(",",":")),flush=True)
            save(root/"report.json",report)
        report["passed"]=not report["errors"] and all(v["passed"] for v in report["groups"].values())
    except Exception as e:
        report["errors"].append(str(e)[:2000])
    finally:
        save(root/"report.json",report)
        print("NATIVE_PARTITION_DONE",json.dumps({"days":args.days,"passed":report["passed"],
              "full":report["full"],"errors":report["errors"]},separators=(",",":")),flush=True)
    if not report["passed"]:raise SystemExit(1)
if __name__=="__main__":main()
