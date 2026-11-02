#!/usr/bin/env python3
"""Bounded microbenchmark campaign. Algorithms are proxies, NOT ClickHouse C++.

Each trial uses a new process. Output hashes must match exactly.
"""
import argparse
import collections
import json
import os
import pathlib
import random
import statistics
import subprocess
import time

VARIANTS=("full_hash","stage_hash","stage_flat")
def median(xs):
    return round(statistics.median(xs),4) if xs else None

def execute(command,timeout=120):
    begin=time.monotonic()
    p=subprocess.run(command,check=False,text=True,stdout=subprocess.PIPE,
                     stderr=subprocess.PIPE,timeout=timeout)
    return {"returncode":p.returncode,"stdout":p.stdout[:12000],"stderr":p.stderr[:5000],
            "outer_wall_ms":round((time.monotonic()-begin)*1000,3)}

def hw_probe(binary,out):
    path=pathlib.Path(out)
    path.mkdir(parents=True,exist_ok=True)
    events="cycles,instructions,cache-misses,branch-misses,task-clock,context-switches,page-faults"
    command=["perf","stat","-x,","-e",events,"-o",str(path/"hardware.csv"),
             binary,"--variant","stage_hash","--n","100000","--groups","38"]
    try:
        r=execute(command,timeout=100)
        (path/"perf_stderr.txt").write_text(r["stderr"])
        (path/"perf_stdout.txt").write_text(r["stdout"])
        if r["returncode"] or "not supported" in (path/"hardware.csv").read_text(errors="replace"):
            return {"available":False,"reason":r["stderr"][:900],"returncode":r["returncode"]}
        return {"available":True,"events":events}
    except (OSError,subprocess.TimeoutExpired) as exc:
        return {"available":False,"reason":str(exc)[:900]}
def main():
    p=argparse.ArgumentParser()
    p.add_argument("--binary",required=True)
    p.add_argument("--output",required=True)
    p.add_argument("--repeats",type=int,default=3)
    p.add_argument("--preset",choices=["quick","broad"],default="broad")
    p.add_argument("--max-trials",type=int,default=96)
    a=p.parse_args()
    out=pathlib.Path(a.output);out.mkdir(parents=True,exist_ok=True)
    assert 1<=a.repeats<=6
    cases=[(n,38,"sequential") for n in (10000,100000,1330000)]
    cases += [(n,38,"random") for n in (100000,1330000)]
    if a.preset=="broad":
        cases +=[(n,g,order) for n,g,order in (
            (10000,1,"sequential"),(100000,1,"sequential"),
            (100000,1000,"sequential"),(1330000,1000,"random"),
            (100000,10000,"random"))]
    planned=[(n,g,o,v,t) for t in range(a.repeats) for n,g,o in cases for v in VARIANTS]
    if len(planned)>a.max_trials:
        planned=planned[:a.max_trials]
    rng=random.Random(88199);rng.shuffle(planned)
    rows=[];errors=[]
    for index,(n,g,order,variant,trial) in enumerate(planned):
        command=[a.binary,"--n",str(n),"--groups",str(g),"--order",order,
                 "--variant",variant,"--seed","88199"]
        try:
            outcome=execute(command)
            if outcome["returncode"]:
                errors.append({"index":index,"command":command,"stderr":outcome["stderr"]})
                print("PERF_MICRO_ERROR",json.dumps(errors[-1],separators=(",",":")),flush=True)
                continue
            data=json.loads(outcome["stdout"].strip())
            assert data["model_not_clickhouse"] and data["id_count"]==n
            assert data["group_registry_size"]==(n if variant=="full_hash" else min(n,g))
            data.update({"trial":trial,"outer_wall_ms":outcome["outer_wall_ms"]})
            rows.append(data)
        except Exception as exc:
            errors.append({"index":index,"command":command,"error":str(exc)})
        if index%9==8:
            print("PERF_MICRO_PROGRESS",json.dumps({"done":index+1,"planned":len(planned),
                "failures":len(errors)},separators=(",",":")),flush=True)
    # Sanity: same semantic checksum for independent label-storage models.
    bycase=collections.defaultdict(dict)
    for x in rows:
        key=(x["n"],x["groups"],x["order"],x["trial"])
        bycase[key][x["variant"]]=x
    for key,c in bycase.items():
        if len(c)==3:
            if len({x["output_checksum"] for x in c.values()})!=1:
                errors.append({"case":key,"error":"ALGORITHM OUTPUT CHECKSUM MISMATCH"})
    summary=[]
    grp=collections.defaultdict(list)
    for r in rows:
        grp[(r["n"],r["groups"],r["order"],r["variant"])].append(r)
    for key,items in sorted(grp.items()):
        med={field:median([x[field] for x in items]) for field in
             ("register_ms","lookup_ms","peak_rss_kib")}
        summary.append(dict(zip(("n","groups","order","variant"),key),trials=len(items),**med))
    result={"schema":1,"preset":a.preset,"planned":len(planned),"passed":len(rows),
            "errors":errors,"results":rows,"summary":summary,
            "limits":"Proxy std::unordered_map and flat open-addressing; does not use ClickHouse collector implementation"}
    (out/"micro_raw.json").write_text(json.dumps(result,indent=2)+"\n")
    (out/"micro_summary.json").write_text(json.dumps(summary,indent=2)+"\n")
    hw=hw_probe(a.binary,out/"hardware")
    (out/"hardware_capability.json").write_text(json.dumps(hw,indent=2)+"\n")
    report=["# ID-to-group algorithms: standalone proxy only","",
            "The tested structures do not prove ClickHouse TimeSeries behavior.",
            "All variants must have the same output checksum and ID count.",
            "Hardware PMU is optional; denied access is recorded as unavailable.","",
            "| N | G | Lookup order | Variant | Samples | Register ms | Lookup ms | Peak RSS KiB |",
            "|---:|---:|---|---|---:|---:|---:|---:|"]
    for x in summary:
        report.append(f"| {x['n']} | {x['groups']} | {x['order']} | {x['variant']} | "+
                      f"{x['trials']} | {x['register_ms']} | {x['lookup_ms']} | {x['peak_rss_kib']} |")
    report.append("")
    report.append("PMU capability: "+json.dumps(hw))
    report.append("Errors: "+str(len(errors)))
    (out/"micro_summary.md").write_text("\n".join(report)+"\n")
    print("PERF_MICRO_DONE",json.dumps({"planned":len(planned),"passed":len(rows),
        "errors":len(errors),"PMU":hw,"summary_rows":len(summary)},separators=(",",":")),flush=True)
    if errors:raise SystemExit(1)

if __name__=="__main__":main()
