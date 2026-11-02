#!/usr/bin/env python3
"""Reduce real perf script CPU-clock call chains to a compact source-level report.

Input is perf script stdout, not a profiler's flat-leaf-only report.
All shares are of observed CPU samples, not wall time or exclusive latency.
"""
import argparse,collections,json,pathlib,re,statistics
def parse_samples(raw):
    samples=[];cur=[];header=False;unparsed=0; times=[]
    for line in raw.splitlines():
        l=line.strip()
        is_header=bool(re.search(r"\d+(?:\.\d+)?:\s+.*\bcpu-clock:",line))
        if is_header:
            if header:samples.append(cur)
            timestamp=re.search(r'(\d+\.\d+):\s+.*\bcpu-clock:',line)
            times.append(float(timestamp.group(1)) if timestamp else None)
            cur=[];header=True
            continue
        if not header:continue
        if not l:
            # Blank lines delimit samples; perf script may also insert banners.
            if cur:
                samples.append(cur);cur=[];header=False
            continue
        m=re.match(r"^\s*(?:0x)?[0-9a-fA-F]+\s+(.+?)\s+\([^()]*\)\s*$",line)
        if m:
            sym=m.group(1).strip()
            if sym:cur.append(sym[:600])
        else:
            unparsed+=1
    if header:samples.append(cur)
    return samples,unparsed,times
def classify(stack):
    joined=" ".join(stack).lower()
    leaf=stack[0].lower() if stack else ""
    tags=any("contexttimeseriestagscollector" in s.lower() or "timeSeriesStoreTags" in s for s in stack)
    agg=any("aggregatefunctiontimeseries" in s.lower() or "aggregatingtransform" in s.lower() for s in stack)
    reverse=any("reverseindex" in s.lower() or "columnunique" in s.lower() for s in stack)
    join=any("mergejoin" in s.lower() or "creatingsets" in s.lower() or "db::set::" in s.lower() for s in stack)
    reader=any("readfrommergetree" in s.lower() or "mergetreeselect" in s.lower() or "mergingSorted" in s for s in stack)
    mem=any(x in leaf for x in ("memset","memcpy","memmove","malloc","free","alloc"))
    return {"tag_collector":tags,"aggregation":agg,"reverse_index":reverse,"join_or_id_set":join,
            "mergetree_or_merge":reader,"memory_leaf":mem,
            "memory_under_collector":mem and tags,
            "hash_resize_under_collector":tags and any("hashtable" in s.lower() and ("resize" in s.lower() or "grower" in s.lower()) for s in stack),
            "memset_under_collector":tags and "memset" in leaf}
def main():
    p=argparse.ArgumentParser()
    p.add_argument("--script",required=True)
    p.add_argument("--output-dir",required=True)
    p.add_argument("--expected-min",type=int,default=100)
    a=p.parse_args()
    raw=pathlib.Path(a.script).read_text(errors="replace")
    samples,unparsed,times=parse_samples(raw)
    out=pathlib.Path(a.output_dir);out.mkdir(parents=True,exist_ok=True)
    leaves=collections.Counter();counts=collections.Counter();folded=collections.Counter()
    missing=0
    for frames in samples:
        if not frames:missing+=1;continue
        leaves[frames[0]]+=1
        folded[";".join(reversed(frames[:75]))]+=1
        for group,present in classify(frames).items():
            if present:counts[group]+=1
    coverage=(len(samples)-missing)/max(1,len(samples))
    valid=len(samples)>=a.expected_min and coverage>=.80
    n=len(samples)
    summary={"valid":valid,"sampling":"perf cpu-clock DWARF, source matching binary Build ID",
             "total_samples":n,"samples_with_no_symbols":missing,"symbolized_samples":n-missing,
             "symbolized_coverage":round(coverage,5),"unparsed_lines":unparsed,
             "first_time_s":next((t for t in times if t is not None),None),
             "last_time_s":next((t for t in reversed(times) if t is not None),None),
             "last_symbolized_time_s":next((t for t,frames in reversed(list(zip(times,samples))) if frames and t is not None),None),
             "limitations":["CPU sampling not wall time; categories overlap",
                            "stack attribution is inclusive; no additive CPU share",
                            "DWARF call graph can miss inlined functions and be truncated",
                            "intervals are not statistically independent in general",
                            "hardware cycles/cache-misses/IPC remain unavailable",
                            "if symbolized coverage below 80%, do not generalize call stacks to entire query"],
             "inclusive_group_counts":{k:{"samples":v,"fraction_of_all_events":round(v/max(n,1),5),
                 "fraction_of_symbolized":round(v/max(n-missing,1),5)} for k,v in counts.most_common()},
             "top_leaves":[{"symbol":k,"samples":v,"fraction_of_all_events":round(v/max(n,1),5),
                 "fraction_of_symbolized":round(v/max(n-missing,1),5)} for k,v in leaves.most_common(30)],
             "memset_attribution":{"all_memset_leaves":sum(v for k,v in leaves.items() if "memset" in k.lower()),
                                   "memset_with_collector_in_stack":counts.get("memset_under_collector",0)}}
    (out/"callchain-summary.json").write_text(json.dumps(summary,indent=2)+"\n")
    with (out/"cpu-stacks.folded").open("w") as w:
        for key,count in folded.most_common():
            w.write(key+" "+str(count)+"\n")
    markdown=["# Native ClickHouse CPU call-chain attribution","",
              "CPU-clock sampling. Shares below are **inclusive CPU sample fractions**, not query-wall fractions.",
              "Categories overlap. Full raw perf.data and source binary build IDs are retained.","",
              f"Samples: {n}; frames available: {n-missing}; coverage: {coverage:.1%}; unparsed lines: {unparsed}; valid: {valid}","","| Group | Samples | % of symbolized events |",
              "|---|---:|---:|"]
    for k,v in counts.most_common():
        markdown.append(f"| {k} | {v} | {100*v/max(n-missing,1):.2f}% |")
    markdown+=["","| Leaf | Samples | % of symbolized events |","|---|---:|---:|"]
    for key,val in leaves.most_common(20):
        markdown.append(f"| {key.replace('|','/')} | {val} | {100*val/max(n-missing,1):.2f}% |")
    markdown+=["","Do NOT attribute all memset to a single caller. Use the stack-filtered count.",
               "Sampled CPU work can overlap concurrent query operators; no critical-path model is inferred."]
    (out/"callchain-summary.md").write_text("\n".join(markdown)+"\n")
    print("CALLCHAIN_DONE",json.dumps({"valid":valid,"samples":n,"empty":missing,
        "coverage":round(coverage,4),"collector_samples":counts.get("tag_collector",0),
        "memset_with_collector":counts.get("memset_under_collector",0),
        "all_memset_leaves":summary["memset_attribution"]["all_memset_leaves"]},separators=(",",":")),flush=True)
    if not valid:raise SystemExit("Only partial CPU call-chain coverage; cannot generalize hotspot fractions")

if __name__=="__main__":main()
