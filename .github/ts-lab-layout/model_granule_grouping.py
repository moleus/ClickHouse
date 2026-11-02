#!/usr/bin/env python3
"""Counterfactual index mark locality for four IDs on exact 95k/day label pattern.

No ClickHouse is run here. It is an independent falsifiable model, NOT a latency
measurement. Underlying hash suffix uses deterministic SplitMix64, not SipHash.
"""
import argparse,collections,json,math,pathlib,random,time
N_PER_DAY=95000
DAYS=14
N=N_PER_DAY*DAYS
G=256
MASK=(1<<64)-1
def mix(x):
    z=(x+0x9e3779b97f4a7c15)&MASK
    z=((z^(z>>30))*0xbf58476d1ce4e5b9)&MASK
    z=((z^(z>>27))*0x94d049bb133111eb)&MASK
    return (z^(z>>31))&MASK
def gen():
    selected={"whole":[],"stage":[],"project":[],"project_stage":[],"regex":[]}
    labels=[]
    for day in range(DAYS):
        for i in range(N_PER_DAY):
            stage=i%38;project=i%8
            n=day*N_PER_DAY+i
            labels.append((stage,project,mix(n)))
            if stage==7:selected["stage"].append(n)
            if project==0:selected["project"].append(n)
            if project==1 and stage==7:selected["project_stage"].append(n)
            if i%7==0 or (i%19)<10:selected["regex"].append(n)
            selected["whole"].append(n)
    return labels,selected
def order(layout,labels):
    if layout=="default":
        return sorted(range(N),key=lambda n:labels[n][2])
    if layout=="stage":
        return sorted(range(N),key=lambda n:(mix(labels[n][0]),labels[n][2]))
    if layout=="project":
        return sorted(range(N),key=lambda n:(mix(labels[n][1]+1000),labels[n][2]))
    if layout=="project_stage":
        return sorted(range(N),key=lambda n:(mix(labels[n][1]+1000),mix(labels[n][0]),labels[n][2]))
    raise ValueError(layout)
def cost_layout(layout,labels,selected):
    idx=order(layout,labels)
    pos=[0]*N
    for i,n in enumerate(idx):pos[n]=i//G
    counts={}
    marks=(N+G-1)//G
    for query,ids in selected.items():
        marks_touched=len({pos[n] for n in ids})
        frac=len(ids)/N
        random_expectation=1-(1-frac)**G
        counts[query]={"selected_series":len(ids),"selected_fraction":round(frac,7),
            "marks_touched":marks_touched,"marks_total":marks,
            "fraction_of_marks":round(marks_touched/marks,6),
            "rows_in_touched_marks_upper_bound":min(N,marks_touched*G),
            "expected_uniform_marks":round(marks*random_expectation),
            "read_amplification_upper":round(min(N,marks_touched*G)/max(len(ids),1),3)}
    return counts
def main():
    p=argparse.ArgumentParser()
    p.add_argument("--out",required=True)
    a=p.parse_args()
    now=time.monotonic()
    labels,selected=gen()
    result={"schema":1,"N":N,"days":DAYS,"per_day":N_PER_DAY,"granularity":G,
            "hashing":"SplitMix64 proxy for the FULL tag suffix and group value; not CH SipHash",
            "limitations":["No actual CH MergeTree part layout or index-in-set algorithm",
                           "No disk/pagecache/compression, hash collisions or merge overhead",
                           "Sorted one part, not real multi-part ingestion",
                           "No query latency, CPU, p95 or insertion cost inferred"],
            "candidates":{}}
    for layout in ("default","stage","project","project_stage"):
        result["candidates"][layout]=cost_layout(layout,labels,selected)
        print("LAYOUT_MARK_MODEL",json.dumps({"layout":layout,
            "results":{q:v["marks_touched"] for q,v in result["candidates"][layout].items()}},
            separators=(",",":")),flush=True)
    assert result["candidates"]["default"]["stage"]["marks_touched"]>=5000
    assert result["candidates"]["stage"]["stage"]["marks_touched"]<250
    assert result["candidates"]["project"]["project"]["marks_touched"]<800
    result["seconds"]=round(time.monotonic()-now,3)
    out=pathlib.Path(a.out);out.parent.mkdir(parents=True,exist_ok=True)
    out.write_text(json.dumps(result,indent=2)+"\n")
    report=["# Counterfactual mark locality model","",
      "The model uses exactly the 95k/day synthetic labels and a deterministic surrogate hash.",
      "It does not execute ClickHouse and cannot predict CPU, disk, or query latency.","",
      "| ID key | Full metric | Stage 7 | Project 0 | Stage 7 + Project 1 | Regex branch |",
      "|---|---:|---:|---:|---:|---:|"]
    for layout,rec in result["candidates"].items():
        report.append("| "+layout+" | "+" | ".join(str(rec[q]["marks_touched"]) for q in
            ("whole","stage","project","project_stage","regex"))+" |")
    report+=["","Compare this predicted table to EXPLAIN indexes=1 from the separate native 1.33m run.",
          "A smaller number of touched marks need not imply lower full-query CPU.",
          "The baseline default ID already groups by metric name; this model only changes grouping INSIDE one metric."]
    out.with_suffix(".md").write_text("\n".join(report)+"\n")
    print("LAYOUT_MARK_MODEL_DONE",json.dumps({"rows":N,"time_s":result["seconds"],
         "layouts":len(result["candidates"])},separators=(",",":")),flush=True)
if __name__=="__main__":main()
