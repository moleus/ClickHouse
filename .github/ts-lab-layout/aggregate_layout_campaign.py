#!/usr/bin/env python3
"""Compact reducer for pinned public synthetic layout benchmark and model.

All raw data remains in the original GitHub Actions artifacts. Requires three
successful independent matrix jobs, matched source/binary/fixture metadata.
Never treats A/B/B/A p50 as proof of production significance.
"""
import argparse,hashlib,json,pathlib,subprocess,statistics
SOURCE_SHA="8f3a36846f80d6be5a8604d142e934399f2687094766c57bf105e43977f52858"
VARIANTS=("stage","project","project_stage")
CASES=("whole","stage","project","project_stage","regex")
def load(path):
    return json.loads(path.read_text())
def gh(args,timeout=90):
    p=subprocess.run(["gh",*args],capture_output=True,text=True,timeout=timeout)
    if p.returncode:raise RuntimeError(f"gh {args[:4]} exit{p.returncode}: {p.stderr[:1200]}")
    return p.stdout
def download(repo,run,artifact,dir):
    dir.mkdir(parents=True,exist_ok=True)
    gh(["run","download",str(run),"-R",repo,"-n",artifact,"-D",str(dir)],timeout=120)
def unique(root,name):
    matches=list(root.rglob(name))
    if len(matches)!=1:raise RuntimeError(f"expected 1 {name}, got {len(matches)}")
    return matches[0]
def summarize_variant(root,variant):
    p=unique(root,"report.json")
    r=load(p)
    if not r.get("passed") or r.get("errors"):
        raise RuntimeError(f"{variant} full experiment INVALID: "+str(r.get("errors"))[:1500])
    if r["variant"]!=variant or r["canonical_sha256"]!=SOURCE_SHA:
        raise RuntimeError("fixture or variant drift")
    if r["ingest"]["rows"]!=1330000:raise RuntimeError("Incomplete ingest")
    for table in ("churn_ts","churn_"+variant):
        if table not in r["parts"]:raise RuntimeError("missing physical parts "+table)
        if int(r["parts"][table]["samples"]["rows"])!=1330000:
            raise RuntimeError("unexpected sample physical row count "+table)
    payload={"variant":variant,"fixture_sha256":r["canonical_sha256"],
            "binary_sha256":r["binary_sha256"],"ingest":r["ingest"],
            "cases":{},"physical":{},"parts":{}}
    for case in CASES:
        if case not in r["cases"]:raise RuntimeError("Missing selector "+case)
        c=r["cases"][case];phases=c["phases"]
        if len(phases)!=4 or not c.get("numerical"):raise RuntimeError("Missing A/B/B/A parity "+case)
        baseline=[x for x in phases if x["phase"].startswith("A")]
        candidate=[x for x in phases if x["phase"].startswith("B")]
        if len(baseline)!=2 or len(candidate)!=2:
            raise RuntimeError("Improper A/B/B/A "+case)
        def avg(field,group):return statistics.mean(x[field] for x in group)
        wall_base=avg("wall_p50_ms",baseline);wall_alt=avg("wall_p50_ms",candidate)
        cpu_base=avg("cpu_p50_ms",baseline);cpu_alt=avg("cpu_p50_ms",candidate)
        memory_base=avg("rss_peak_p50_mib",baseline);memory_alt=avg("rss_peak_p50_mib",candidate)
        drift=abs(baseline[0]["wall_p50_ms"]-baseline[1]["wall_p50_ms"])/wall_base
        if drift>.30:raise RuntimeError("Baseline drift "+case+" "+str(drift))
        payload["cases"][case]={
            "baseline_wall_ms":round(wall_base,3),
            "candidate_wall_ms":round(wall_alt,3),
            "wall_speedup":round(wall_base/wall_alt,4),
            "baseline_cpu_ms":round(cpu_base,3),
            "candidate_cpu_ms":round(cpu_alt,3),
            "cpu_ratio":round(cpu_alt/cpu_base,4),
            "baseline_rss_mib":round(memory_base,3),
            "candidate_rss_mib":round(memory_alt,3),
            "rss_ratio":round(memory_alt/memory_base,4),
            "baseline_read_rows":round(avg("read_rows_p50",baseline)),
            "candidate_read_rows":round(avg("read_rows_p50",candidate)),
            "baseline_drift_fraction":round(drift,4),
            "valid_numerical":True}
    for table,p in r["parts"].items():
        payload["parts"][table]={}
        for kind in ("samples","tags","timeranges"):
            rec=p[kind]
            payload["parts"][table][kind]={z:rec.get(z) for z in ("parts","rows","marks","bytes")}
    for table,controls in r["physical"].items():
        payload["physical"][table]={}
        for name,probe in controls.items():
            payload["physical"][table][name]={
                "matched_samples":probe.get("candidate_matches"),
                "physical_latency_ms_diagnostic":probe.get("elapsed_ms_instrumented"),
                "all_granule_counts":probe.get("granules_from_explain",[])}
    return payload
def main():
    p=argparse.ArgumentParser()
    p.add_argument("--repo",default="moleus/ClickHouse")
    p.add_argument("--layout-run",type=int,required=True)
    p.add_argument("--model-run",type=int,required=True)
    p.add_argument("--public-corpus-run",type=int,required=True)
    p.add_argument("--out",required=True)
    a=p.parse_args()
    root=pathlib.Path(a.out);root.mkdir(parents=True,exist_ok=True)
    run_info=json.loads(gh(["run","view",str(a.layout_run),"-R",a.repo,
                             "--json","status,conclusion,headSha,name"]))
    if run_info["conclusion"]!="success":
        raise RuntimeError("Full layout run failed/in progress: "+str(run_info))
    result={"schema":1,"run":a.layout_run,"status":run_info,"repo":a.repo,
            "source_sha256":SOURCE_SHA,"independent_jobs":len(VARIANTS),
            "layouts":{},"errors":[],"limits":[
              "A/B/B/A on synthetic hot cache does not establish production deployment",
              "normal native PromQL output parity with default ID only",
              "hardware PMU unavailable, granule counts do not equal elapsed CPU",
              "queries are not weighted by production QPS"]}
    binaries=set()
    for variant in VARIANTS:
        folder=root/"raw"/variant
        download(a.repo,a.layout_run,"tslab-layout-"+variant+"-evidence",folder)
        item=summarize_variant(folder,variant)
        binaries.add(item["binary_sha256"])
        result["layouts"][variant]=item
        print("LAYOUT_REDUCER_VARIANT",json.dumps({"variant":variant,
             "speedups":{k:v["wall_speedup"] for k,v in item["cases"].items()}},
             separators=(",",":")),flush=True)
    if len(binaries)!=1:raise RuntimeError("Binary hashes differ across independent runners")
    result["verified_binary_sha256"]=next(iter(binaries))
    model_dir=root/"raw"/"model"
    download(a.repo,a.model_run,"tslab-layout-mark-model-evidence",model_dir)
    model=load(unique(model_dir,"grouping-predictions.json"))
    if model["N"]!=1330000 or model["granularity"]!=256:raise RuntimeError("Model mismatch")
    result["model"]={"run":a.model_run,"predicted_marks":{
        layout:{q:v["marks_touched"] for q,v in data.items()}
        for layout,data in model["candidates"].items()},"limitations":model["limitations"]}
    public_dir=root/"raw"/"public"
    download(a.repo,a.public_corpus_run,"tslab-public-promql-corpus",public_dir)
    public=load(unique(public_dir,"corpus-summary.json"))
    result["public_corpus"]={"run":a.public_corpus_run,"repo":public["public_repo"],
        "commit":public["public_source_commit"],"panel_count":public["panel_queries"],
        "label_prevalence":{x["label"]:x["share_of_panel_target_exprs"] for x in public["label_frequency"][:12]},
        "not_production_execution_frequencies":True}
    output=root/"layout-campaign-summary.json"
    output.write_text(json.dumps(result,indent=2)+"\n")
    lines=["# TimeSeries sorted compound ID: three parallel native experiments","",
           "Each result is a new TimeSeries table with the SAME 1.33m sample fixture and SAME SHA-verified O2 binary.",
           "A/B/B/A with 2 warmups and 4 measured samples per phase; outputs equal on five selectors.",
           "Only synthetic data; no tenant row-policy or dense 15s scrape proof.","",
           "| Variant | Whole x | Stage x | Project x | Both x | Branch regex x |",
           "|---|---:|---:|---:|---:|---:|"]
    for variant,item in result["layouts"].items():
        lines.append("| "+variant+" | "+" | ".join(f"{item['cases'][q]['wall_speedup']:.2f}"
            for q in CASES)+" |")
    lines+=["","A speedup >1 means candidate faster; <1 means regression.",
            "The model predicts selected marks but the actual EXPLAIN may not prune as expected.",
            "All query CPU, memory, read rows, target parts and raw plans remain in original run artifacts.",
            "","## Public corpus (not query traffic)",
            f"{public['panel_queries']} panel expressions in pinned {public['public_repo']}.",
            "A dashboard definition count does not measure production query volume or runtime.",""]
    lines+=["## Raw links",
       f"- Native matrix: https://github.com/{a.repo}/actions/runs/{a.layout_run}",
       f"- Mark model: https://github.com/{a.repo}/actions/runs/{a.model_run}",
       f"- Public queries: https://github.com/{a.repo}/actions/runs/{a.public_corpus_run}"]
    (root/"layout-campaign-summary.md").write_text("\n".join(lines)+"\n")
    print("LAYOUT_REDUCER_DONE",json.dumps({"complete":True,"variants":len(VARIANTS),
          "bytes":output.stat().st_size,"public_panels":public["panel_queries"]},separators=(",",":")),flush=True)
if __name__=="__main__":main()
