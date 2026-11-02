#!/usr/bin/env python3
"""Fail-closed compact aggregation of frozen 1.33m native packed-ID matrix.

Four independent artifacts, same source/bin. Two optimized successful runs
and two non-optimized OOM runs. No false total pass. Keeps raw evidence in
Actions, writes only compact JSON/Markdown to the result branch.
"""
import argparse,hashlib,json,pathlib,subprocess,statistics

SOURCE="8f3a36846f80d6be5a8604d142e934399f2687094766c57bf105e43977f52858"
SELECTORS=("stage","project","project_stage","regex","whole")
def get(path):return json.loads(path.read_text())
def download(run,repo,artifact,out):
    out.mkdir(parents=True,exist_ok=True)
    p=subprocess.run(["gh","run","download",str(run),"-R",repo,"-n",artifact,
                      "-D",str(out)],capture_output=True,text=True,timeout=120)
    if p.returncode:raise RuntimeError(f"Could not download {artifact}: {p.stderr[:700]}")
    hits=list(out.rglob("report.json"))
    if len(hits)!=1:raise RuntimeError(f"Unexpected number reports {artifact}: {len(hits)}")
    return get(hits[0])
def compact_case(payload,case):
    if not payload.get("numerical"):
        raise RuntimeError("Native equality failed for "+case)
    phases=payload.get("phases",[])
    if len(phases)!=4 or [x["phase"][0] for x in phases]!=["A","B","B","A"]:
        raise RuntimeError("Bad control ordering "+case)
    c=payload.get("compared")
    if not c:raise RuntimeError("Missing summary "+case)
    if c["baseline_drift_fraction"]>.30:raise RuntimeError("Too much baseline drift "+case)
    base=[x for x in phases if x["phase"].startswith("A")]
    cand=[x for x in phases if x["phase"].startswith("B")]
    fields=("wall_p50_ms","cpu_p50_ms","rss_peak_p50_mib","read_rows_p50","query_memory_p50_mib")
    extras={}
    for fld in fields:
        extras[fld]={
           "baseline":round(statistics.mean(x[fld] for x in base),3),
           "candidate":round(statistics.mean(x[fld] for x in cand),3)}
    return {"wall_speedup":c["speedup"],
            "baseline_ms":c["baseline_p50_mean_ms"],
            "candidate_ms":c["candidate_p50_mean_ms"],
            "baseline_drift":c["baseline_drift_fraction"],
            "query_values_equal":True,"measurements":extras}
def flatten_parts(r):
    d={}
    for name,rec in r.get("parts",{}).items():
        d[name]={}
        for k in ("samples","tags","timeranges"):
            part=rec.get(k,{})
            d[name][k]={n:part.get(n) for n in ("parts","rows","marks","bytes")}
    return d
def flatten_explain(r):
    d={}
    for name,qs in r.get("physical",{}).items():
        d[name]={}
        for q,info in qs.items():
            d[name][q]={"matched_samples":info.get("candidate_matches"),
                        "granules":info.get("granules_from_explain")}
    return d
def main():
    p=argparse.ArgumentParser()
    p.add_argument("--run",type=int,required=True)
    p.add_argument("--repo",default="moleus/ClickHouse")
    p.add_argument("--out",required=True)
    a=p.parse_args()
    root=pathlib.Path(a.out);root.mkdir(parents=True,exist_ok=True)
    result={"schema":1,"run":a.run,"repo":a.repo,"fixture_sha256":SOURCE,
            "source":"public synthetic 14d x95k new time series/day",
            "test_matrix":{},"errors":[],"limitations":[
                "Only successful optimized SELECT variant is suitable for measured speedup, not a production-ready patch",
                "OFF broad OOM means a regression; two OFF failures are correctly retained",
                "128bit SipHash tags suffix reduced to112bits to reserve16bit label prefix, collision risk remains",
                "The ID value changes even though the Tuple type remains identical: migration must be planned",
                "No hardware LLC/TLB counters; hosted runners differ in CPU/cache and benchmarks are paired within each job",
                "Query-level fastpath feature is experimental, not validated for all PromQL, ACL or long-term ingest"]}
    binaries=set()
    for tag in ("stage","project"):
        for fastpath in (0,1):
            key=f"{tag}-fast{fastpath}"
            art=f"tslab-packed-{key}-evidence"
            raw=download(a.run,a.repo,art,root/"raw"/key)
            if raw["canonical_sha256"]!=SOURCE or raw["variant"]!=tag or raw["fastpath"]!=fastpath:
                raise RuntimeError("Variant/source mismatch "+key)
            binaries.add(raw["binary_sha256"])
            x={"variant":tag,"fastpath":fastpath,"source_sha256":SOURCE,
               "full_suite_pass":raw.get("passed") is True,
               "cases":{},"parts":flatten_parts(raw),
               "physical":flatten_explain(raw),
               "errors":raw.get("errors",[])}
            cases=raw.get("cases",{})
            for case in SELECTORS:
                if case not in cases:continue
                if cases[case].get("compared"):
                    x["cases"][case]=compact_case(cases[case],case)
                else:x["cases"][case]={"incomplete":True}
            if fastpath==1:
                if not x["full_suite_pass"] or any(q not in x["cases"] for q in SELECTORS):
                    raise RuntimeError("Optimized native PromQL incomplete for "+key)
            else:
                if x["full_suite_pass"] or not any("MEMORY_LIMIT_EXCEEDED" in str(e) for e in x["errors"]):
                    raise RuntimeError("Expected unoptimized broad OOM not correctly recorded "+key)
                if "whole" in x["cases"] and x["cases"]["whole"].get("wall_speedup"):
                    raise RuntimeError("Unexpected valid broad OFF without memory failure")
            result["test_matrix"][key]=x
            print("PACKED_REDUCED_VARIANT",json.dumps({"key":key,"valid":x["full_suite_pass"],
                 "narrow":{n:round(c["wall_speedup"],3) for n,c in x["cases"].items()
                           if c.get("wall_speedup")},
                 "failures":len(x["errors"])},separators=(",",":")),flush=True)
    if len(binaries)!=1:raise RuntimeError("Binary mismatch among runs")
    result["verified_binary_sha256"]=next(iter(binaries))
    result["complete"]=len(result["test_matrix"])==4
    (root/"packed-campaign.json").write_text(json.dumps(result,indent=2)+"\n")
    lines=["# Fixed-width label-prefix ID: native 1.33m benchmark",
           "","Same 2-field identifier TYPE but changed value; 16-bit label prefix and 112-bit full-tags hash.",
           f"Source fixture SHA256: {SOURCE}; run {a.run}.",
           "","Only compare **within each independent A/B/B/A job**.",
           "","| Native fastpath | Layout | Stage x | Project x | Both x | Regex x | Whole x | Status |",
           "|---|---|---:|---:|---:|---:|---:|---|"]
    for tag in ("stage","project"):
        for fp in (0,1):
            item=result["test_matrix"][f"{tag}-fast{fp}"]
            cols=[]
            for q in SELECTORS:
                c=item["cases"].get(q,{})
                cols.append(f"{c['wall_speedup']:.2f}" if c.get("wall_speedup") else "-")
            status="all 5 correct" if item["full_suite_pass"] else "broad OOM"
            lines.append(f"| {fp} | {tag} | "+" | ".join(cols)+f" | {status} |")
    lines+=["","Fastpath OFF broad OOM at 4.66GiB; fastpath ON is **not production ready**.",
            "The leading stage prefix can improve the sparse workload but must be subject to full semantic and collision tests.",
            "Raw QueryLog CPU/memory, marks, and exact control phases remain in pinned GitHub Action artifacts.",
            f"Source run: https://github.com/{a.repo}/actions/runs/{a.run}"]
    (root/"packed-campaign.md").write_text("\n".join(lines)+"\n")
    print("PACKED_CAMPAIGN_DONE",json.dumps({"complete":result["complete"],"optimized_ok":2,
          "nonoptimized_OOM":2,"binary_sha256":result["verified_binary_sha256"],
          "bytes":(root/"packed-campaign.json").stat().st_size},separators=(",",":")),flush=True)
if __name__=="__main__":main()
