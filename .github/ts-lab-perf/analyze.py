#!/usr/bin/env python3
"""Reduce large raw perf evidence to compact JSON/Markdown. No external packages.

Times from pipeline processors overlap. CPU flame percentages are CPU-sample
fractions, not wall-clock fractions. Hosts can have different CPUs.
"""
import argparse
import collections
import json
import math
import os
import pathlib
import statistics
import sys

def load(path):
    try:return json.loads(path.read_text())
    except (OSError,ValueError) as e:return {"error":str(e),"ok":False}

def fnum(n,digits=2):
    return f"{n:,.{digits}f}" if isinstance(n,(int,float)) else "n/a"

def get(obj,*path,default=None):
    for part in path:
        if not isinstance(obj,dict):return default
        obj=obj.get(part)
    return default if obj is None else obj

def check_environment(rec):
    flags=[]
    before=get(rec,"environment","disk",default=[])
    after=get(rec,"environment_after","disk",default=[])
    for label,disks in [("before",before),("after",after)]:
        for disk in disks:
            free=disk.get("free_fraction")
            if free is not None and free<.20:
                flags.append("LOW_DISK_FREE_"+label+":"+str(disk.get("path")))
            if disk.get("total_inodes") and disk.get("free_inodes",0)/disk["total_inodes"]<.05:
                flags.append("LOW_INODE_FREE_"+label+":"+str(disk.get("path")))
    if rec.get("engine")=="ch":
        bef=get(rec,"ch_before","merges",default=[])
        aft=get(rec,"ch_after","merges",default=[])
        if bef or aft:flags.append("BACKGROUND_MERGES_NONEMPTY")
        bparts={ (z["database"],z["table"]):z for z in get(rec,"ch_before","parts",default=[]) if z.get("database")!="system" }
        aparts={ (z["database"],z["table"]):z for z in get(rec,"ch_after","parts",default=[]) if z.get("database")!="system" }
        if any((key in aparts and (value["parts"],value["rows"],value["bytes"]) !=
                tuple(aparts[key].get(k) for k in ("parts","rows","bytes")))
               for key,value in bparts.items()):flags.append("PARTS_LAYOUT_CHANGED")
    if not rec.get("ok",False):flags.append("QUERY_FAILED")
    return flags

def summarize_proc(rec):
    d=rec.get("proc_delta",{})
    ticks=get(rec,"environment","clock_ticks_per_second",default=100)
    t=(d.get("utime_ticks",0)+d.get("stime_ticks",0))/ticks*1000 if ticks else None
    rss=rec.get("proc_peak_rss_kib")
    physical=d.get("read_bytes")
    logical=d.get("rchar")
    return {"wall_ms":rec.get("wall_ms"),"cpu_ms":round(t,2) if t is not None else None,
            "process_peak_rss_mib":round(rss/1024,2) if rss is not None else None,
            "logical_read_mib":round(logical/1048576,2) if logical is not None else None,
            "physical_read_mib":round(physical/1048576,2) if physical is not None else None,
            "write_mib":round(d.get("write_bytes",0)/1048576,2),
            "major_page_faults":d.get("majflt"),
            "minor_page_faults":d.get("minflt"),
            "voluntary_context_switches":d.get("voluntary_ctxt_switches"),
            "nonvoluntary_context_switches":d.get("nonvoluntary_ctxt_switches"),
            "read_syscalls":d.get("syscr"),"write_syscalls":d.get("syscw")}

def profile_events(rec):
    records=get(rec,"clickhouse","query_log",default=[])
    row=next((x for x in reversed(records) if x.get("type")=="QueryFinish"),{})
    events=row.get("ProfileEvents",{})
    keep={k:v for k,v in events.items() if v and any(s in k.lower() for s in
          ("read","write","cache","oscp","seek","wait","disk","network","mutex",
           "alloc","fault","thread","memory","mark","file","prefetch"))}
    return {"read_rows":row.get("read_rows"),"read_bytes":row.get("read_bytes"),
            "tracked_peak_mib":round(row["memory_usage"]/1048576,2) if "memory_usage" in row else None,
            "duration_ms":row.get("query_duration_ms"),
            "events":dict(sorted(keep.items(),key=lambda z:z[0]))}

def processors(rec):
    by=collections.defaultdict(lambda:collections.Counter())
    for row in get(rec,"clickhouse","processors",default=[]):
        name=row.get("name","unknown")
        for src,dst in (("elapsed_us","active_us"),("input_wait_elapsed_us","input_wait_us"),
                        ("output_wait_elapsed_us","output_wait_us"),("input_rows","in_rows"),
                        ("output_rows","out_rows"),("input_bytes","in_bytes"),("output_bytes","out_bytes")):
            by[name][dst]+=int(row.get(src,0))
        by[name]["instances"]+=1
    rows=[{"name":key,**dict(val)} for key,val in by.items()]
    return sorted(rows,key=lambda r:r.get("active_us",0),reverse=True)

def cpu_samples(rec):
    vals=[r for r in get(rec,"clickhouse","cpu_leaf",default=[])
          if "CPU" in str(r.get("trace_type","")).upper()]
    total=sum(x.get("samples",0) for x in vals)
    out=[]
    for item in vals[:50]:
        n=item.get("samples",0); p=n/max(total,1)
        out.append({"symbol":str(item.get("leaf") or "<missing symbol>"),"samples":n,
                    "sample_share":round(p,6),
                    "theoretical_cpu_speedup_if_eliminated":round(1/(1-p),3) if p<.995 else None})
    return {"total_leaf_samples_in_top100":total,"top":out,
            "warning":"sample fractions from top 100 leaves; incomplete denominator if >100 symbols"}

def cpu_full_stacks(rec):
    rows=[x for x in get(rec,"clickhouse","cpu_stacks",default=[])
          if "CPU" in str(x.get("trace_type","")).upper()]
    totals=[x.get("count",0) for x in get(rec,"clickhouse","cpu_stack_count",default=[])
            if "CPU" in str(x.get("trace_type","")).upper()]
    total=sum(totals)
    selected=sum(x.get("samples",0) for x in rows)
    counts=collections.Counter()
    top=[]
    for x in rows:
        stack=[str(z) for z in x.get("stack",[]) if z]
        n=int(x.get("samples",0))
        if not stack:counts["unknown_stack"]+=n
        else:
            leaf=stack[0].lower()
            tag=any("contexttimeseriestagscollector" in z.lower() for z in stack)
            if tag:counts["collector_inclusive"]+=n
            if tag and "memset" in leaf:counts["memset_under_collector"]+=n
            if tag and any("hashtable" in z.lower() and "resize" in z.lower() for z in stack):
                counts["collector_hash_resize"]+=n
            if "memset" in leaf:counts["memset_all"]+=n
            if any("mergingSorted" in z for z in stack):counts["merging_sorted_inclusive"]+=n
            if any("reverSeindex" in z.lower() or "columnunique" in z.lower() for z in stack):
                counts["reverse_index_inclusive"]+=n
            if any("mergetreeselect" in z.lower() or "readfrommergetree" in z.lower() for z in stack):
                counts["mergetree_read_inclusive"]+=n
            if any("set::" in z.lower() or "creatingsets" in z.lower() for z in stack):
                counts["id_set_inclusive"]+=n
            if any("aggregatefunctiontimeseries" in z.lower() for z in stack):
                counts["rollup_inclusive"]+=n
        if len(top)<20:top.append({"samples":n,
                       "leaf":stack[0][:190] if stack else "<unresolved>",
                       "calls":[z[:145] for z in stack[1:6]]})
    return {"cpu_trace_count":total,"selected_stack_samples":selected,
            "selected_fraction":round(selected/max(total,1),5),
            "category":{k:{"samples":v,"fraction_of_all_cpu":round(v/max(total,1),5)}
                        for k,v in counts.most_common()},
            "top_stacks":top,
            "warning":"Categories overlap, sampled CPU not wall time. LIMIT 1000 may omit rare stacks."
                      " A single parent function appearing in a trace is not proof it consumed all leaf time."}

def vm_trace_leaves(rec):
    trace=rec.get("vm_trace")
    if not isinstance(trace,dict):return []
    result=[]
    def walk(node,depth):
        if len(result)>=500:return
        result.append({"depth":depth,"message":str(node.get("message",""))[:240],
                       "duration_ms":node.get("duration_msec")})
        for child in node.get("children",[]):
            if isinstance(child,dict):walk(child,depth+1)
    walk(trace,0)
    return sorted(result,key=lambda z:z["duration_ms"] or 0,reverse=True)

def infer_hotspots(rec,cpu):
    # Only lists hypotheses for further experiments, not recommendations to patch.
    grouped=collections.Counter()
    for x in cpu.get("top",[]):
        label=x["symbol"].lower()
        family=("tag_registry" if any(s in label for s in ("storetags","tagcollector","tagscollector")) else
                "hash_and_sets" if any(s in label for s in ("hash","set","rehash")) else
                "memory_copy_alloc" if any(s in label for s in ("memcpy","memset","malloc","free","alloc")) else
                "sample_rollup" if any(s in label for s in ("timeseriesavg","aggregatefunctiontimeseries")) else
                "index_and_join" if any(s in label for s in ("mergejoin","creatingsets","readfrommergetree")) else
                "other")
        grouped[family]+=x["samples"]
    total=sum(grouped.values())
    return [{"family":k,"observed_cpu_sample_share":round(v/max(total,1),3),
             "hypothesis":"requires isolated test and matched A/B/A; no wall-time inference",
             "max_cpu_speedup_if_removed":round(1/(1-v/total),2) if total and v/total<.99 else None}
            for k,v in grouped.most_common() if k!="other"]

def describe(rec,path):
    s={"case":rec.get("case"),"engine":rec.get("engine"),"source":str(path),
       "ok":rec.get("ok",False),"measurement_kind":rec.get("measurement_kind"),
       "result_points":rec.get("result_points"),"result_sha256":rec.get("results_sha256"),
       "host":{k:get(rec,"environment",k) for k in ("cpu_model","logical_cpus","kernel","governors")},
       "flags":check_environment(rec),"process":summarize_proc(rec)}
    if rec.get("engine")=="ch":
        s["query"]=profile_events(rec)
        s["processors"]=processors(rec)
        s["cpu_profile"]=cpu_samples(rec)
        s["cpu_stacks"]=cpu_full_stacks(rec)
        s["hypotheses"]=infer_hotspots(rec,s["cpu_profile"])
        s["missing"]=[k for k in ("query_log","processors","threads","cpu_leaf","cpu_stacks")
                       if k not in get(rec,"clickhouse",default={})]
    else:
        s["vm_trace"]=vm_trace_leaves(rec)[:80]
        s["vm_heap"]=rec.get("vm_heap")
        s["missing"]=[k for k in ("vm_trace","vm_heap")
                      if k not in rec]
    return s

def markdown(records):
    lines=["# Automated performance diagnosis","",
           "Diagnostic profiling is **not** a direct measurement of uninstrumented production latency.",
           "Pipeline operator active times overlap across threads. Do not sum them as request latency.",
           "Linux disk and network counters are host-wide. VM tracing includes nested durations.",
           "CPU-sample percentages are not wall-time percentages. Unavailable counters are not zero.","",
           "| Case | Engine | Wall ms | Process CPU ms | RSS MiB | Phys read MiB | Validity |",
           "|---|---|---:|---:|---:|---:|---|"]
    for x in records:
        p=x["process"]
        status=", ".join(x["flags"]) if x["flags"] else ("OK" if x["ok"] else "FAILED")
        lines.append("| "+ " | ".join((str(x.get("case")),str(x.get("engine")),
            fnum(p.get("wall_ms")),fnum(p.get("cpu_ms")),fnum(p.get("process_peak_rss_mib")),
            fnum(p.get("physical_read_mib")),status.replace("|","/")))+" |")
    for x in records:
        lines += ["",f"## {x.get('case')} ({x.get('engine')})",""]
        if x["engine"]=="ch":
            q=x["query"]
            lines += [f"QueryLog: read_rows={q.get('read_rows')}, read_bytes={q.get('read_bytes')}, "+
                      f"tracked peak MiB={q.get('tracked_peak_mib')}.",
                      "", "Top pipeline processors: **cumulative work time**, not critical-path time.","",
                      "| Processor | Instances | Work ms | Input wait ms | Output wait ms |",
                      "|---|---:|---:|---:|---:|"]
            for z in x["processors"][:16]:
                lines.append(f"| {z['name']} | {z['instances']} | {fnum(z['active_us']/1000)} | "+
                             f"{fnum(z['input_wait_us']/1000)} | {fnum(z['output_wait_us']/1000)} |")
            lines += ["","Top CPU leaf symbols (sampled; often top100 truncated):",""]
            for z in x["cpu_profile"]["top"][:12]:
                lines.append(f"- {z['symbol']}: {fnum(100*z['sample_share'])}% of reported samples")
            if x["cpu_stacks"]["cpu_trace_count"]:
                c=x["cpu_stacks"]
                lines += ["",f"Full CPU stacks: {c['selected_stack_samples']}/{c['cpu_trace_count']} samples",
                          "Overlapping CPU categories:"]
                for group,obj in c["category"].items():
                    lines.append(f"- {group}: {obj['samples']} samples ({obj['fraction_of_all_cpu']*100:.2f}% of all sampled CPU)")
            if x["missing"]:lines.append("Unavailable ClickHouse data: "+", ".join(x["missing"]))
        else:
            lines += ["VM query trace: the durations of parent and child nodes overlap.",""]
            for z in x["vm_trace"][:16]:
                lines.append("- "+ "  "*min(z["depth"],4) +
                             str(z["message"]).replace("\n"," ")[:140]+"; "+fnum(z["duration_ms"])+" ms")
            if x["missing"]:lines.append("Unavailable VM data: "+", ".join(x["missing"]))
    lines += ["","## Next evidence gates","",
              "1. Repeat low-instrumentation A/B/B/A on the same binary/data with cold/warm cache declared.",
              "2. Confirm disk free >=20%, no active merges, comparable host CPU and no CPU steal/PSI spikes.",
              "3. Compare per-table read_rows and primary-key/index pruning with EXPLAIN; profiler counters include inner queries.",
              "4. Separate high-overhead perf/strace/pprof runs from timing runs.",
              "5. For each candidate: prove semantics, derive a benefit inequality, run micro/component tests, then full-query cross-engine correctness and A/B/A.",
              "6. Keep OOM and missing profiler permissions explicit; do not change production defaults."]
    return "\n".join(lines)+"\n"

def main():
    p=argparse.ArgumentParser()
    p.add_argument("--root",required=True)
    p.add_argument("--json",required=True)
    p.add_argument("--markdown",required=True)
    a=p.parse_args()
    files=sorted(pathlib.Path(a.root).rglob("record.json"))
    if not files:raise SystemExit("no record.json found; refuse empty success")
    records=[describe(load(p),p) for p in files]
    out={"schema":1,"record_count":len(records),"records":records,
         "limitations":["host disk/net counters include unrelated activity",
                        "explain and traces are run after measured query, with different cache state",
                        "processor elapsed_us is summed across parallel operators",
                        "CPU samples are statistical and incomplete without symbolized frames",
                        "instrumented diagnostic runs cannot establish baseline latency"]}
    pathlib.Path(a.json).parent.mkdir(parents=True,exist_ok=True)
    pathlib.Path(a.json).write_text(json.dumps(out,indent=2)+"\n")
    pathlib.Path(a.markdown).write_text(markdown(records))
    compact=[{"case":x["case"],"engine":x["engine"],"wall_ms":x["process"]["wall_ms"],
              "cpu_ms":x["process"]["cpu_ms"],"flags":x["flags"],"top":x.get("hypotheses",[])[:3]}
             for x in records]
    print("PERF_REDUCER",json.dumps(compact,separators=(",",":")),flush=True)

if __name__=="__main__":main()
