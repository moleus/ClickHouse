#!/usr/bin/env python3
"""Low-context automatic reducer for sparse/dense layout research CI.

Fixed public/synthetic GitHub artifact references. Never interprets missing,
failed, OOM or partial query results as success. Full raw evidence stays in
original Actions artifacts; this only publishes a small auditable digest.
"""
import argparse,json,pathlib,subprocess,statistics,re
CASES=("stage","project","project_stage","regex")
VARIANTS=("stage","project","project_stage")
SOURCE_SHA="8f3a36846f80d6be5a8604d142e934399f2687094766c57bf105e43977f52858"
DENSE_SHA="83695082ec9713894ae3a75bec127065fed82f13673093ff8de4ac2b8d15c9ce"
def cmd(args,timeout=120):
    p=subprocess.run(["gh",*args],capture_output=True,text=True,timeout=timeout)
    if p.returncode:raise RuntimeError("gh returned "+str(p.returncode)+": "+p.stderr[:700])
    return p.stdout
def unique(root,name):
    match=list(root.rglob(name))
    if len(match)!=1:raise RuntimeError(f"Artifact expected one {name}, found {len(match)}")
    return match[0]
def get_artifact(repo,run,name,root):
    dest=root/name
    dest.mkdir(parents=True,exist_ok=True)
    cmd(["run","download",str(run),"-R",repo,"-n",name,"-D",str(dest)])
    return dest
def read(p):
    return json.loads(p.read_text())
def compact_sparse(report,variant,fast):
    if report.get("canonical_sha256")!=SOURCE_SHA:raise RuntimeError("Fixture drift")
    if report.get("variant")!=variant:raise RuntimeError("Wrong candidate variant")
    if report.get("fastpath")!=fast or report.get("selective_only") is not True:
        raise RuntimeError("Wrong fastpath/selector mode")
    result={"variant":variant,"fastpath":fast,"passed":bool(report.get("passed")),
            "imported_rows":report.get("ingest",{}).get("rows"),
            "binary_sha256":report.get("binary_sha256"),
            "issues":report.get("errors",[])[:10],
            "comparisons":{},"physical_mark_counts":{}}
    if result["imported_rows"]!=1330000:result["issues"].append("Incomplete source load")
    for case in CASES:
        item=report.get("cases",{}).get(case,{})
        comp=item.get("compared")
        if comp and item.get("numerical"):
            result["comparisons"][case]={"speedup":comp.get("speedup"),
                     "baseline_ms":comp.get("baseline_p50_mean_ms"),
                     "candidate_ms":comp.get("candidate_p50_mean_ms"),
                     "drift_fraction":comp.get("baseline_drift_fraction"),
                     "correctness_pass":True}
        else:result["comparisons"][case]={"correctness_pass":False,"error":"missing full paired results"}
    for table,matches in report.get("physical",{}).items():
        result["physical_mark_counts"][table]={}
        for selector,probe in matches.items():
            result["physical_mark_counts"][table][selector]={
                "matching_samples":probe.get("candidate_matches"),
                "selected_marks":probe.get("granules_from_explain")}
    if not all(x["correctness_pass"] for x in result["comparisons"].values()) or result["issues"]:
        result["passed"]=False
    return result
def compact_dense(report):
    if report.get("fixture",{}).get("sha256")!=DENSE_SHA or report.get("passed") is not True:
        raise RuntimeError("Dense fixture invalid/incomplete")
    values={}
    for name,obj in report.get("cases",{}).items():
        cc=obj["compared"]
        cpu={}
        for phase in obj["measurements"]:
            observed=[x["cpu_ms"] for x in phase["trials"] if x.get("cpu_ms") is not None and not x["warmup"]]
            if observed:cpu[phase["phase"]]=round(statistics.median(observed),3)
        values[name]={"speedup":cc["speedup"],
             "baseline_p50_ms":cc["baseline_ms"],
             "candidate_p50_ms":cc["candidate_ms"],
             "baseline_drift":cc["baseline_drift"],
             "cpu_per_phase_ms":cpu}
    return {"fixture_sha256":DENSE_SHA,"total_samples":report["fixture"]["samples"],
            "cases":values,"querylog_coverage":report.get("query_log_coverage"),
            "limitation":report.get("limitation"),"errors":report.get("errors")}
def main():
    p=argparse.ArgumentParser()
    p.add_argument("--repo",default="moleus/ClickHouse")
    p.add_argument("--sparse-run",type=int,required=True)
    p.add_argument("--dense-run",type=int,required=True)
    p.add_argument("--mark-run",type=int,required=True)
    p.add_argument("--public-run",type=int,required=True)
    p.add_argument("--out",required=True)
    a=p.parse_args()
    out=pathlib.Path(a.out);out.mkdir(parents=True,exist_ok=True)
    report={"schema":1,"type":"TimeSeries synthetic physical layout research",
            "run_urls":{key:f"https://github.com/{a.repo}/actions/runs/{id}" for key,id in
                (("sparse",a.sparse_run),("dense",a.dense_run),("mark_model",a.mark_run),("public_corpus",a.public_run))},
            "sparse_id_formats":{},"errors":[],"all_six_passed":True,
            "no_private_data":True,
            "limitations":["Single-node synthetic data, not production query frequencies",
                 "No hardware PMU, no tenant ACL or distributed storage proof",
                 "Physically shorter SAMPLES read does not imply broad-query correctness",
                 "Sparse results only; prior broad ID queries OOM at 4.66GiB"]}
    for variant in VARIANTS:
        report["sparse_id_formats"][variant]={}
        for fast in (0,1):
            name=f"tslab-layout-{variant}-fast{fast}-evidence"
            try:
                root=get_artifact(a.repo,a.sparse_run,name,out/"download")
                rec=read(unique(root,"report.json"))
                summary=compact_sparse(rec,variant,fast)
                report["sparse_id_formats"][variant][str(fast)]=summary
                if not summary["passed"]:report["all_six_passed"]=False
            except Exception as exc:
                report["all_six_passed"]=False
                error=f"{variant} fast{fast}: {str(exc)[:500]}"
                report["errors"].append(error)
                report["sparse_id_formats"][variant][str(fast)]={"passed":False,"error":error}
            print("LAYOUT_REDUCED_CASE",json.dumps({"variant":variant,"fastpath":fast,
                "passed":report["sparse_id_formats"][variant][str(fast)]["passed"]},separators=(",",":")),flush=True)
    folder=get_artifact(a.repo,a.dense_run,"tslab-layout-dense-read-evidence",out/"download")
    report["dense"]=compact_dense(read(unique(folder,"report.json")))
    folder=get_artifact(a.repo,a.mark_run,"tslab-layout-mark-model-evidence",out/"download")
    model=read(unique(folder,"grouping-predictions.json"))
    assert model["N"]==1330000
    report["mark_model"]={"granularity":model["granularity"],
         "predicted_marks":{k:{q:x["marks_touched"] for q,x in v.items()}
                            for k,v in model["candidates"].items()},
         "surrogate_not_clickhouse":True}
    folder=get_artifact(a.repo,a.public_run,"tslab-public-promql-corpus",out/"download")
    public=read(unique(folder,"corpus-summary.json"))
    assert public["panel_queries"]==289
    report["public_dashboard_corpus"]={"repo":public["public_repo"],"commit":public["public_source_commit"],
      "panel_definitions":289,
      "label_panels":{x["label"]:x["panels_containing_label"] for x in public["label_frequency"][:10]},
      "not_real_execution_counts":True}
    compact=out/"layout-results.json"
    compact.write_text(json.dumps(report,indent=2)+"\n")
    lines=["# TimeSeries layout results: automatic failure-aware digest","",
           "Six independent synthetic high-cardinality selective comparisons, two full-query results and public label corpus.",
           "Prior broad high-cardinality candidate IDs all reached OOM; sparse wins cannot authorize production.","",
           "| ID type | Fastpath setting | All controls passed | Stage x | Project x | Both x | Regex x |",
           "|---|---|---|---:|---:|---:|---:|"]
    for variant,byflag in report["sparse_id_formats"].items():
        for fast,rec in byflag.items():
            vals=[rec.get("comparisons",{}).get(k,{}).get("speedup") for k in CASES]
            items=[f"{x:.2f}x" if isinstance(x,(float,int)) else "n/a" for x in vals]
            lines.append("| "+variant+" | "+fast+" | "+str(rec.get("passed"))+" | "+" | ".join(items)+" |")
    lines+=["","## Stable 15s dense sample data (bulk-ingested rows)","",
             "| Query | Original ms | Candidate ms | Speedup |",
             "|---|---:|---:|---:|"]
    for q,v in report["dense"]["cases"].items():
        lines.append(f"| {q} | {v['baseline_p50_ms']} | {v['candidate_p50_ms']} | {v['speedup']}x |")
    lines+=["","## Result status","",
            f"All six sparse jobs passed: {report['all_six_passed']}. Errors: {len(report['errors'])}.",
            "All raw per-trial values and EXPLAIN remain in source CI artifacts.",
            "Public Grafana dashboard panel counts are not query traffic weights.","",
            "## CI runs",
            *[f"- {k}: {v}" for k,v in report["run_urls"].items()]]
    (out/"layout-results.md").write_text("\n".join(lines)+"\n")
    print("LAYOUT_RESULTS_DONE",json.dumps({"all_six_passed":report["all_six_passed"],
          "sparse_jobs":len(VARIANTS)*2,"summary_bytes":compact.stat().st_size,
          "errors":len(report["errors"])},separators=(",",":")),flush=True)
if __name__=="__main__":main()
