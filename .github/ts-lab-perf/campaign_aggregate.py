#!/usr/bin/env python3
"""Retrieve a bounded, pinned set of CI artifacts. Reduce all large reports locally.

Only <=250KiB of compact JSON and Markdown is intended for Git commits;
raw evidence remains in the original run artifacts.
"""
import argparse,hashlib,json,pathlib,subprocess,sys

def load(p,default=None):
    try:return json.loads(p.read_text())
    except (ValueError,OSError):return default
def uniq(root,name):
    xs=list(root.rglob(name))
    return xs[0] if len(xs)==1 else None
def cli(cmd,timeout=90):
    try:
        p=subprocess.run(["gh",*cmd],capture_output=True,text=True,timeout=timeout)
        return p.returncode,p.stdout,p.stderr[:800]
    except Exception as exc:return 255,"",str(exc)[:800]
def mini_fresh(p):
    d=load(uniq(p,"fresh-stage-projection.json"),{})
    cases={}
    for name,x in d.get("cases",{}).items():
        a=x.get("summary",{}).get("original",{})
        b=x.get("summary",{}).get("projected",{})
        t=a.get("p50_ms");u=b.get("p50_ms")
        cases[name]={"original_ms":t,"projected_ms":u,"vm_ms":x.get("vm_p50_ms"),
                     "speedup":round(t/u,4) if t and u else None,
                     "rss_kib_original":a.get("rss_peak_p50_kib"),
                     "rss_kib_projected":b.get("rss_peak_p50_kib"),
                     "trials_original":a.get("success"),"trials_projected":b.get("success")}
    return {"passed":d.get("passed"),"fixture_sha256":d.get("source_sha256"),"cases":cases,
            "errors":d.get("errors",[])}
def mini_micro(p):
    d=load(uniq(p,"micro_summary.json"),[])
    rows=[x for x in d if x.get("n")==1330000 and x.get("groups")==38 and x.get("order")=="random"]
    hw=load(uniq(p,"hardware_capability.json"),{})
    return {"proxy_only":True,"N":1330000,"G":38,"rows":rows,
            "hardware_available_in_proxy_run":hw.get("available"),
            "total_matrix_cells":len(d)}
def mini_profile(p):
    d=load(uniq(p,"perf-lens-summary.json"),{})
    rows=[]
    for x in d.get("records",[]):
        proc=x.get("process",{});q=x.get("query",{})
        rows.append({"case":x.get("case"),"engine":x.get("engine"),
                     "wall_ms_instrumented":proc.get("wall_ms"),"cpu_ms":proc.get("cpu_ms"),
                     "physical_read_mib":proc.get("physical_read_mib"),
                     "rss_peak_mib":proc.get("process_peak_rss_mib"),
                     "read_rows":q.get("read_rows"),"flags":x.get("flags"),
                     "hotspot_hypotheses":x.get("hypotheses",[])[:3]})
    return {"rows":rows,"numerical_parity":load(uniq(p,"numerical-parity.json"),{}),
            "warning":"Instrumented times. Old parts flags can include system log changes."}
def mini_index(p):
    diag=load(uniq(p,"discovery.json"),{})
    d=load(uniq(p,"physical-index-summary.json"),{})
    rows=[]
    for x in d.get("records",[]):
        name=x.get("case")
        rows.append({"case":name,"wall_ms_instrumented":x.get("process",{}).get("wall_ms"),
                    "read_rows":x.get("query",{}).get("read_rows"),
                    "matched_rows":diag.get("cases",{}).get(name,{}).get("matched_rows"),
                    "flags":x.get("flags")})
    return {"tags_rows":diag.get("discovery",{}).get("tags_rows"),
            "fixture_sha256":diag.get("fixture_sha256"),"rows":rows,
            "warning":"Direct inner target admin-only. Active merges possible."}
def mini_pmu(p):
    d=load(uniq(p,"summary.json"),{})
    return {k:d.get(k) for k in ("hardware_available","stack_profile_available",
            "software_stack_available","perf_event_paranoid_before","perf_event_paranoid_after",
            "software-clock","software-clock-report","software-stat")}
def mini_hardware(p):
    d=load(uniq(p,"summary.json"),[])
    return {"cases":[{"case":x.get("case"),"ok":x.get("ok"),"kind":x.get("kind"),
                      "instrumented_wall_ms":x.get("wall_ms_instrumented"),
                      "perf_exit":x.get("perf_exit"),"counters":x.get("cpu_counters")}
                      for x in d],"warning":"A valid perf exit does not mean PMU hardware events are available"}
REDUCERS={"fresh":mini_fresh,"micro":mini_micro,"profiler":mini_profile,
          "index":mini_index,"pmu":mini_pmu,"hardware":mini_hardware}

def main():
    ap=argparse.ArgumentParser()
    ap.add_argument("--manifest",required=True)
    ap.add_argument("--output-dir",required=True)
    ap.add_argument("--allow-partial",action="store_true")
    a=ap.parse_args()
    m=load(pathlib.Path(a.manifest))
    refs=m["run_refs"]
    if not (0<len(refs)<=min(8,m.get("max_runs",8))):raise SystemExit("Refuse unbounded artifact retrieval")
    out=pathlib.Path(a.output_dir);out.mkdir(parents=True,exist_ok=True)
    report={"schema":1,"repo":m["repo"],"branch":m["branch"],"valid_required":True,
            "runs":[],"readme":"Raw files remain in their original GitHub Actions artifacts"}
    for item in refs:
        id=int(item["id"]);kind=item["kind"];folder=out/"raw"/str(id)
        folder.mkdir(parents=True,exist_ok=True)
        code,stdout,stderr=cli(["run","view",str(id),"-R",m["repo"],
                                "--json","status,conclusion,headSha,name"],30)
        try:metadata=json.loads(stdout) if code==0 else {"error":stderr}
        except ValueError:metadata={"error":"Cannot parse gh run status"}
        rc,stdout,stderr=cli(["run","download",str(id),"-R",m["repo"],
                             "-n",item["artifact"],"-D",str(folder)],90)
        rec={"id":id,"kind":kind,"artifact":item["artifact"],"required":item.get("required",False),
             "run":metadata,"run_url":f"https://github.com/{m['repo']}/actions/runs/{id}"}
        if rc!=0:rec["error"]="Artifact retrieval: "+stderr[:400]
        else:
            total=sum(p.stat().st_size for p in folder.rglob("*") if p.is_file())
            if total>m.get("artifact_download_limit_mib",50)*1048576:
                rec["error"]="Uncompressed artifact exceeds declared byte cap"
            else:
                try:rec["summary"]=REDUCERS[kind](folder)
                except Exception as exc:rec["error"]="Reduction error: "+str(exc)[:400]
        if rec.get("required") and (rec.get("error") or not rec.get("summary")):
            report["valid_required"]=False
        report["runs"].append(rec)
        print("CAMPAIGN_REDUCED",json.dumps({"run_id":id,"kind":kind,
               "success":bool(rec.get("summary")),"error":rec.get("error")},separators=(",",":")),flush=True)
    raw=json.dumps(report,indent=2,ensure_ascii=False)+"\n"
    if len(raw.encode())>250000:raise SystemExit("Compact report exceeds 250KiB")
    (out/"campaign-summary.json").write_text(raw)
    lines=["# TimeSeries performance campaign","",
           "Pinned CI runs and compact evidence. Instrumented times are not baseline latency.",
           "Raw outputs are retained only in the original GitHub Actions artifacts.",
           "Processor times and nested trace times must never be summed as wall time.","",
           "| Run | Type | GitHub status | Evidence |",
           "|---|---|---|---|"]
    for r in report["runs"]:
        link=f"[{r['id']}]({r['run_url']})"
        lines.append(f"| {link} | {r['kind']} | {r['run'].get('conclusion') or r['run'].get('status')} | "+
                     ("available" if r.get("summary") else "missing")+" |")
    for r in report["runs"]:
        if not r.get("summary"):continue
        lines.extend(["",f"## {r['kind']}: {r['id']}","", "~~~~json",
                      json.dumps(r["summary"],indent=2,ensure_ascii=False)[:11000],"~~~~"])
    (out/"campaign-summary.md").write_text("\n".join(lines)+"\n")
    print("CAMPAIGN_DONE",json.dumps({"runs":len(refs),"required_complete":report["valid_required"],
        "summary_bytes":len(raw.encode())},separators=(",",":")),flush=True)
    if not report["valid_required"] and not a.allow_partial:raise SystemExit(1)
if __name__=="__main__":main()
