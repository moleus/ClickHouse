#!/usr/bin/env python3
"""Reproducible synthetic TimeSeries statistics and JOIN algorithm experiment."""
import argparse, datetime, hashlib, json, os, pathlib, statistics, sys, time
import urllib.request, urllib.parse, urllib.error

sys.path.insert(0, ".github/ts-lab-perf")
sys.path.insert(0, ".github/ts-lab-bench")
from bench_granularity_v11 import inner
from bench_bucket_cxx_abca import normalize_any, assert_same

URL = "http://127.0.0.1:18191"
BASE = datetime.datetime(2026, 10, 19, tzinfo=datetime.timezone.utc)
DAY = 86400
CASES = {
    "node": ("node_cpu_seconds_total", "cluster", "cluster-01"),
    "pod": ("container_cpu_usage_seconds_total", "namespace", "ns-03"),
    "http": ("http_server_requests_total", "service", "service-03"),
    "ci": ("jenkins_avs_build_stage_duration", "stage", "stage-0007"),
}

def lit(value):
    return "'" + str(value).replace("\\", "\\\\").replace("'", "\\'") + "'"

def execute(query, qid=None, data=None, timeout=180):
    params = {"wait_end_of_query": 1}
    if qid:
        params["query_id"] = qid
    if data is None:
        payload = query.encode()
    else:
        params["query"] = query
        payload = data
    request = urllib.request.Request(URL + "/?" + urllib.parse.urlencode(params),
                                     data=payload, method="POST")
    started = time.monotonic()
    try:
        with urllib.request.urlopen(request, timeout=timeout) as resp:
            return round((time.monotonic()-started)*1000, 3), resp.read().decode()
    except urllib.error.HTTPError as exc:
        raise RuntimeError(f"SQL HTTP {exc.code}: {exc.read().decode(errors='replace')[:1200]} for {query[:500]}") from exc

def jsonrows(query, qid=None):
    _, body = execute(query + " FORMAT JSONEachRow", qid)
    return [json.loads(s) for s in body.splitlines() if s.strip()]

def optional(query, target, name, raw=False):
    try:
        target[name] = execute(query)[1][:55000] if raw else jsonrows(query)
    except Exception as e:
        target[name + "_error"] = str(e)[:1000]

def save(path, value):
    pathlib.Path(path).parent.mkdir(parents=True, exist_ok=True)
    pathlib.Path(path).write_text(json.dumps(value, indent=2, sort_keys=True, default=str) + "\n")

def family(workload, i):
    if workload == "node":
        name = ("node_cpu_seconds_total", "node_memory_MemAvailable_bytes", "node_network_receive_bytes_total")[i%3]
        return name, {"job":"node-exporter", "cluster":f"cluster-{i%8:02}", "region":"eu-north-1",
                      "instance":f"node-{(i*7919)&0xffffffff:08x}", "cpu":str(i%16),
                      "mode":("idle","system","user")[i%3]}
    if workload == "pod":
        name = ("container_cpu_usage_seconds_total","kube_pod_status_phase",
                "kube_pod_container_status_restarts_total")[i%3]
        return name, {"job":("cadvisor" if i%3==0 else "kube-state-metrics"),
                      "namespace":f"ns-{i%16:02}", "cluster":f"cluster-{i%8:02}",
                      "pod":f"api-{i*1013:08x}-{i%65536:05x}", "uid":f"{i*43219:032x}",
                      "container":"api", "phase":("Running" if i%7 else "Pending")}
    if workload == "http":
        name = ("http_server_requests_total","http_requests_total","go_goroutines")[i%3]
        return name, {"job":"go-server", "service":f"service-{i%20:02}",
                      "namespace":f"ns-{i%16:02}", "pod":f"go-{i*1223:08x}",
                      "method":("GET","POST","PUT")[i%3], "route":"/api/v1/items",
                      "code":("500" if i%19==0 else "200")}
    return "jenkins_avs_build_stage_duration", {
        "stage":f"stage-{i%38:04}", "project":f"project-{i%20:02}",
        "build":f"build-{i*16777619:012x}", "branch":("main" if i%13==0 else f"feature-{i:05x}"),
        "jenkins_job":f"pipeline-{i%50:03}", "result":"SUCCESS"}

def fixture(workload, count, dest):
    sha = hashlib.sha256()
    metrics = {}
    points = 0
    with open(dest, "wb") as out:
        for i in range(count):
            metric, labels = family(workload, i)
            begin = BASE + datetime.timedelta(days=(11*i)%14, hours=12)
            n = 1 if workload=="ci" else 48
            samples = [[(begin+datetime.timedelta(seconds=15*j)).strftime("%Y-%m-%d %H:%M:%S.000"),
                        float(1+i%101+j)] for j in range(n)]
            line = (json.dumps({"metric_name":metric,"tags":labels,"samples":samples},
                               separators=(",",":"),sort_keys=True)+"\n").encode()
            out.write(line)
            sha.update(line)
            metrics[metric] = metrics.get(metric,0)+1
            points += n
    return {"sha256":sha.hexdigest(),"series":count,"samples":points,
            "metrics":metrics, "bytes":os.path.getsize(dest),
            "limitation":"synthetic; 14 daily cohorts; dense 15s samples within each day; NOT live scrape ingestion"}

def load(table, filename):
    total = 0
    with open(filename,"rb") as f:
        while True:
            lines = [f.readline() for _ in range(1200)]
            lines = [s for s in lines if s]
            if not lines:
                break
            execute(f"INSERT INTO {table} (metric_name,tags,samples) FORMAT JSONEachRow",
                    data=b"".join(lines),timeout=240)
            total += len(lines)
    return total

def catalog(table, label):
    report={}
    for name in ("tags","timeranges","samples"):
        t=inner(table,name)
        physical=t.split(chr(96))[1]
        entry={"table":physical}
        optional("SELECT name,type,statistics FROM system.columns WHERE database='default' "
                 "AND table="+lit(physical),entry,"column_statistics")
        optional("SELECT column,statistics,estimates.cardinality AS ndv,estimates.min AS min,"
                 "estimates.max AS max,rows FROM system.parts_columns WHERE active "
                 "AND database='default' AND table="+lit(physical)+
                 " AND column IN ('metric_name','id',"+lit(label)+")",entry,"part_statistics")
        optional("SELECT name,rows,marks,bytes_on_disk FROM system.parts WHERE active "
                 "AND database='default' AND table="+lit(physical),entry,"parts")
        report[name]=entry
    return report

def digest(body):
    rows=sorted(json.dumps(json.loads(s),sort_keys=True) for s in body.splitlines() if s.strip())
    return {"rows":len(rows),"sha256":hashlib.sha256("\n".join(rows).encode()).hexdigest()}

def native(workload,table,out):
    metric,label,value=CASES[workload]
    selector=f'{metric}{{{label}="{value}"}}'
    if workload=="ci":
        expr="avg by(stage)(avg_over_time("+selector+"[1d]))"
    elif workload=="node":
        expr="sum by(cluster)(rate("+selector+"[5m]))"
    elif workload=="pod":
        expr="sum by(namespace)(rate("+selector+"[5m]))"
    else:
        expr="sum by(service)(rate("+selector+"[5m]))"
    start=int((BASE+datetime.timedelta(hours=12, minutes=12)).timestamp())
    end=start+13*DAY
    statement=(f"SELECT * FROM prometheusQueryRange({table},{lit(expr)},{start},"
               f"{end},{DAY}) SETTINGS max_threads=4 FORMAT JSONEachRow")
    out["native"]={"promql":expr,"sql":statement,"trials":[]}
    reference=None
    for k in range(2):
        qid=f"cbo_{workload}_native_{k}_{os.getenv('GITHUB_RUN_ID','local')}"
        ms,body=execute(statement,qid)
        points=normalize_any(body)
        if not points:
            raise AssertionError("Empty native PromQL result")
        if reference is None:
            reference=points
            comparison={"points":len(points),"max_abs_error":0.0}
        else:
            comparison=assert_same(reference,points,workload,"repeated native")
        out["native"]["trials"].append({"query_id":qid,"wall_ms":ms,**comparison})
    out["native"]["parity"]=True
    optional("EXPLAIN PLAN indexes=1 "+statement.split(" FORMAT ")[0],out["native"],"plan",True)

def join_sql(table, cond, days, algorithm, use_statistics=1, semi=False):
    tags=inner(table,"tags");tr=inner(table,"timeranges")
    lo="2026-11-01 00:00:00" if days==1 else "2026-10-19 00:00:00"
    timerange=(f"max_time >= toDateTime64({lit(lo)},3,'UTC') AND "
               f"min_time < toDateTime64('2026-11-02 00:00:00',3,'UTC')")
    if semi:
        core=f"SELECT id FROM {tags} WHERE {cond} AND id IN (SELECT id FROM {tr} WHERE {timerange})"
    else:
        core=(f"SELECT t.id FROM (SELECT id FROM {tags} WHERE {cond}) AS t "
              f"LEFT JOIN (SELECT id,toUInt8(1) AS matched FROM {tr} WHERE {timerange}) AS r "
              f"USING(id) WHERE r.matched=1")
    return core+(f" SETTINGS max_threads=4, max_memory_usage=3500000000,"
                 f" use_statistics={use_statistics},join_algorithm={lit(algorithm)}")

def trial(workload, case, variant, sqltext, out, ref):
    entry={"sql":sqltext,"trials":[]}
    out["cases"][case]["variants"][variant]=entry
    optional("EXPLAIN PLAN indexes=1 "+sqltext,entry,"plan",True)
    if variant in ("merge","hash","semi"):
        optional("EXPLAIN ANALYZE processors=1,indexes=1 "+sqltext,entry,"analyze",True)
    try:
        for n in range(5):
            qid=f"cbo_{workload}_{case}_{variant}_{n}_{os.getenv('GITHUB_RUN_ID','local')}"
            ms,body=execute(sqltext+" FORMAT JSONEachRow",qid)
            result=digest(body)
            if result["rows"] == 0:
                raise AssertionError("Invalid empty result: window does not overlap the fixture or predicate is wrong")
            if ref is None:
                ref=result
            if result!=ref:
                raise AssertionError(f"Incorrect join IDs: {result} != {ref}")
            entry["trials"].append({"query_id":qid,"wall_ms":ms,"warmup":n<2,**result})
        entry["median_ms"]=statistics.median(x["wall_ms"] for x in entry["trials"][2:])
        entry["parity"]=True
    except Exception as e:
        entry["error"]=str(e)[:1200]
        out["cases"][case]["errors"].append({variant:entry["error"]})
    return ref

def bench(workload, table, out, root):
    metric,label,value=CASES[workload]
    for case, predicate, days in (
        ("narrow_short",f"metric_name={lit(metric)} AND {label}={lit(value)}",1),
        ("narrow_all",f"metric_name={lit(metric)} AND {label}={lit(value)}",14),
        ("wide_short",f"metric_name={lit(metric)}",1),
        ("wide_all",f"metric_name={lit(metric)}",14)):
        record={"predicate":predicate,"days":days,"variants":{},"errors":[]}
        out["cases"][case]=record
        ref=None
        for variant, algo, stats, semi in (
            ("merge","full_sorting_merge",1,False),
            ("hash","hash",1,False),
            ("parallel_hash","parallel_hash",1,False),
            ("auto","auto",1,False),
            ("semi","full_sorting_merge",1,True),
            ("merge_no_stats","full_sorting_merge",0,False)):
            statement=join_sql(table,predicate,days,algo,stats,semi)
            ref=trial(workload,case,variant,statement,out,ref)
            save(root/"report.json",out)
        a=record["variants"]["merge"].get("median_ms")
        b=record["variants"]["hash"].get("median_ms")
        record["hash_speedup"]=round(a/b,4) if a and b else None

def add_logs(out):
    qids=[x["query_id"] for c in out["cases"].values() for v in c["variants"].values()
          for x in v.get("trials",[])]
    qids.extend(x["query_id"] for x in out.get("native",{}).get("trials",[]))
    execute("SYSTEM FLUSH LOGS")
    logs=jsonrows("SELECT query_id,type,query_duration_ms,memory_usage,read_rows,read_bytes,ProfileEvents "
                  "FROM system.query_log WHERE type IN ('QueryFinish','ExceptionWhileProcessing') "
                  "AND query_id IN ("+",".join(map(lit,qids))+")")
    by_id={x["query_id"]:x for x in logs}
    out["querylog_coverage"]={"expected":len(qids),"found":len(by_id)}
    for case in out["cases"].values():
        for entry in case["variants"].values():
            for item in entry.get("trials",[]):
                log=by_id.get(item["query_id"],{})
                ev=log.get("ProfileEvents") or {}
                item.update({"cpu_ms":round(ev.get("OSCPUVirtualTimeMicroseconds",0)/1000,3),
                             "read_rows":log.get("read_rows"),
                             "read_bytes":log.get("read_bytes"),
                             "query_memory":log.get("memory_usage")})
            valid=[x for x in entry.get("trials",[])[2:] if "cpu_ms" in x]
            if valid:
                entry["median_cpu_ms"]=statistics.median(x["cpu_ms"] for x in valid)
                entry["peak_query_memory"]=max(x.get("query_memory") or 0 for x in valid)
    focus=[]
    for case in out["cases"].values():
        for v in ("merge","hash","semi"):
            tr=case["variants"].get(v,{}).get("trials",[])
            if len(tr)>=3:
                focus.append(tr[2]["query_id"])
    for qid in focus[:5]:
        item={}
        optional("SELECT plan_step_name,name,count() AS processors,"
                 "sum(elapsed_us) AS elapsed_us,sum(input_wait_elapsed_us) AS input_wait_us,"
                 "sum(output_wait_elapsed_us) AS output_wait_us,"
                 "sum(input_rows) AS input_rows,sum(output_rows) AS output_rows "
                 "FROM system.processors_profile_log WHERE query_id="+lit(qid)+
                 " GROUP BY plan_step_name,name ORDER BY elapsed_us DESC LIMIT 35",
                 item,"processors")
        optional("SELECT trace_type,symbols[1] AS top_symbol,count() AS samples "
                 "FROM system.trace_log WHERE query_id="+lit(qid)+
                 " GROUP BY trace_type,top_symbol ORDER BY samples DESC LIMIT 40",
                 item,"stacks")
        out.setdefault("profiles",{})[qid]=item
    optional("SELECT query_id,table,predicate_expression,input_rows,passed_rows,"
             "index_names,total_granules,granules_after,index_selectivities "
             "FROM system.predicate_statistics_log WHERE query_id IN ("+
             ",".join(map(lit,focus[:5]))+") LIMIT 300",out,"predicate_statistics")

def main():
    p=argparse.ArgumentParser()
    p.add_argument("--workload",required=True,choices=list(CASES))
    p.add_argument("--count",type=int,default=6000)
    p.add_argument("--output",required=True)
    p.add_argument("--fixture",required=True)
    a=p.parse_args()
    assert 100<=a.count<=150000
    root=pathlib.Path(a.output);root.mkdir(parents=True,exist_ok=True)
    table="tslab_cbo_"+a.workload
    report={"workload":a.workload,"count":a.count,"cases":{},"errors":[],
            "note":"all data synthetic; SQL JOIN probes are NOT native TimeSeries execution"}
    try:
        report["version"]=jsonrows("SELECT version() AS version")
        optional("SELECT name,value FROM system.settings WHERE name IN "
                 "('use_statistics','auto_statistics_types','materialize_statistics_on_insert',"
                 "'query_plan_optimize_join_order_algorithm','predicate_statistics_sample_rate',"
                 "'query_plan_join_shard_by_pk_ranges','join_algorithm')",report,"settings")
        report["fixture"]=fixture(a.workload,a.count,a.fixture)
        _,label,_=CASES[a.workload]
        execute(f"CREATE TABLE {table} ENGINE=TimeSeries SETTINGS recent_samples_ttl_seconds=0,"
                f"tags_to_columns={{{lit(label)}:{lit(label)}}}")
        report["ingested"]=load(table,a.fixture)
        assert report["ingested"]==a.count
        report["catalog_before"]=catalog(table,label)
        for kind in ("tags","timeranges","samples"):
            t=inner(table,kind)
            try:
                execute(f"ALTER TABLE {t} MATERIALIZE STATISTICS ALL SETTINGS mutations_sync=2",timeout=240)
                report.setdefault("materialize",{})[kind]="ok"
            except Exception as e:
                report.setdefault("materialize",{})[kind]=str(e)[:1100]
            try:
                execute(f"OPTIMIZE TABLE {t} FINAL SETTINGS optimize_throw_if_noop=0",timeout=180)
                report.setdefault("merges",{})[kind]="ok"
            except Exception as e:
                report.setdefault("merges",{})[kind]=str(e)[:1100]
        execute("SYSTEM STOP MERGES")
        report["catalog_after"]=catalog(table,label)
        native(a.workload,table,report)
        bench(a.workload,table,report,root)
        add_logs(report)
        report["passed"]=(all(c["variants"]["merge"].get("parity") and
                                  c["variants"]["hash"].get("parity")
                                  for c in report["cases"].values())
                          and report["querylog_coverage"]["found"]==report["querylog_coverage"]["expected"])
    except Exception as e:
        report["errors"].append(str(e)[:2000])
        report["passed"]=False
        print("ADAPTIVE_PROBE_ERROR",str(e)[:1200],flush=True)
    finally:
        save(root/"report.json",report)
        digest_report={"workload":a.workload,"passed":report.get("passed"),
                       "errors":report["errors"],"fixture":report.get("fixture"),
                       "cases":{n:{"hash_speedup":v.get("hash_speedup"),
                                   "variants":{k:{"median_ms":z.get("median_ms"),
                                                  "median_cpu_ms":z.get("median_cpu_ms"),
                                                  "error":z.get("error")}
                                               for k,z in v["variants"].items()}}
                                for n,v in report["cases"].items()}}
        save(root/"compact.json",digest_report)
        print("ADAPTIVE_PROBE_RESULT",json.dumps(digest_report,separators=(",",":")),flush=True)
    if not report["passed"]:
        raise SystemExit(1)

if __name__=="__main__":
    main()
