#!/usr/bin/env python3
"""Diagnostic physical TAGS index study. CI admin-only. Never use this as tenant read path."""
import json,os,pathlib,subprocess,urllib.request

BASE="http://127.0.0.1:18191"
ROOT=pathlib.Path("evidence/physical-index")
METRIC="jenkins_ci_95k_daily"

def post(sql):
    with urllib.request.urlopen(urllib.request.Request(BASE+"/",data=sql.encode()),timeout=240) as response:
        return response.read().decode(errors="replace")

def rows(sql):
    return [json.loads(line) for line in post(sql+" FORMAT JSONEachRow").splitlines() if line.strip()]

def quoted(db,table):
    quote=chr(96)
    return quote+db.replace(quote,quote*2)+quote+"."+quote+table.replace(quote,quote*2)+quote

def count_metric(table):
    try:
        s=post("SELECT count() FROM "+table+" WHERE metric_name='"+METRIC+
               "' SETTINGS max_threads=4,optimize_trivial_count_query=0 FORMAT TSV")
        return int(s.strip())
    except Exception as exc:
        print("PHYSICAL_INDEX_DISCOVERY_WARNING",str(exc)[:300],flush=True)
        return -1

def discover():
    cols=rows("SELECT database,table,name FROM system.columns WHERE database='default'")
    groups={}
    for z in cols:
        groups.setdefault((z["database"],z["table"]),set()).add(z["name"])
    candidates={"tags":[],"samples":[],"time_ranges":[]}
    for (db,t),fields in groups.items():
        if t in ("churn_ts","precision_ts"):continue
        full=quoted(db,t)
        if {"id","metric_name","tags"}.issubset(fields):candidates["tags"].append(full)
        if {"id","samples","bucket"}.issubset(fields):candidates["samples"].append(full)
        if {"id","min_time","max_time"}.issubset(fields) and "tags" not in fields and "bucket" not in fields:
            candidates["time_ranges"].append(full)
    winners=[(table,count_metric(table)) for table in candidates["tags"]]
    winners=[(table,n) for table,n in winners if n==1330000]
    if len(winners)!=1:
        raise RuntimeError("Need one 1.33m TAGS table; candidates="+str(candidates)+", matches="+str(winners))
    return {"columns":{quoted(db,t):sorted(v) for (db,t),v in groups.items()},
            "candidates":candidates,"tags":winners[0][0],"tags_rows":winners[0][1],
            "sample_table_association":"unverified; do not assume arbitrary inner table ownership"}

def main():
    ROOT.mkdir(parents=True,exist_ok=True)
    m=json.loads(pathlib.Path("evidence/95k-manifest.json").read_text())
    assert m["series"]==1330000
    report={"fixture_sha256":m["sha256"],"source_commit":os.getenv("GITHUB_SHA"),
            "safety":"isolated admin-only direct inner SQL is NOT tenant-safe",
            "cases":{},"failures":[]}
    try:
        report["show_create"]=post("SHOW CREATE TABLE churn_ts FORMAT TSVRaw")[:100000]
        report["discovery"]=discover()
    except Exception as exc:
        report["failure"]=str(exc)[:2500]
        (ROOT/"discovery.json").write_text(json.dumps(report,indent=2)+"\n")
        raise
    tags=report["discovery"]["tags"]
    cases={
        "tags_metric_only":f"SELECT count() FROM {tags} WHERE metric_name='{METRIC}'",
        "tags_stage_exact":f"SELECT count() FROM {tags} WHERE metric_name='{METRIC}' AND tags['stage']='stage-0007'",
        "tags_branch_regex":f"SELECT count() FROM {tags} WHERE metric_name='{METRIC}' AND match(tags['branch'],'^(main|feature-0[0-9])$')",
        "tags_project_exact":f"SELECT count() FROM {tags} WHERE metric_name='{METRIC}' AND tags['project']='project-00'",
    }
    pid=pathlib.Path(os.environ["RUNNER_TEMP"],"ch.pid").read_text().strip()
    for key,sql in cases.items():
        sql+=(" SETTINGS max_threads=4,optimize_trivial_count_query=0,log_processors_profiles=1,"
              "query_profiler_cpu_time_period_ns=20000000,query_profiler_real_time_period_ns=20000000")
        sql_path=ROOT/(key+".sql")
        sql_path.write_text(sql+" FORMAT JSONEachRow\n")
        command=["python3",".github/ts-lab-perf/capture.py","--engine","ch","--url",BASE,
                 "--pid",pid,"--data-path",str(pathlib.Path(os.environ["RUNNER_TEMP"],"ch/data")),
                 "--query-file",str(sql_path),"--case",key,"--output",str(ROOT/key)]
        result=subprocess.run(command,check=False)
        item={"status":"ok" if result.returncode==0 else "failed"}
        report["cases"][key]=item
        if result.returncode:report["failures"].append(key)
        else:
            records=json.loads((ROOT/key/"query_result.json").read_text())
            item["matched_rows"]=records[0].get("count()") if records else None
        (ROOT/"discovery.json").write_text(json.dumps(report,indent=2)+"\n")
        print("PHYSICAL_INDEX_CASE",json.dumps({"case":key,**item},separators=(",",":")),flush=True)
    print("PHYSICAL_INDEX_DONE",json.dumps({"cases":len(cases),"failures":report["failures"],
        "tags_table":tags},separators=(",",":")),flush=True)
    if report["failures"]:raise SystemExit(1)

if __name__=="__main__":main()
