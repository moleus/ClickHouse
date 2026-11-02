#!/usr/bin/env python3
"""Diagnostic only: split TAGS, TimeRanges, SAMPLES and full native stage query.

Direct internal target access bypasses normal TimeSeries authorization.
This script is valid only in the isolated test ClickHouse CI instance.
"""
import json,os,pathlib,subprocess,sys,time
sys.path.insert(0,".github/ts-lab-perf")
from physical_index_suite import discover,post

ROOT=pathlib.Path("evidence/stage-path")
BASE="http://127.0.0.1:18191"
METRIC="jenkins_ci_95k_daily"
STAGE="stage-0007"
SAMPLE_COUNT=35000

def main():
    ROOT.mkdir(parents=True,exist_ok=True)
    m=json.loads(pathlib.Path("evidence/95k-manifest.json").read_text())
    assert m["series"]==1330000 and m["sha256"]=="8f3a36846f80d6be5a8604d142e934399f2687094766c57bf105e43977f52858"
    meta=discover();tags=meta["tags"]
    uuid=tags.split(".inner_id.tags.")[-1].replace(chr(96),"")
    all_targets=meta["candidates"]
    pairs={}
    for kind in ("samples","time_ranges"):
        options=[p for p in all_targets[kind] if p.split(".")[-1].replace(chr(96),"")==uuid]
        if len(options)!=1:raise RuntimeError("cannot verify matching UUID for "+kind+": "+str(options))
        pairs[kind]=options[0]
    sample,range_table=pairs["samples"],pairs["time_ranges"]
    start=int(m["base"]); end=int(m["end"])
    startsql=f"fromUnixTimestamp64Milli({start*1000})"
    endsql=f"fromUnixTimestamp64Milli({end*1000})"
    tagexpr=f"metric_name='{METRIC}' AND tags['stage']='{STAGE}'"
    tagids=f"SELECT id FROM {tags} WHERE {tagexpr}"
    trcond=f"max_time >= {startsql} AND min_time <= {endsql}"
    joinids=(f"SELECT t.id FROM {tags} AS t INNER JOIN "
             f"(SELECT id FROM {range_table} WHERE {trcond}) AS r USING(id) "
             f"WHERE t.metric_name='{METRIC}' AND t.tags['stage']='{STAGE}'")
    query_list=[
      ("tags_ids",f"SELECT uniqExact(id) AS n FROM {tags} WHERE {tagexpr}",SAMPLE_COUNT,True),
      ("time_ranges_cover",f"SELECT count() AS n FROM {range_table} WHERE {trcond}",1330000,False),
      ("tags_time_join",f"SELECT uniqExact(id) AS n FROM ({joinids})",SAMPLE_COUNT,False),
      ("sample_id_set",f"SELECT sum(length(samples)) AS n FROM {sample} WHERE id IN ({tagids})",SAMPLE_COUNT,True),
      ("sample_id_time",f"SELECT sum(length(samples)) AS n FROM {sample} WHERE id IN ({tagids}) "
          f"AND bucket >= {startsql} AND bucket <= {endsql} AND max_time >= {startsql} AND min_time <= {endsql}",
          SAMPLE_COUNT,True),
      ("sample_joined_ids",f"SELECT sum(length(samples)) AS n FROM {sample} WHERE id IN ({joinids}) "
          f"AND bucket >= {startsql} AND bucket <= {endsql}",SAMPLE_COUNT,False),
      ("native_stage",f"SELECT * FROM prometheusQueryRange(churn_ts,"
          f"'avg by(stage)(avg_over_time({METRIC}{{stage=\"{STAGE}\"}}[1d]))',"
          f"{start+86400},{end},86400)",14,True)
    ]
    report={"fixture_sha256":m["sha256"],"owner_suffix":uuid,
            "physical_targets":{"tags":tags,**pairs},
            "explicit_safety":"admin CI internal SQL not equivalent to tenant-safe native selector",
            "samples_expected":SAMPLE_COUNT,"cases":[],"failures":[]}
    (ROOT/"discovery.json").write_text(json.dumps(report,indent=2)+"\n")
    pid=pathlib.Path(os.environ["RUNNER_TEMP"],"ch.pid").read_text().strip()
    for index,(key,sql,expected,required) in enumerate(query_list):
        if key=="native_stage":sql+=" SETTINGS max_threads=4,log_processors_profiles=1,query_profiler_cpu_time_period_ns=20000000"
        else:sql+=" SETTINGS max_threads=4,optimize_trivial_count_query=0,log_processors_profiles=1,query_profiler_cpu_time_period_ns=20000000"
        sqlfile=ROOT/(key+".sql")
        sqlfile.write_text(sql+" FORMAT JSONEachRow\n")
        args=["python3",".github/ts-lab-perf/capture.py","--engine","ch","--url",BASE,
              "--pid",pid,"--data-path",str(pathlib.Path(os.environ["RUNNER_TEMP"],"ch/data")),
              "--case",key,"--query-file",str(sqlfile),"--output",str(ROOT/key)]
        p=subprocess.run(args,check=False)
        got=None;error=None
        try:
            q=json.loads((ROOT/key/"query_result.json").read_text())
            if key=="native_stage":got=sum(len(r.get("samples",[])) for r in q)
            else:got=int(q[0]["n"]) if q else None
        except Exception as exc:error=str(exc)[:450]
        valid=p.returncode==0 and got==expected
        case={"name":key,"index":index,"expected":expected,"observed":got,
              "required":required,"valid":valid,"error":error}
        report["cases"].append(case)
        if not valid:report["failures"].append(case)
        (ROOT/"discovery.json").write_text(json.dumps(report,indent=2)+"\n")
        print("STAGE_PATH_CASE",json.dumps(case,separators=(",",":")),flush=True)
    required_errors=[x for x in report["failures"] if x["required"]]
    print("STAGE_PATH_DONE",json.dumps({"cases":len(report["cases"]),"failed":len(report["failures"]),
        "required_failed":len(required_errors),"sha256":m["sha256"]},separators=(",",":")),flush=True)
    if required_errors:raise SystemExit("Required stage-path diagnostic failed; inspect raw artifacts")

if __name__=="__main__":main()
