#!/usr/bin/env python3
"""Turn CI processor and query trace samples into comparable CPU call-graphs.

All percentages are sampled CPU-stack proportions, not elapsed wall time.
The profile records come from one trial, not from the experiment median.
"""
import argparse,collections,html,json,pathlib,re

INTERESTING = (
    "ReverseIndex", "HashJoinMethods", "insertFromBlockImplTypeCase",
    "MergeJoinTransform", "AllJoinImpl", "ColumnTuple::compareAt",
    "ColumnLowCardinality", "SerializationLowCardinality", "LZ4",
    "memcpy", "memset", "realloc", "MergeTree", "Sort",
    "ReadBuffer", "SipHash", "Join", "ColumnDecimal", "ColumnUnique"
)
NOISE = ("ThreadPoolImpl", "PipelineExecutor", "std::__1::__function",
         "std::__1::__thread", "ExecutionThreadContext", "pthread_",
         "__clone", "PipelineExecutor")

def safe(s):
    return re.sub(r"[^a-zA-Z0-9_.-]", "_",s)

def short(sym):
    sym=sym.strip()
    if len(sym)>125:
        return sym[:122]+"..."
    return sym

def graphviz_stack(profile):
    edges=collections.Counter()
    nodes=collections.Counter()
    for row in profile.get("flamegraph",[]):
        if row.get("trace_type")!="CPU":continue
        n=int(row.get("n",0))
        path=[short(s) for s in row["stack"].split(";") if s.strip()]
        path=[s for s in path if not any(k in s for k in NOISE)]
        for item in set(path):nodes[item]+=n
        for a,b in zip(path,path[1:]):
            if a!=b:edges[(a,b)]+=n
    # Keep only 25 hottest domain functions and their direct caller edges
    hot={k for k,v in nodes.most_common(25)}
    kept=collections.Counter({(a,b):v for (a,b),v in edges.items()
                              if a in hot and b in hot and v>=2})
    lines=["digraph cpu {","  rankdir=LR;","  graph [overlap=false,splines=true];",
           "  node [shape=box,fontsize=10];"]
    for i,(name,n) in enumerate(sorted(((x,nodes[x]) for x in hot),key=lambda x:-x[1])):
        lines.append('  n%d [label=%s];' % (i,json.dumps(f"{short(name)}\\n{n} CPU samples")))
    identifiers={name:i for i,(name,n) in enumerate(sorted(((x,nodes[x]) for x in hot),key=lambda x:-x[1]))}
    for (a,b),n in kept.most_common():
        lines.append("  n%d -> n%d [label=%s,penwidth=%.2f];" %
                     (identifiers[a],identifiers[b],json.dumps(str(n)),1+min(n/60,4)))
    return "\n".join(lines+["}"])+"\n"

def analyze_profile(profile):
    types=collections.Counter()
    leaf=collections.Counter()
    inclusive=collections.Counter()
    path_count=collections.Counter()
    for row in profile.get("flamegraph",[]):
        typ=row.get("trace_type","unknown")
        n=int(row.get("n",0))
        types[typ]+=n
        if typ!="CPU":continue
        stack=[s for s in row["stack"].split(";") if s.strip()]
        if not stack:continue
        leaf[stack[-1]]+=n
        path_count[";".join(stack)]+=n
        for frame in set(stack):
            if not any(x in frame for x in NOISE):
                inclusive[frame]+=n
    cpu=sum(leaf.values())
    log=(profile.get("log") or [{}])[0]
    ev=log.get("ProfileEvents") or {}
    procs=(profile.get("processors") or [])
    return {
       "sample_count":dict(types), "cpu_samples":cpu,
       "wall_ms":log.get("query_duration_ms"),
       "cpu_ms":round(ev.get("OSCPUVirtualTimeMicroseconds",0)/1000,3),
       "query_peak_mib":round(log.get("memory_usage",0)/1048576,3),
       "compressed_read_mib":round(ev.get("ReadCompressedBytes",0)/1048576,3),
       "top_exclusive":[{"symbol":short(k),"samples":v,
                         "fraction":round(v/cpu,4) if cpu else None}
                         for k,v in leaf.most_common(25)],
       "top_inclusive":[{"symbol":short(k),"samples":v,
                         "fraction":round(v/cpu,4) if cpu else None}
                         for k,v in inclusive.most_common(25)],
       "processors":[{"name":p.get("name"),"step":p.get("plan_step_name"),
                      "work_ms":round(p.get("work_us",0)/1000,3),
                      "wait_input_ms":round(p.get("in_wait_us",0)/1000,3),
                      "wait_output_ms":round(p.get("out_wait_us",0)/1000,3),
                      "input_rows":p.get("input_rows"),"output_rows":p.get("output_rows")}
                      for p in procs[:30]],
       "folded":"\n".join(k+" "+str(v) for k,v in path_count.most_common())+"\n"
    }

def render_bars(profile,title):
    top=profile["top_exclusive"][:16]
    n=profile["cpu_samples"]
    width,height=1250,60+len(top)*30
    lines=[f'<svg xmlns="http://www.w3.org/2000/svg" width="{width}" height="{height}" viewBox="0 0 {width} {height}">',
           '<rect width="100%" height="100%" fill="#fff"/>',
           f'<text x="14" y="25" font-size="17">{html.escape(title)} — exclusive CPU samples ({n})</text>']
    for i,row in enumerate(top):
        y=48+i*30
        label=row["symbol"]
        lines.append(f'<text x="14" y="{y+13}" font-size="12">{html.escape(label[:89])}</text>')
        frac=row["fraction"] or 0
        lines.append(f'<rect x="780" y="{y}" width="{round(frac*325,2)}" height="18" fill="#777"/>')
        lines.append(f'<text x="1114" y="{y+14}" font-size="12">{100*frac:.1f}% ({row["samples"]})</text>')
    return "\n".join(lines+["</svg>"])+"\n"

def main():
    p=argparse.ArgumentParser()
    p.add_argument("--root",required=True)
    p.add_argument("--output",required=True)
    args=p.parse_args()
    dst=pathlib.Path(args.output)
    dst.mkdir(parents=True,exist_ok=True)
    reports=sorted(pathlib.Path(args.root).rglob("cbo-1330k-*/report.json"))
    all_data={}
    for path in reports:
        report=json.loads(path.read_text())
        case=report["case"]
        for day in ("1","3","7","14"):
            window=report["windows"].get(day,{})
            profiles=window.get("profiles",{})
            for index,(qid,profile) in enumerate(profiles.items()):
                algo="merge" if index==0 else "hash"
                key=f"{case}-{day}d-{algo}"
                a=analyze_profile(profile)
                (dst/(safe(key)+".folded")).write_text(a.pop("folded"))
                (dst/(safe(key)+".dot")).write_text(graphviz_stack(profile))
                (dst/(safe(key)+".svg")).write_text(render_bars(a,key))
                all_data[key]=a
    if len(all_data)<20:
        raise RuntimeError(f"Missing CPU profiles: found {len(all_data)}")
    (dst/"profiles.json").write_text(json.dumps(all_data,indent=2)+"\n")
    md=["# Native data, direct JOIN CPU profile evidence","",
        "All percentages use CPU sampling of a single trial. They are not measured shares of wall-clock duration.",
        "Work-time sums overlap across processors. Do not add them to get request duration.","",
        "| Case | Window | Algorithm | Wall ms | CPU ms | Peak MiB | Top exclusive leaf (CPU samples) |",
        "|---|---|---|---:|---:|---:|---|"]
    for key,a in sorted(all_data.items()):
        case,day,algo=key.rsplit("-",2)
        top=a["top_exclusive"][0]
        md.append(f'| {case} | {day} | {algo} | {a["wall_ms"]} | {a["cpu_ms"]:.1f} | {a["query_peak_mib"]:.1f} | {top["symbol"][:45]} ({top["fraction"]*100:.1f}%) |')
    md.extend(["","Artifacts include separate CPU folded stacks, sampled call graphs (DOT), per-variant SVG bar charts and full JSON processor profiles.",""])
    (dst/"README.md").write_text("\n".join(md))
    print("CBO_CPU_GRAPH_RESULT",json.dumps({"profiles":len(all_data),"cases":sorted(set(k.split("-")[0] for k in all_data))}),flush=True)

if __name__=="__main__":main()
