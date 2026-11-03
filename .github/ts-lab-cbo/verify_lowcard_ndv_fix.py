#!/usr/bin/env python3
"""Strict post-build LowCardinality UniqV2 estimator regression checks."""
import argparse,json,pathlib,urllib.request,urllib.error
URL="http://127.0.0.1:18191/"
def sql(q):
    try:
        with urllib.request.urlopen(urllib.request.Request(URL,data=q.encode(),method="POST"),timeout=150) as r:
            return r.read().decode()
    except urllib.error.HTTPError as e:
        raise RuntimeError(f"HTTP {e.code}: {e.read().decode(errors='replace')[:1200]}") from e
def fetch(q):
    return [json.loads(x) for x in sql(q+" FORMAT JSONEachRow").splitlines() if x]
def nullable(case,vals):
    name="tslab_lc_nullable_"+case
    sql(f"CREATE TABLE {name} (id UInt64,val LowCardinality(Nullable(String))) ENGINE=MergeTree ORDER BY id")
    sql(f"INSERT INTO {name} SELECT number,CAST({vals},'LowCardinality(Nullable(String))') FROM numbers(15000)")
    sql(f"ALTER TABLE {name} ADD STATISTICS val TYPE uniq_v2")
    sql(f"ALTER TABLE {name} MATERIALIZE STATISTICS ALL SETTINGS mutations_sync=2")
    actual=fetch(f"SELECT count() AS rows,uniqExact(val) AS ndv,countIf(isNull(val)) AS null_count FROM {name}")[0]
    part=fetch("SELECT column,estimates.cardinality AS ndv FROM system.parts_columns "
               f"WHERE database='default' AND active AND table='{name}' AND column='val'")
    assert len(part)==1 and part[0]["ndv"]==1,(name,actual,part)
    assert actual["ndv"]==1 and actual["rows"]==15000,(name,actual,part)
    if case=="with_null":assert actual["null_count"]>0,(name,actual)
    else:assert actual["null_count"]==0,(name,actual)
    return {"actual":actual,"part":part[0]}
def main():
    p=argparse.ArgumentParser();p.add_argument("--input",required=True);p.add_argument("--out",required=True)
    a=p.parse_args()
    base=json.loads(pathlib.Path(a.input).read_text())
    if base.get("passed") is not True or len(base["cases"])!=20:
        raise AssertionError("20-case input probe failed")
    summary={"cases":{},"nullable":{},"errors":[]}
    for k in (1,2,8,38,152):
        for empty in (0,1):
            names=[f"ndv_curve_{t}_{k}_{empty}" for t in ("plain","lc")]
            both=[base["cases"][n] for n in names]
            for n,row in zip(names,both):
                actual=row["actual"]["exact"]
                estimated=row["estimated_ndv"]
                if actual!=k or abs(estimated-k)>max(2,0.05*k):
                    summary["errors"].append(f"{n} true={actual} estimated={estimated}")
                summary["cases"][n]={"actual":actual,"estimated":estimated}
            if abs(both[0]["estimated_ndv"]-both[1]["estimated_ndv"])>max(2,0.05*k):
                summary["errors"].append(f"LC/plain disagree k={k}, empty={empty}")
    try:
        summary["nullable"]["nondefault"]=nullable("nondefault","'constant'")
        summary["nullable"]["with_null"]=nullable("with_null","if(number%5=0,CAST(NULL,'Nullable(String)'),CAST('constant','Nullable(String)'))")
    except Exception as e:summary["errors"].append("Nullable test: "+str(e)[:1400])
    summary["passed"]=not summary["errors"]
    pathlib.Path(a.out).write_text(json.dumps(summary,indent=2)+"\n")
    print("LOWCARD_NDV_PATCH_RESULT",json.dumps({"passed":summary["passed"],"errors":summary["errors"],
          "nullable":summary["nullable"]},separators=(",",":")),flush=True)
    if not summary["passed"]:raise SystemExit(1)
if __name__=="__main__":main()
