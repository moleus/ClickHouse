#!/usr/bin/env python3
"""Reduce and validate eight-phase native ClickHouse glibc allocator frontier.

Never combine different GitHub runners. Require 104 unique query IDs across
fresh process restarts. A final 14d OOM is recorded, not silently counted PASS.
"""
import argparse,json,pathlib,statistics
SHA="8f3a36846f80d6be5a8604d142e934399f2687094766c57bf105e43977f52858"
MODES=("default","arena4","arena8","trim","trim","arena8","arena4","default")
ENVS={
 "default":{},
 "arena4":{"MALLOC_ARENA_MAX":"4"},
 "arena8":{"MALLOC_ARENA_MAX":"8"},
 "trim":{"MALLOC_TRIM_THRESHOLD_":"131072"},
}
def median(a):return round(statistics.median(a),3)
def rss_mib(step):
    val=step.get("process_memory_after",{}).get("status",{}).get("VmRSS")
    if val is None:raise AssertionError("RSS missing")
    fields=val.split()
    if fields[1]!="kB":raise AssertionError("Wrong RSS unit")
    return round(int(fields[0])/1024,3)
def main():
    p=argparse.ArgumentParser();p.add_argument("--root",required=True)
    p.add_argument("--output",required=True)
    a=p.parse_args();root=pathlib.Path(a.root)
    out={"fixture_sha256":SHA,"scope":"symmetric 8 phase same-runner native PromQL memory CPU latency",
         "phases":[],"aggregates":{},"errors":[],"pareto":[]}
    qids=set()
    for i,mode in enumerate(MODES):
        file=root/f"allocator-frontier-phase{i}-{mode}"/"report.json"
        if not file.exists():
            out["errors"].append("Missing "+str(file));continue
        v=json.loads(file.read_text())
        if v.get("phase")!=str(i) or v.get("mode")!=mode:
            out["errors"].append(f"phase {i} identity mismatch")
        if v.get("fixture_sha256")!=SHA or v.get("series")!=1330000:
            out["errors"].append(f"phase {i} fixture mismatch")
        if v.get("server_start",{}).get("allocator_env",{})!=ENVS[mode]:
            out["errors"].append(f"phase {i} allocator env mismatch")
        steps=v.get("steps",[])
        if len(steps)!=13 or any(x.get("days")!=7 for x in steps[:12]) or steps[-1].get("days")!=14:
            out["errors"].append(f"phase {i} incomplete 12+1 native queries");continue
        for step in steps:
            qid=step.get("query_id")
            if not qid or qid in qids:out["errors"].append(f"phase {i} repeated query_id {qid}")
            qids.add(qid)
        seven=steps[:12]
        if any(not x.get("ok") or len(x.get("querylog",{}))==0 for x in seven):
            out["errors"].append(f"phase {i} missing successful 7d/QueryFinish")
        if len(seven)!=12:continue
        try:
            walls=[x["querylog"]["duration_ms"] for x in seven[2:]]
            cpus=[x["querylog"]["cpu_ms"] for x in seven[2:]]
            peaks=[x["querylog"]["peak_query_bytes"] for x in seven[2:]]
            if any(v is None for v in walls+cpus+peaks):
                raise AssertionError("QueryFinish incomplete")
            outcome=steps[-1]
            if not outcome["ok"]:
                error=outcome.get("error","")
                if not ("MEMORY_LIMIT_EXCEEDED" in error or "memory limit exceeded" in error.lower()):
                    raise AssertionError("Unexpected 14d error "+error[:220])
            entry={"phase":i,"mode":mode,"wall_7d_ms":median(walls),
                   "cpu_7d_ms":median(cpus),"peak_7d_mib":median(peaks)/1048576,
                   "rss_7d_mib":rss_mib(seven[-1]),"rss_14d_mib":rss_mib(outcome),
                   "ok14":bool(outcome["ok"]),
                   "wall_14d_ms":outcome.get("querylog",{}).get("duration_ms")}
            out["phases"].append(entry)
        except Exception as e:out["errors"].append(f"phase {i}: {str(e)[:500]}")
    if len(qids)!=104:out["errors"].append(f"Expected 104 unique query_ids; got {len(qids)}")
    if len(out["phases"])==8:
        for mode in sorted(set(MODES)):
            phases=[x for x in out["phases"] if x["mode"]==mode]
            if len(phases)!=2:
                out["errors"].append("Missing phase pair "+mode);continue
            result={"phase_ids":[x["phase"] for x in phases]}
            for m in ("wall_7d_ms","cpu_7d_ms","rss_7d_mib","rss_14d_mib"):
                values=[x[m] for x in phases];mean=statistics.mean(values)
                result[m]=round(mean,3)
                result[m+"_drift"]=round(abs(values[0]-values[1])/mean,4) if mean else None
                if m in ("wall_7d_ms","cpu_7d_ms") and result[m+"_drift"]>0.30:
                    out["errors"].append(f"{mode} unstable {m}")
            result["success_14d"]=sum(x["ok14"] for x in phases)
            out["aggregates"][mode]=result
        b=out["aggregates"].get("default")
        if b:
            for mode,rec in out["aggregates"].items():
                rec["relative_default"]={
                    m:round(rec[m]/b[m],4) if b[m] else None
                    for m in ("wall_7d_ms","cpu_7d_ms","rss_7d_mib","rss_14d_mib")}
        # Mark any candidate strictly dominated in wall/CPU/RSS on this runner.
        ms=("wall_7d_ms","cpu_7d_ms","rss_7d_mib")
        for mode,rec in out["aggregates"].items():
            dominated=any(other!=mode and all(other_rec[m]<=rec[m] for m in ms)
                          and any(other_rec[m]<rec[m] for m in ms)
                          for other,other_rec in out["aggregates"].items())
            rec["dominated"]=dominated
            if not dominated:out["pareto"].append(mode)
    out["valid"]=not out["errors"] and len(out["phases"])==8
    pathlib.Path(a.output).parent.mkdir(parents=True,exist_ok=True)
    pathlib.Path(a.output).write_text(json.dumps(out,indent=2)+"\n")
    print("GLIBC_FRONTIER_RESULT",json.dumps({"valid":out["valid"],"errors":out["errors"],
          "pareto":out["pareto"],"aggregates":out["aggregates"]},separators=(",",":")),flush=True)
    if not out["valid"]:raise SystemExit(1)
if __name__=="__main__":main()
