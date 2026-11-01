#!/usr/bin/env python3
"""Deterministic Jenkins sparse-counterexample dataset, 1 sample per series.

Represents build-stage duration observations, not a scraped gauge.
All 3 destination formats receive exactly the same timestamps, labels and values.
"""
import argparse
import datetime as dt
import json
import pathlib
import time


def args():
    p=argparse.ArgumentParser()
    p.add_argument("--output",required=True)
    p.add_argument("--init",action="store_true")
    p.add_argument("--day",type=int)
    p.add_argument("--days",type=int,default=14)
    p.add_argument("--builds-per-day",type=int,default=150)
    p.add_argument("--stages-per-build",type=int,default=38)
    p.add_argument("--stage-variants",type=int,default=38,choices=[38,1448])
    return p.parse_args()


def main():
    a=args()
    root=pathlib.Path(a.output)
    root.mkdir(parents=True,exist_ok=True)
    manifest=root/"manifest.json"
    if a.init:
        assert a.days>0 and a.builds_per_day>0 and a.stages_per_build==38
        base=int(time.time()//86400)*86400-a.days*86400
        m={"metric":"jenkins_avs_build_stage_duration","base_epoch":base,
           "days":a.days,"builds_per_day":a.builds_per_day,
           "stages_per_build":a.stages_per_build,"stage_variants":a.stage_variants,
           "total_series":a.days*a.builds_per_day*a.stages_per_build,
           "total_samples":a.days*a.builds_per_day*a.stages_per_build,
           "query_start_epoch":base+86400,"query_end_epoch":base+a.days*86400,
           "step_seconds":86400,"generator":"jenkins-one-sample-per-series-v1",
           "scope":"synthetic, not production events"}
        manifest.write_text(json.dumps(m,indent=2)+"\n")
        print("JENKINS_INIT",json.dumps(m,sort_keys=True),flush=True)
        return
    m=json.loads(manifest.read_text())
    day=a.day
    if day is None or not 0<=day<m["days"]:
        raise ValueError(("day out of range",day,m["days"]))
    start=m["base_epoch"]+day*86400
    ch=root/"ch-batch.jsonl"
    vm=root/"vm-batch.prom"
    events=root/"event-batch.jsonl"
    per_day=m["builds_per_day"]*m["stages_per_build"]
    with ch.open("w") as clickhouse,vm.open("w") as victoria,events.open("w") as event_file:
        for build in range(m["builds_per_day"]):
            branch=("main" if build%4==0 else
                    ("release-"+str(build%12) if build%5==0 else "feature-"+str(build%85)))
            labels_common={
                "app":f"app-{build%741:03d}",
                "app_name":f"appname-{build%430:03d}",
                "branch":branch,
                "build":f"d{day:02d}-b{build:05d}",
                "jenkins_job":f"job-{build%34:02d}",
                "job":"jenkins",
                "project":f"project-{build%68:02d}",
                "purpose":"ci",
                "result":("success" if build%11 else "failure"),
            }
            for stage_index in range(m["stages_per_build"]):
                sid=(stage_index if m["stage_variants"]==38
                     else (build%38)*38+stage_index)
                stage=f"stage-{sid:04d}"
                labels={**labels_common,"stage":stage}
                epoch=start+3600+(build*31+stage_index*7)%79000
                timestamp=dt.datetime.fromtimestamp(epoch,dt.timezone.utc).strftime("%Y-%m-%d %H:%M:%S.000")
                duration=round(2.0+((build*17+stage_index*31+day*13)%1900)/10.0,3)
                clickhouse.write(json.dumps({"metric_name":m["metric"],"tags":labels,
                                             "samples":[[timestamp,duration]]},
                                            separators=(",",":"))+"\n")
                text_labels=",".join(k+'="'+v+'"' for k,v in sorted(labels.items()))
                victoria.write(m["metric"]+"{"+text_labels+"} "+str(duration)+" "+str(epoch*1000)+"\n")
                event_file.write(json.dumps({"ts":timestamp,"stage":stage,"branch":branch,
                                              "build":labels["build"],"jenkins_job":labels["jenkins_job"],
                                              "project":labels["project"],"app":labels["app"],
                                              "result":labels["result"],"duration":duration},
                                             separators=(",",":"))+"\n")
    info={"day":day,"series":per_day,"samples":per_day,"stage_variants":m["stage_variants"],
          "ch_bytes":ch.stat().st_size,"vm_bytes":vm.stat().st_size,
          "events_bytes":events.stat().st_size}
    print("JENKINS_BATCH",json.dumps(info,sort_keys=True),flush=True)


if __name__=="__main__":
    main()
