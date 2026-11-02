#!/usr/bin/env python3
"""Cost estimator selectivity when one Kubernetes label value dominates.

Exact data: 100 buckets, one heavy "hot" cluster and 99 rare clusters. Tests
whether NDV/basic stats capture distribution and compares estimates for hot
and cold predicates. Does not execute quadratic self-join output.
"""
import argparse,json,pathlib,hashlib,time,urllib.request,urllib.error
URL="http://127.0.0.1:18191/"
def lit(s):return "'"+str(s).replace("'","\\'")+"'"
def sql(q):
    start=time.monotonic()
    try:
        with urllib.request.urlopen(urllib.request.Request(URL,data=q.encode(),method="POST"),timeout=180) as response:
            return round((time.monotonic()-start)*1000,3),response.read().decode()
    except urllib.error.HTTPError as ex:
        raise RuntimeError(f"SQL HTTP {ex.code}: {ex.read().decode(errors='replace')[:1300]} {q[:300]}") from ex
def jsonrows(q):
    return [json.loads(x) for x in sql(q+" FORMAT JSONEachRow")[1].splitlines() if x.strip()]
def save(path,report):
    pathlib.Path(path).parent.mkdir(parents=True,exist_ok=True)
    pathlib.Path(path).write_text(json.dumps(report,indent=2)+"\n")
def plan_query(table,label,stats):
    return f"SELECT count() FROM {table} WHERE cluster={lit(label)} SETTINGS use_statistics={stats},optimize_trivial_count_query=0"
def run(typ,heavy,n,report):
    name=f"tslab_skew_{'lc' if typ.startswith('LowCardinality') else 'string'}_{heavy}"
    vals=f"if(number<{n*heavy//100},'hot',concat('cold-',toString(number%99)))"
    data=f"INSERT INTO {name} SELECT number,CAST({vals},{lit(typ)}) FROM numbers({n})"
    entry={"name":name,"type":typ,"hot_percent":heavy,"fixture_sha":hashlib.sha256(data.encode()).hexdigest(),
           "rows_expected":n,"runs":[]}
    report["cases"][name]=entry
    sql(f"CREATE TABLE {name} (id UInt64, cluster {typ}) ENGINE=MergeTree ORDER BY id")
    entry["insert_ms"]=sql(data)[0]
    sql(f"OPTIMIZE TABLE {name} FINAL SETTINGS optimize_throw_if_noop=0")
    sql(f"ALTER TABLE {name} MATERIALIZE STATISTICS ALL SETTINGS mutations_sync=2")
    entry["actual"]=jsonrows(f"SELECT count() AS n,uniqExact(cluster) AS ndv,sum(cluster='hot') AS hot_rows FROM {name}")[0]
    entry["part_stats"]=jsonrows("SELECT column,statistics,estimates.cardinality AS ndv,rows "
                                 "FROM system.parts_columns WHERE active AND database='default' "
                                 f"AND table={lit(name)} AND column='cluster'")
    entry["distribution"]=jsonrows(f"SELECT cluster,count() AS n FROM {name} GROUP BY cluster ORDER BY n DESC,cluster LIMIT 12")
    expected_hot=n*heavy//100
    expected_cold=sum(1 for i in range(expected_hot,n) if i%99==1)
    if entry["actual"]["n"]!=n or entry["actual"]["ndv"]!=100 or entry["actual"]["hot_rows"]!=expected_hot:
        raise AssertionError(f"Bad fixture {name}: {entry['actual']}")
    for label,want in (("hot",expected_hot),("cold-1",expected_cold)):
        for flag in (0,1):
            statement=plan_query(name,label,flag)
            ms,raw=sql(statement+" FORMAT JSONEachRow")
            actual=json.loads(raw.strip())["count()"]
            if actual!=want:raise AssertionError(f"{name}: {label}: {actual}!={want}")
            _,explain=sql("EXPLAIN PLAN indexes=1 "+statement)
            entry["runs"].append({"label":label,"use_statistics":flag,"expected":want,"actual":actual,
                                  "wall_ms":ms,"explain":explain[:35000]})
    for label,want in (("hot",expected_hot),("cold-1",expected_cold)):
        # We only EXPLAIN the quadratic join. Its exact output is derived from
        # the validated frequency table: sum(freq(value)^2) for unfiltered,
        # freq(label)^2 for equality.
        sq=(f"SELECT count() FROM {name} a INNER JOIN {name} b USING(cluster) "
            f"WHERE a.cluster={lit(label)} SETTINGS use_statistics=1,join_algorithm='hash'")
        _,plan=sql("EXPLAIN PLAN indexes=1 "+sq)
        entry.setdefault("join_estimates",{})[label]={"actual_pairs":want*want,
            "explain":plan[:50000]}
    return entry
def main():
    p=argparse.ArgumentParser();p.add_argument("--out",required=True)
    p.add_argument("--rows",type=int,default=100000)
    a=p.parse_args();assert 50000<=a.rows<=200000
    report={"source":"synthetic 100-label skew; NOT native TimeSeries benchmark",
            "cases":{},"errors":[]}
    try:
        report["version"]=jsonrows("SELECT version() AS version")
        for typ in ("String","LowCardinality(String)"):
            for heavy in (90,99):
                res=run(typ,heavy,a.rows,report)
                print("SKEW_SELECTIVITY_CASE",json.dumps({"table":res["name"],
                      "hot_percent":heavy,"hot_actual":res["actual"]["hot_rows"],
                      "part_stats":res["part_stats"],
                      "cold_actual":res["runs"][2]["actual"]},separators=(",",":")),flush=True)
                save(a.out,report)
        report["passed"]=len(report["cases"])==4 and not report["errors"]
    except Exception as ex:
        report["errors"].append(str(ex)[:1900]);report["passed"]=False
    finally:
        save(a.out,report)
        print("SKEW_SELECTIVITY_RESULT",json.dumps({"passed":report.get("passed"),
               "cases":len(report["cases"]),"errors":report["errors"]},separators=(",",":")),flush=True)
    if not report["passed"]:raise SystemExit(1)
if __name__=="__main__":
    main()
