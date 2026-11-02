#!/usr/bin/env python3
"""Preflight native ClickHouse TimeSeries compound ID ordering, no C++ rebuild.

Verifies default, stage-prefixed, project-prefixed and project/stage-prefixed
series IDs with identical synthetic samples and native PromQL results.
"""
import argparse,datetime,hashlib,json,os,pathlib,random,sys,time,urllib.parse,urllib.request,urllib.error
sys.path.insert(0,".github/ts-lab-bench")
from bench_bucket_cxx_abca import normalize_any,assert_same

URL="http://127.0.0.1:18191/"
METRIC="tslab_layout_smoke_requests"
START=1793491200
DAY=86400
SCHEMA={
 "default":[],
 "stage":["stage"],
 "project":["project"],
 "project_stage":["project","stage"],
}
def query(q,timeout=120):
    req=urllib.request.Request(URL,data=q.encode(),method="POST")
    try:
        with urllib.request.urlopen(req,timeout=timeout) as r:
            return r.read().decode(errors="replace")
    except urllib.error.HTTPError as exc:
        raise RuntimeError(f"ClickHouse HTTP {exc.code}: {exc.read().decode(errors='replace')[:2200]}") from exc
def insert(table,data):
    req=urllib.request.Request(URL+"?wait_end_of_query=1&query="+urllib.parse.quote(
        "INSERT INTO "+table+" (metric_name,tags,samples) FORMAT JSONEachRow"),
        data=data,method="POST")
    with urllib.request.urlopen(req,timeout=180) as r:r.read()
def definition(table,labels):
    if not labels:
        return f"CREATE TABLE {table} ENGINE=TimeSeries SETTINGS recent_samples_ttl_seconds=0"
    full="Tuple("+", ".join(["UInt64"]*(1+len(labels))+["LowCardinality(UUID)"])+")"
    fields=["sipHash64(metric_name)"]
    for label in labels:
        fields.append(f"sipHash64(tags['{label}'])")
    fields.append("toLowCardinality(reinterpretAsUUID(sipHash128(tags)))")
    generator="tuple("+", ".join(fields)+")"
    return (f"CREATE TABLE {table} ENGINE=TimeSeries "
            f"SAMPLES INNER COLUMNS (id {full}) "
            f"TAGS INNER COLUMNS (id {full} DEFAULT {generator}) "
            f"TIME RANGES INNER COLUMNS (id {full}) "
            f"SETTINGS recent_samples_ttl_seconds=0")
def data_rows():
    rows=[]
    # 2 metric families, 4 stages, 4 projects, 10 jobs, 100 builds.
    for day in range(4):
        for n in range(800):
            tags={"stage":f"stage-{n%4:02d}","project":f"project-{(n//4)%4:02d}",
                  "job":f"job-{(n//16)%10:02d}","branch":"main" if n%3 else "feature",
                  "build":f"build-{day}-{n:05d}","region":f"region-{(n//64)%3:02d}"}
            metric=METRIC if n%5 else METRIC+"_other"
            ts=datetime.datetime.fromtimestamp(START+(day+1)*DAY-3600,datetime.timezone.utc)
            rows.append({"metric_name":metric,"tags":tags,
                         "samples":[[ts.strftime("%Y-%m-%d %H:%M:%S.000"),float(n%19+1)]]})
    rng=random.Random(521)
    rng.shuffle(rows)
    return rows
def data_bytes(rows):
    return ("\n".join(json.dumps(r,sort_keys=True,separators=(",",":")) for r in rows)+"\n").encode()
def target_stats(table):
    uuid=query(f"SELECT toString(uuid) FROM system.tables WHERE database='default' AND name='{table}' FORMAT TSV").strip()
    assert len(uuid)==36,(table,uuid)
    name=".inner_id.samples."+uuid
    stats=json.loads(query("SELECT sum(rows) AS rows,sum(marks) AS marks,count() AS parts,sum(bytes_on_disk) AS bytes_on_disk "
            f"FROM system.parts WHERE active AND database='default' AND table='{name}' FORMAT JSONEachRow").strip())
    return {"uuid":uuid,"samples":stats,"samples_ddl":query(f"SHOW CREATE TABLE default.\x60{name}\x60 FORMAT TSVRaw")[:9000]}
def main():
    p=argparse.ArgumentParser()
    p.add_argument("--out",required=True)
    a=p.parse_args()
    out=pathlib.Path(a.out);out.mkdir(parents=True,exist_ok=True)
    rows=data_rows();body=data_bytes(rows)
    sha=hashlib.sha256(body).hexdigest()
    (out/"synthetic-source.sha256").write_text(sha+"  synthetic-only\n")
    result={"source_sha256":sha,"source_rows":len(rows),"cases":{},"errors":[],
            "warning":"all metric/build/region/job values are synthetic"}
    (out/"report.json").write_text(json.dumps(result,indent=2)+"\n")
    queries={
      "whole":f"avg by(stage)(avg_over_time({METRIC}[1d]))",
      "stage":f"avg by(stage)(avg_over_time({METRIC}{{stage=\"stage-01\"}}[1d]))",
      "project":f"avg by(stage)(avg_over_time({METRIC}{{project=\"project-01\"}}[1d]))",
      "project_stage":f"avg by(stage)(avg_over_time({METRIC}{{project=\"project-01\",stage=\"stage-01\"}}[1d]))",
      "regex":f"avg by(stage)(avg_over_time({METRIC}{{job=~\"job-0[1-4]\"}}[1d]))"
    }
    baseline={}
    for variant,labels in SCHEMA.items():
        table="tsl_smoke_"+variant
        try:
            sql=definition(table,labels)
            query(sql,timeout=60)
            # Insert twice as separate batches (dedup and multiple parts must preserve result).
            insert(table,data_bytes(rows[:len(rows)//2]))
            insert(table,data_bytes(rows[len(rows)//2:]))
            assert int(query(f"SELECT count() FROM {table} FORMAT TSV").strip())==len(rows)
            # All physical bucket rows for this fixture contain exactly one sample.
            query("OPTIMIZE TABLE "+table+" FINAL",timeout=120)
            case={"ddl":sql,"targets":target_stats(table),"outputs":{}}
            for name,expr in queries.items():
                q=f"SELECT * FROM prometheusQueryRange({table},'{expr}',{START+DAY},{START+4*DAY},{DAY}) SETTINGS max_threads=4 FORMAT JSONEachRow"
                raw=query(q,timeout=100)
                normalized=normalize_any(raw.encode())
                if variant=="default":
                    baseline[name]=normalized
                else:
                    assert_same(baseline[name],normalized,name,variant)
                case["outputs"][name]={"points":len(normalized),"sha256":hashlib.sha256(
                    repr(sorted(normalized.items(),key=lambda x:repr(x[0]))).encode()).hexdigest()}
                print("LAYOUT_SMOKE_QUERY",json.dumps({"variant":variant,"query":name,
                    "points":len(normalized),"match":True},separators=(",",":")),flush=True)
            result["cases"][variant]=case
        except Exception as exc:
            result["errors"].append({"variant":variant,"exception":str(exc)[:2500]})
            result["cases"][variant]={"error":str(exc)[:2500]}
            (out/"report.json").write_text(json.dumps(result,indent=2)+"\n")
            raise
        (out/"report.json").write_text(json.dumps(result,indent=2)+"\n")
    result["passed"]=len(result["cases"])==len(SCHEMA) and not result["errors"]
    (out/"report.json").write_text(json.dumps(result,indent=2)+"\n")
    print("LAYOUT_SMOKE_DONE",json.dumps({"passed":result["passed"],"source_sha256":sha,
        "variants":list(result["cases"]),"total_checks":len(queries)*len(SCHEMA)},separators=(",",":")),flush=True)
    if not result["passed"]:raise SystemExit(1)
if __name__=="__main__":main()
