#!/usr/bin/env python3
"""Synthetic correlated stage/project stats: actual vs estimated SELECT and JOIN rows."""
import argparse,datetime,hashlib,json,os,pathlib,statistics,sys,time,urllib.request,urllib.parse,urllib.error
sys.path.insert(0,".github/ts-lab-perf")
sys.path.insert(0,".github/ts-lab-bench")
from bench_granularity_v11 import inner
from bench_bucket_cxx_abca import normalize_any,assert_same
METRIC="tslab_cbo_correlated_labels"
BASE=datetime.datetime(2026,10,19,tzinfo=datetime.timezone.utc)
URL="http://127.0.0.1:18191"
PRED={"impossible":(7,0),"correlated":(6,0),"other_pair":(7,1),
      "stage":(7,None),"project":(None,0),"wide":(None,None)}
def lit(x):return "'"+str(x).replace("'","\\'")+"'"
def request(q,qid=None,data=None):
    params={"wait_end_of_query":1}
    if qid:params["query_id"]=qid
    if data is not None:params["query"]=q
    t=time.monotonic()
    req=urllib.request.Request(URL+"/?"+urllib.parse.urlencode(params),
       data=q.encode() if data is None else data,method="POST")
    try:
        with urllib.request.urlopen(req,timeout=240) as resp:
            return round((time.monotonic()-t)*1000,3),resp.read().decode()
    except urllib.error.HTTPError as exc:
        raise RuntimeError(exc.read().decode(errors="replace")[:1300]+" QUERY "+q[:220]) from exc
def query(q):return [json.loads(s) for s in request(q+" FORMAT JSONEachRow")[1].splitlines() if s.strip()]
def save(path,data):
    pathlib.Path(path).parent.mkdir(parents=True,exist_ok=True)
    pathlib.Path(path).write_text(json.dumps(data,indent=2,default=str)+"\n")
def maybe(q,out,key,raw=False):
    try:out[key]=request(q)[1][:90000] if raw else query(q)
    except Exception as exc:out[key+"_error"]=str(exc)[:1200]
def when(i):return (i//152)%14
def matched(i,c):
    a,b=PRED[c];j=i%152
    return (a is None or j%38==a) and (b is None or j%8==b)
def expected(n,c,window):
    return sum(1 for i in range(n) if matched(i,c) and (window=="all" or when(i)==13))
def fixture(n,path):
    sha=hashlib.sha256()
    with open(path,"wb") as f:
        for i in range(n):
            j=i%152
            t=BASE+datetime.timedelta(days=when(i),hours=12,minutes=j%60)
            line=json.dumps({"metric_name":METRIC,
              "tags":{"stage":f"stage-{j%38:04d}","project":f"project-{j%8:02d}",
                      "build":f"build-{i:08d}","branch":"main","job":"jenkins"},
              "samples":[[t.strftime("%Y-%m-%d %H:%M:%S.000"),1.0+i%101]]},
              separators=(",",":")).encode()+b"\n"
            f.write(line);sha.update(line)
    return {"sha256":sha.hexdigest(),"rows":n,"bytes":os.path.getsize(path),
            "formula":"j=i%152; stage=j%38; project=j%8; day=(i//152)%14",
            "interval":"one point per series"}
def import_rows(table,file):
    rows=0
    with open(file,"rb") as f:
        while 1:
            lines=[f.readline() for _ in range(1500)]
            lines=[x for x in lines if x]
            if not lines:break
            request(f"INSERT INTO {table} (metric_name,tags,samples) FORMAT JSONEachRow",data=b"".join(lines))
            rows+=len(lines)
    return rows
def catalog(table):
    out={}
    for k in ("tags","timeranges","samples"):
        name=inner(table,k).split(chr(96))[1];d={"table":name}
        maybe("SELECT name,type,statistics FROM system.columns WHERE database='default' AND table="+lit(name),d,"columns")
        maybe("SELECT column,statistics,estimates.cardinality AS ndv,rows,marks FROM system.parts_columns WHERE database='default' AND table="+lit(name)+" AND active",d,"stats")
        maybe("SELECT name,rows,marks,bytes_on_disk FROM system.parts WHERE database='default' AND table="+lit(name)+" AND active",d,"parts")
        out[k]=d
    return out
def condition(kind,c):
    s,p=PRED[c];z="metric_name="+lit(METRIC)
    if s is not None:
        z+=" AND "+("stage" if kind=="column" else "tags['stage']")+"="+lit(f"stage-{s:04d}")
    if p is not None:
        z+=" AND "+("project" if kind=="column" else "tags['project']")+"="+lit(f"project-{p:02d}")
    return z
def join_query(table,kind,c,window,algo,stats,checksum=False):
    st=int((BASE+datetime.timedelta(days=13 if window=="last" else 0)).timestamp())*1000
    en=int((BASE+datetime.timedelta(days=14)).timestamp())*1000
    tr=inner(table,"timeranges");tg=inner(table,"tags")
    part=f"SELECT id FROM {tg} WHERE {condition(kind,c)}"
    tm=f"max_time>=fromUnixTimestamp64Milli({st}) AND min_time<=fromUnixTimestamp64Milli({en})"
    joined=f"SELECT a.id FROM ({part}) a LEFT JOIN (SELECT id,toUInt8(1) AS hit FROM {tr} WHERE {tm}) b USING(id) WHERE b.hit=1"
    agg=("count() AS n,toString(sum(sipHash64(id))) AS s,toString(groupBitXor(sipHash64(id))) AS x"
         if checksum else "count() AS n")
    return f"SELECT {agg} FROM ({joined}) SETTINGS max_threads=4,max_memory_usage=3500000000,use_statistics={stats},join_algorithm={lit(algo)},optimize_trivial_count_query=0"
def compare(table,kind,c,window,n,report):
    out={"expected":expected(n,c,window),"by_stats":{}}
    for stats in (0,1):
        d={"plans":{},"runs":[]}
        out["by_stats"][str(stats)]=d
        seq=("full_sorting_merge","hash","hash","full_sorting_merge","auto")
        baseline=None
        for i,algo in enumerate(seq):
            statement=join_query(table,kind,c,window,algo,stats)
            if algo not in d["plans"]:
                maybe("EXPLAIN PLAN indexes=1 "+statement,d["plans"],algo,True)
                maybe("EXPLAIN PIPELINE "+statement,d["plans"],algo+"_pipeline",True)
            qid=f"corr_{kind}_{c}_{window}_{stats}_{i}_{os.getenv('GITHUB_RUN_ID','local')}"
            ms,raw=request(statement+" FORMAT JSONEachRow",qid)
            got=json.loads(raw.strip())
            if got["n"]!=out["expected"]:
                raise AssertionError(f"{qid}: expected {out['expected']}, got {got['n']}")
            if i in (0,1,4):
                check=json.loads(request(join_query(table,kind,c,window,algo,stats,True)+" FORMAT JSONEachRow")[1].strip())
                if baseline is None:baseline=check
                if check!=baseline:raise AssertionError(f"{qid}: JOIN ID checksum mismatch")
            d["runs"].append({"qid":qid,"algo":algo,"ms":ms,"n":got["n"]})
        a=[x["ms"] for x in d["runs"] if x["algo"]=="full_sorting_merge"]
        b=[x["ms"] for x in d["runs"] if x["algo"]=="hash"]
        d["paired"]={"merge_ms":statistics.median(a),"hash_ms":statistics.median(b),
                     "merge_over_hash":round(statistics.median(a)/statistics.median(b),3),
                     "merge_drift":round(abs(a[0]-a[-1])/statistics.mean(a),3)}
        d["valid_drift"]=d["paired"]["merge_drift"]<=0.30
    report.setdefault("results",{}).setdefault(kind,{})[c+"_"+window]=out
def profiles(report):
    request("SYSTEM FLUSH LOGS")
    ids=[x["qid"] for fam in report["results"].values() for c in fam.values()
         for s in c["by_stats"].values() for x in s["runs"]]
    logs=query("SELECT query_id,query_duration_ms,memory_usage,read_rows,read_bytes,ProfileEvents FROM system.query_log WHERE type='QueryFinish' AND query_id IN ("+",".join(map(lit,ids))+")")
    byid={x["query_id"]:x for x in logs}
    report["log_coverage"]={"expected":len(ids),"actual":len(byid)}
    if len(ids)!=len(byid):raise AssertionError("Incomplete query_log evidence")
    for fam in report["results"].values():
        for c in fam.values():
            for s in c["by_stats"].values():
                for x in s["runs"]:
                    z=byid[x["qid"]];ev=z.get("ProfileEvents") or {}
                    x.update({"cpu_ms":round(ev.get("OSCPUVirtualTimeMicroseconds",0)/1000,3),
                              "memory":z["memory_usage"],"read_rows":z["read_rows"],"read_bytes":z["read_bytes"]})
    for fam in report["results"].values():
        for label,case in fam.items():
            if not label.startswith(("impossible_last","correlated_last")):continue
            for trial in case["by_stats"]["1"]["runs"][:2]:
                qid=trial["qid"];d={}
                maybe("SELECT plan_step_name,name,count() AS processors,sum(elapsed_us) AS elapsed_us,sum(input_rows) AS input_rows,sum(output_rows) AS output_rows FROM system.processors_profile_log WHERE query_id="+lit(qid)+" GROUP BY plan_step_name,name ORDER BY elapsed_us DESC LIMIT 30",d,"processors")
                maybe("SELECT trace_type,arrayStringConcat(arrayReverse(arrayFilter(x -> x != '',symbols)),';') AS stack,count() AS samples,sum(size) AS bytes FROM system.trace_log WHERE query_id="+lit(qid)+" GROUP BY trace_type,stack ORDER BY samples DESC LIMIT 80",d,"stacks")
                maybe("SELECT table,index_names,total_granules,granules_after,index_selectivities,predicate_expression,input_rows,passed_rows FROM system.predicate_statistics_log WHERE query_id="+lit(qid)+" LIMIT 100",d,"predicates")
                report.setdefault("profiles",{})[qid]=d
def native(table):
    expr=f"sum by(stage,project)(avg_over_time({METRIC}[1d]))"
    st=int((BASE+datetime.timedelta(hours=12,minutes=30)).timestamp())
    q=f"SELECT * FROM prometheusQueryRange({table},{lit(expr)},{st},{st+13*86400},86400) SETTINGS max_threads=4 FORMAT JSONEachRow"
    ms,text=request(q)
    out=normalize_any(text.encode())
    if len(out)<200:raise AssertionError(f"Native query not populated: {len(out)}")
    return out,{"ms":ms,"points":len(out),"promql":expr}
def main():
    p=argparse.ArgumentParser()
    p.add_argument("--fixture",required=True);p.add_argument("--output",required=True)
    p.add_argument("--count",type=int,default=152000)
    a=p.parse_args();assert 152<=a.count<=304000 and a.count%152==0
    root=pathlib.Path(a.output);root.mkdir(parents=True,exist_ok=True)
    r={"count":a.count,"results":{},"errors":[],"scope":"synthetic labels, no production"}
    try:
        r["fixture"]=fixture(a.count,a.fixture)
        assert expected(a.count,"impossible","all")==0
        assert expected(a.count,"correlated","all")==a.count//152
        baseline=None
        for kind,table in (("map","corr_map"),("column","corr_column")):
            extra=",tags_to_columns={'stage':'stage','project':'project'}" if kind=="column" else ""
            request(f"CREATE TABLE {table} ENGINE=TimeSeries SETTINGS recent_samples_ttl_seconds=0"+extra)
            assert import_rows(table,a.fixture)==a.count
            assert int(request(f"SELECT count() FROM {table} FORMAT TSV")[1].strip())==a.count
            r.setdefault("before",{})[kind]=catalog(table)
            if kind=="column":
                names={x["name"] for x in r["before"][kind]["tags"]["columns"]}
                assert {"stage","project"}.issubset(names),names
            for name in ("tags","timeranges","samples"):
                phys=inner(table,name)
                request(f"OPTIMIZE TABLE {phys} FINAL SETTINGS optimize_throw_if_noop=0")
                try:request(f"ALTER TABLE {phys} MATERIALIZE STATISTICS ALL SETTINGS mutations_sync=2")
                except Exception as ex:r.setdefault("materialize_errors",[]).append(str(ex)[:900])
            r.setdefault("after",{})[kind]=catalog(table)
            got,meta=native(table)
            r.setdefault("native",{})[kind]=meta
            if baseline is None:baseline=got
            else:r["native"]["parity"]=assert_same(baseline,got,"correlated","mapped-vs-columns")
        request("SYSTEM STOP MERGES")
        for kind,table in (("map","corr_map"),("column","corr_column")):
            for c in PRED:
                for window in ("last","all"):
                    compare(table,kind,c,window,a.count,r)
                    save(root/"report.json",r)
        profiles(r)
        r["passed"]=all(not v.get("materialize_errors") for v in [r])
    except Exception as ex:
        r["errors"].append(str(ex)[:1800]);r["passed"]=False
        print("CORR_FAILURE",r["errors"][-1],flush=True)
    finally:
        save(root/"report.json",r)
        compact={"passed":r.get("passed"),"errors":r["errors"],"fixture":r.get("fixture"),
                 "native":r.get("native"),"log_coverage":r.get("log_coverage"),
                 "cases":{k:{v:{"expected":c["expected"],
                       "stats0":c["by_stats"].get("0",{}).get("paired"),
                       "stats1":c["by_stats"].get("1",{}).get("paired")}
                       for v,c in fm.items()} for k,fm in r["results"].items()}}
        save(root/"compact.json",compact)
        print("CORR_RESULT",json.dumps(compact,separators=(",",":")),flush=True)
    if not r["passed"]:raise SystemExit(1)
if __name__=="__main__":main()
