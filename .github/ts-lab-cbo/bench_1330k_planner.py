#!/usr/bin/env python3
"""1.33 million-series JOIN experiment, exact SQL checksum, profile and plan capture.
The SQL probe is not a native PromQL benchmark.
"""
import argparse,json,pathlib,sys,urllib.request,urllib.parse,urllib.error,time,statistics,os
sys.path.insert(0,".github/ts-lab-perf")
sys.path.insert(0,".github/ts-lab-layout")
from bench_granularity_v11 import inner
from bench_layout_full import CANONICAL
NAME="jenkins_ci_95k_daily"
DAY=86400
CASES={"stage":"AND tags['stage']='stage-0007'",
       "project":"AND tags['project']='project-00'",
       "regex":"AND match(tags['branch'],'^(main|feature-0[0-9])$')",
       "whole":""}
def literal(s): return "'"+str(s).replace("'","\\'")+"'"
def sql(query,qid=None):
    params={"wait_end_of_query":1}
    if qid: params["query_id"]=qid
    start=time.perf_counter()
    try:
        with urllib.request.urlopen(urllib.request.Request(
            "http://127.0.0.1:18191/?"+urllib.parse.urlencode(params),
            data=query.encode(),method="POST"),timeout=240) as response:
            return round((time.perf_counter()-start)*1000,3),response.read().decode()
    except urllib.error.HTTPError as exc:
        raise RuntimeError(exc.read().decode(errors="replace")[:1400]) from exc
def rows(q):
    return [json.loads(s) for s in sql(q+" FORMAT JSONEachRow")[1].splitlines() if s.strip()]
def attempt(q,where,key,plain=False):
    try:where[key]=sql(q)[1][:50000] if plain else rows(q)
    except Exception as e:where[key+"_error"]=str(e)[:1600]
def save(p,j):
    pathlib.Path(p).parent.mkdir(parents=True,exist_ok=True)
    pathlib.Path(p).write_text(json.dumps(j,indent=2,default=str)+"\n")
def query(table,condition,start,end,kind,checksum=False):
    tag=inner(table,"tags"); tim=inner(table,"timeranges")
    filter_tags=f"metric_name={literal(NAME)} {condition}"
    filter_time=f"max_time>=fromUnixTimestamp64Milli({1000*start}) AND min_time<=fromUnixTimestamp64Milli({1000*end})"
    if kind=="semi":
        ids=f"SELECT id FROM {tag} WHERE {filter_tags} AND id IN (SELECT id FROM {tim} WHERE {filter_time})"
        algorithm="full_sorting_merge"
    else:
        ids=(f"SELECT a.id FROM (SELECT id FROM {tag} WHERE {filter_tags}) a "
             f"LEFT JOIN (SELECT id,toUInt8(1) AS matched FROM {tim} WHERE {filter_time}) b USING(id) "
             f"WHERE b.matched=1")
        algorithm=kind
    # The hot path must NOT evaluate expensive checksum hashes per result ID.
    # Correctness is checked separately once per algorithm before timing.
    agg=("count() AS n,toString(sum(sipHash64(id))) AS check1,"
         "toString(groupBitXor(sipHash64(id))) AS check2") if checksum else "count() AS n"
    return (f"SELECT {agg} FROM ({ids}) "
            f"SETTINGS max_threads=4,max_memory_usage=3500000000,"
            f"use_statistics=1,join_algorithm={literal(algorithm)},optimize_trivial_count_query=0")
def capture(qid,out):
    attempt("SELECT query_duration_ms,memory_usage,read_rows,read_bytes,ProfileEvents "
            "FROM system.query_log WHERE type='QueryFinish' AND query_id="+literal(qid),out,"log")
    attempt("SELECT plan_step_name,name,count() AS processors,sum(elapsed_us) AS work_us,"
            "sum(input_wait_elapsed_us) AS in_wait_us,sum(output_wait_elapsed_us) AS out_wait_us,"
            "sum(input_rows) AS input_rows,sum(output_rows) AS output_rows "
            "FROM system.processors_profile_log WHERE query_id="+literal(qid)+
            " GROUP BY plan_step_name,name ORDER BY work_us DESC LIMIT 80",out,"processors")
    attempt("SELECT trace_type,arrayStringConcat(arrayReverse(arrayFilter(x -> x != '',symbols)),';') AS stack,"
            "count() AS n,sum(size) AS bytes FROM system.trace_log WHERE query_id="+literal(qid)+
            " GROUP BY trace_type,stack ORDER BY n DESC LIMIT 200",out,"flamegraph")
def main():
    p=argparse.ArgumentParser()
    p.add_argument("--case",required=True,choices=sorted(CASES))
    p.add_argument("--manifest",required=True)
    p.add_argument("--output",required=True)
    p.add_argument("--days",default="1,3,7,14",help="Comma separated positive day windows")
    a=p.parse_args()
    days_list=[int(x) for x in a.days.split(",")]
    if not days_list or len(set(days_list))!=len(days_list) or any(x<1 or x>14 for x in days_list):
        raise ValueError("Invalid day window list")
    dest=pathlib.Path(a.output);dest.mkdir(parents=True,exist_ok=True)
    meta=json.loads(pathlib.Path(a.manifest).read_text())
    if meta["sha256"]!=CANONICAL or meta["series"]!=1330000:
        raise AssertionError("Canonical 1.33m fixture missing")
    report={"case":a.case,"fixture":meta["sha256"],"windows":{},"errors":[]}
    save(dest/"report.json",report)
    try:
        try:
            n=int(sql("SELECT count() FROM churn_ts FORMAT TSV")[1].strip())
            if n!=1330000:raise AssertionError("Missing TimeSeries data "+str(n))
        except Exception:raise
        for kind in ("tags","timeranges","samples"):
            raw=inner("churn_ts",kind).split(chr(96))[1]
            o={}
            attempt("SELECT column,statistics,estimates.cardinality AS ndv,rows,marks "
                    "FROM system.parts_columns WHERE active AND database='default' AND table="+literal(raw),o,"statistics")
            attempt("SELECT name,rows,marks,bytes_on_disk FROM system.parts "
                    "WHERE active AND database='default' AND table="+literal(raw),o,"parts")
            report.setdefault("catalog",{})[kind]=o
        for days in days_list:
            start=meta["end"]-days*DAY
            end=meta["end"]
            slot={"days":days,"variants":{},"trials":[],"errors":[]}
            report["windows"][str(days)]=slot
            reference_checksum=None
            for kind in ("full_sorting_merge","hash","parallel_hash","auto","semi"):
                q=query("churn_ts",CASES[a.case],start,end,kind)
                entry={"sql":q,"checksum_sql":query("churn_ts",CASES[a.case],start,end,kind,True)}
                slot["variants"][kind]=entry
                attempt("EXPLAIN PLAN indexes=1 "+q,entry,"plan",plain=True)
                try:
                    check=json.loads(sql(entry["checksum_sql"]+" FORMAT JSONEachRow")[1].strip())
                    if int(check["n"])==0:raise AssertionError("empty digest")
                    if reference_checksum is None:reference_checksum=check
                    if check!=reference_checksum:raise AssertionError("checksum mismatch across JOIN algorithms")
                    entry["checksum_verified"]=True
                except Exception as exc:
                    slot["errors"].append({"checksum":kind,"error":str(exc)[:800]})
            # Warm both compared algorithms before A/B/B/A to limit cold-start drift.
            for warm_kind in ("full_sorting_merge","hash"):
                for _ in range(2):
                    sql(slot["variants"][warm_kind]["sql"]+" FORMAT JSONEachRow")
            baseline=None
            order=["full_sorting_merge","hash","hash","full_sorting_merge",
                   "auto","semi","parallel_hash","full_sorting_merge","hash"]
            for ix,kind in enumerate(order):
                q=slot["variants"][kind]["sql"]
                qid=f"planner1330_{a.case}_{days}_{ix}_{os.getenv('GITHUB_RUN_ID','local')}"
                try:
                    ms,response=sql(q+" FORMAT JSONEachRow",qid)
                    data=[json.loads(x) for x in response.splitlines() if x.strip()]
                    if len(data)!=1 or int(data[0]["n"])==0:
                        raise AssertionError("Empty or invalid digest")
                    if baseline is None:baseline=data[0]
                    if data[0]!=baseline:raise AssertionError("JOIN checksum mismatch "+kind)
                    rec={"kind":kind,"query_id":qid,"ms":ms,"count":int(data[0]["n"])}
                    slot["trials"].append(rec)
                    print("PLANNER1330_TRIAL",json.dumps({"case":a.case,"days":days,**rec}),flush=True)
                except Exception as exc:
                    slot["errors"].append({kind:str(exc)[:1100]})
                save(dest/"report.json",report)
            sql("SYSTEM FLUSH LOGS")
            a1=[x["ms"] for x in slot["trials"] if x["kind"]=="full_sorting_merge"]
            b1=[x["ms"] for x in slot["trials"] if x["kind"]=="hash"]
            if len(a1)==len(b1)==3:
                slot["comparison"]={"merge_ms":statistics.median(a1),
                    "hash_ms":statistics.median(b1),
                    "merge_over_hash_speedup":round(statistics.median(b1)/statistics.median(a1),4),
                    "merge_drift":round(abs(a1[0]-a1[-1])/statistics.mean(a1),4)}
                if slot["comparison"]["merge_drift"] > .30:
                    slot["errors"].append({"baseline_drift":slot["comparison"]["merge_drift"],
                                           "reject":"A/B/B/A control drift exceeds 30 percent"})
            for x in slot["trials"][:2]:
                qid=x["query_id"];slot.setdefault("profiles",{})[qid]={}
                capture(qid,slot["profiles"][qid])
            ids=[x["query_id"] for x in slot["trials"][:2]]
            if ids:
                attempt("SELECT query_id,table,index_names,total_granules,granules_after,"
                        "index_selectivities,predicate_expression,input_rows,passed_rows "
                        "FROM system.predicate_statistics_log WHERE query_id IN ("+
                        ",".join(map(literal,ids))+") LIMIT 400",slot,"predicate_stats")
            save(dest/"report.json",report)
            print("PLANNER1330_WINDOW",json.dumps({"case":a.case,"days":days,"comparison":slot.get("comparison"),
                   "errors":slot["errors"]}),flush=True)
        report["passed"]=len(report["windows"])==len(days_list) and all(not w["errors"] for w in report["windows"].values())
    except Exception as exc:
        report["errors"].append(str(exc)[:2000]);report["passed"]=False
    finally:
        save(dest/"report.json",report)
        save(dest/"compact.json",{"passed":report.get("passed"),"case":a.case,"errors":report["errors"],
             "results":{day:w.get("comparison") for day,w in report["windows"].items()}})
        print("PLANNER1330_RESULT",json.dumps({"passed":report.get("passed"),"case":a.case,"errors":report["errors"],
             "results":{day:w.get("comparison") for day,w in report["windows"].items()}},separators=(",",":")),flush=True)
    if not report["passed"]:raise SystemExit(1)
if __name__=="__main__":main()
