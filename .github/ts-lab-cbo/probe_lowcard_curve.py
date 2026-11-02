#!/usr/bin/env python3
"""LowCardinality UniqV2 bias curve, including empty/default and join estimation."""
import argparse,json,pathlib,time,urllib.request,urllib.error,hashlib
URL="http://127.0.0.1:18191/"
def quote(x):return "'"+str(x).replace("'","\\'")+"'"
def sql(statement):
    t=time.monotonic()
    try:
        with urllib.request.urlopen(urllib.request.Request(URL,data=statement.encode(),method="POST"),timeout=120) as r:
            return round((time.monotonic()-t)*1000,3),r.read().decode()
    except urllib.error.HTTPError as e:
        raise RuntimeError(f"CH HTTP {e.code} {e.read().decode(errors='replace')[:1000]}: {statement[:300]}") from e
def fetch(statement):
    return [json.loads(x) for x in sql(statement+" FORMAT JSONEachRow")[1].splitlines() if x.strip()]
def write(file,obj):
    pathlib.Path(file).parent.mkdir(parents=True,exist_ok=True)
    pathlib.Path(file).write_text(json.dumps(obj,indent=2,sort_keys=True)+"\n")
def estimate(plan):
    return [line.strip() for line in plan.splitlines()
            if "rows estimated" in line or "Output rows:" in line or "Selectivity:" in line or "Algorithm:" in line][:20]
def run(typ,k,empty,n,report):
    alias=("lc" if typ.startswith("LowCardinality") else "plain")
    name=f"ndv_curve_{alias}_{k}_{int(empty)}"
    ttype=quote(typ)
    # Make all categories present. Empty category tests the implicit LC default key.
    val=(f"if(number%{k}=0, '', concat('v-',toString(number%{k})))"
         if empty else f"concat('v-',toString(number%{k}))")
    schema=f"CREATE TABLE {name} (label {typ}, number UInt64) ENGINE=MergeTree ORDER BY number"
    cmd=f"INSERT INTO {name} SELECT CAST({val},{ttype}),number FROM numbers({n})"
    row={"type":typ,"distinct_planned":k,"include_empty":empty,"n":n,"schema":schema,
         "fixture_query":cmd,"source_sha256":hashlib.sha256(cmd.encode()).hexdigest()}
    report["cases"][name]=row
    sql(schema)
    row["insert_ms"],_ = sql(cmd)
    _,row["optimize_response"]=sql(f"OPTIMIZE TABLE {name} FINAL SETTINGS optimize_throw_if_noop=0")
    _,row["materialize_response"]=sql(f"ALTER TABLE {name} MATERIALIZE STATISTICS ALL SETTINGS mutations_sync=2")
    actual=fetch(f"SELECT count() AS rows,uniqExact(label) AS exact,uniqCombined64(label) AS combined,countIf(label='') AS empty_rows FROM {name}")[0]
    row["actual"]=actual
    estimates=fetch("SELECT column,statistics,estimates.cardinality AS ndv,rows FROM "
                    "system.parts_columns WHERE active AND database='default' "
                    f"AND table={quote(name)} AND column='label'")
    row["parts"]=estimates
    if len(estimates)!=1 or estimates[0]["ndv"] is None:
        raise AssertionError("Missing complete NDV estimate "+name)
    row["estimated_ndv"]=estimates[0]["ndv"]
    # Full key multiplicity, not a lossy estimate: sum(count(label)^2).
    groups=fetch(f"SELECT label,count() AS members FROM {name} GROUP BY label")
    row["exact_join_rows"]=sum(int(g["members"])**2 for g in groups)
    stmt=f"SELECT count() FROM {name} AS a INNER JOIN {name} AS b ON a.label=b.label SETTINGS use_statistics=1,join_algorithm='hash'"
    _,p=sql("EXPLAIN PLAN indexes=1 "+stmt)
    row["explain_join"]=p[:30000]
    row["estimated_join_plan"]=estimate(p)
    if actual["rows"]!=n or actual["exact"]!=k:
        raise AssertionError(f"Input broken {name}: {actual}")
    return row
def main():
    p=argparse.ArgumentParser()
    p.add_argument("--out",required=True)
    p.add_argument("--rows",type=int,default=15000)
    a=p.parse_args()
    assert a.rows>=15000 and a.rows<=50000
    out=pathlib.Path(a.out)
    report={"rows_per_table":a.rows,"cases":{},"errors":[],
            "limitation":"synthetic high repetitions; plans only, no actual high-fanout JOIN execution"}
    try:
        report["version"]=fetch("SELECT version() AS version")
        for typ in ("String","LowCardinality(String)"):
            for k in (1,2,8,38,152):
                for empty in (False,True):
                    row=run(typ,k,empty,a.rows,report)
                    print("NDV_CURVE_ROW",json.dumps({"type":typ,"k":k,"empty":empty,
                          "estimate":row["estimated_ndv"],"actual":row["actual"]["exact"],
                          "join_pairs":row["exact_join_rows"],
                          "plan":row["estimated_join_plan"][:7]},separators=(",",":")),flush=True)
                    write(out,report)
        report["passed"]=len(report["cases"])==20 and not report["errors"]
    except Exception as ex:
        report["errors"].append(str(ex)[:1900]);report["passed"]=False
    finally:
        write(out,report)
        summary={"passed":report.get("passed"),"errors":report["errors"],
                 "cases":{name:{"actual":v.get("actual",{}).get("exact"),
                                "estimate":v.get("estimated_ndv"),
                                "join_pairs":v.get("exact_join_rows"),
                                "plan":v.get("estimated_join_plan")}
                           for name,v in report["cases"].items()}}
        print("NDV_CURVE_RESULT",json.dumps(summary,separators=(",",":")),flush=True)
    if not report["passed"]:raise SystemExit(1)
if __name__=="__main__":main()
