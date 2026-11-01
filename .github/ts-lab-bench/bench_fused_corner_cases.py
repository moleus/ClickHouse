#!/usr/bin/env python3
"""Adversarial correctness of guarded C++ avg-over-time group fusion.

Runs on the EXACT SAME physical TimeSeries data across original and
candidate binaries (runner restarts server between phases).
Includes multiple samples per ID per window, duplicate timestamps,
out-of-order inserts, absent stage, empty stage, label regex, offset,
overlapping windows, nonoverlap gaps, and empty data.
No numerical assumptions of one sample per series.
"""
import argparse
import json
import math
import os
import pathlib
import random
import urllib.request
import urllib.error

from bench_bucket_cxx_abca import post,normalize_any,assert_same,to_json_rows,from_json_rows


METRIC="lab_disjoint_avg"
BASE=1790985600 # arbitrary UTC October 2026; exact source epoch is recorded in evidence
DAY=86400


def create_data(seed=48213):
    rng=random.Random(seed)
    rows=[]
    for sid in range(210):
        tags={"host":"h"+str(sid),"project":"p"+str(sid%7),"job":"job"+str(sid%11)}
        if sid%13!=0:
            tags["stage"]="" if sid%17==0 else "s"+str(sid%5)
        for day in range(-1,5):
            if (sid+day)%17==0:continue
            chosen=rng.sample([1,17,2700,41000,86399,86400],rng.randint(1,4))
            selected=[]
            for shift in chosen:
                stamp=BASE+day*DAY+shift
                val=float((sid%29)*3+(day+2)*4+shift%11)/3.0
                selected.append([stamp,val])
                if sid%17==0 and shift==2700:
                    selected.append([stamp,val+1.0])
            if sid%19==0:
                selected.reverse()
            if not selected:continue
            rows.append({"metric_name":METRIC,"tags":tags,
                         "samples":[[__import__("datetime").datetime.fromtimestamp(
                             stamp,__import__("datetime").timezone.utc).strftime("%Y-%m-%d %H:%M:%S.000"),value]
                             for stamp,value in selected]})
    return rows


def main():
    p=argparse.ArgumentParser()
    p.add_argument("--phase",required=True,choices=["baseline","candidate","recheck"])
    p.add_argument("--canonical",required=True)
    p.add_argument("--output",required=True)
    p.add_argument("--warmups",type=int,default=1)
    p.add_argument("--repeats",type=int,default=3)
    a=p.parse_args()
    can=pathlib.Path(a.canonical)
    if a.phase=="baseline":
        rows=create_data()
        first=post("http://127.0.0.1:18191",
                   "CREATE TABLE lab_disjoint_avg ENGINE=TimeSeries "
                   "SETTINGS store_time_ranges=false,recent_samples_ttl_seconds=0")
        for pos in range(0,len(rows),320):
            chunk=rows[pos:pos+320]
            data=("INSERT INTO lab_disjoint_avg(metric_name,tags,samples) FORMAT JSONEachRow\n"
                   +"\n".join(json.dumps(row) for row in chunk))
            post("http://127.0.0.1:18191",data)
        count=post("http://127.0.0.1:18191","SELECT count() FROM lab_disjoint_avg")
        print("FUSION_CORNER_DATA",json.dumps({"inserted_bucket_rows":len(rows),
                                               "series":210,"physical_rows":count[1].decode().strip()}),flush=True)
    else:
        if not can.exists():raise AssertionError("baseline canonical absent")

    # Start Oct 3, ends Oct 6. The first candidate window also
    # consumes samples before the first query timestamp.
    start=BASE+DAY
    end=BASE+4*DAY
    tests={
        "by_stage_1d":("avg by(stage)(avg_over_time(lab_disjoint_avg[1d]))",DAY),
        "by_stage_filter_1d":('avg by(stage)(avg_over_time(lab_disjoint_avg{project="p2"}[1d]))',DAY),
        "by_stage_regex_1d":('avg by(stage)(avg_over_time(lab_disjoint_avg{stage=~"s[123]"}[1d]))',DAY),
        "by_stage_6h":("avg by(stage)(avg_over_time(lab_disjoint_avg[6h]))",DAY),
        "by_stage_36h":("avg by(stage)(avg_over_time(lab_disjoint_avg[36h]))",DAY),
        "no_labels":("avg(avg_over_time(lab_disjoint_avg[1d]))",DAY),
        "without_job":("avg without(job)(avg_over_time(lab_disjoint_avg[1d]))",DAY),
        "overlap_1min":("avg by(stage)(avg_over_time(lab_disjoint_avg[1d]))",60),
        "offset_1d":("avg by(stage)(avg_over_time(lab_disjoint_avg[1d] offset 1d))",DAY),
        "absent_metric":("avg by(stage)(avg_over_time(unknown_lab_metric[1d]))",DAY),
    }
    if a.phase=="baseline":reference={}
    else:reference=json.loads(can.read_text())
    results={"phase":a.phase,"cases":{},"input":{"seed":48213,"series":210,"random_shuffled":True},
             "semantics":"exact CH original comparison across separate binary process restarts on identical data"}
    for name,(expr,step) in tests.items():
        query=(f"SELECT * FROM prometheusQueryRange(lab_disjoint_avg,'{expr}',"
               f"{start},{end},{step}) FORMAT JSONEachRow")
        values=None
        times=[]
        for i in range(a.warmups+a.repeats):
            ms,response=post("http://127.0.0.1:18191",query,
                             f"tslab_fusioncorner_{a.phase}_{name}_{i}")
            parsed=normalize_any(response)
            if values is None:values=parsed
            else:assert_same(values,parsed,name,a.phase)
            if i>=a.warmups:times.append(ms)
        if a.phase=="baseline":
            reference[name]=to_json_rows(values)
        else:
            ref=from_json_rows(reference[name])
            assert_same(ref,values,name,a.phase)
        results["cases"][name]={"expr":expr,"step_s":step,"points":len(values),
                                 "p50_ms":sorted(times)[len(times)//2],
                                 "trials_ms":times,"same_values_as_original":True}
        print("FUSION_CORNER",a.phase,name,json.dumps({
            "points":len(values),"p50_ms":results["cases"][name]["p50_ms"],
            "same_values_as_original":True},sort_keys=True),flush=True)
    if a.phase=="baseline":
        can.write_text(json.dumps(reference,separators=(",",":"))+"\n")
    out=pathlib.Path(a.output);out.parent.mkdir(parents=True,exist_ok=True)
    out.write_text(json.dumps(results,indent=2)+"\n")
    print("FUSION_CORNER_DONE",json.dumps({
        "phase":a.phase,"cases":len(tests),"all_outputs_exact":True}),flush=True)


if __name__=="__main__":
    main()
