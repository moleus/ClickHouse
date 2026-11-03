#!/usr/bin/env python3
"""R33: deterministic *build-structured* Jenkins TimeSeries fixture.

The old canonical 95k series/day model generates 95k distinct build labels
for 95k rows, and couples stage/branch/project modulo counters. Keep that
fixture unchanged for historical comparison. This independent workload
models 2500 builds/day x 38 stages/build: same 95k new series/day, now
stable build/job/project/branch labels shared by all stage observations.
It is a declared synthetic proxy, NOT an export of a live Grafana dashboard.
"""
import argparse
import datetime as dt
import hashlib
import json
import pathlib
from collections import Counter

SOURCE_METRIC="jenkins_avs_build_stage_duration"
BASE=dt.datetime(2026,8,4,tzinfo=dt.timezone.utc)
STAGES=38
REALISTIC_DAILY_BUILDS=2500
DAYS=14


def build_seed(day, build):
    raw=f"r33-jenkins-v1:{day}:{build}".encode("ascii")
    return int.from_bytes(hashlib.blake2b(raw,digest_size=16).digest(),"little")


def series_rows(day, build):
    seed=build_seed(day,build)
    job=(seed>>7)%32
    project=job%8  # Intentional job -> project ownership, not stage -> project!
    app=job%6
    branch_code=(seed>>23)%100
    if branch_code<65:
        branch="main"
    elif branch_code<90:
        branch=f"feature-{(seed>>33)%23:02d}"
    else:
        branch=f"release-{(seed>>33)%8:02d}"
    build_id=f"day-{day:03d}-build-{build:05d}"
    # Build starts span the day. A stage can finish after midnight.
    started=(seed>>44)%(24*3600)
    stable={
        "app":f"ci-app-{app:02d}",
        "build":build_id,
        "branch":branch,
        "jenkins_job":f"job-{job:02d}",
        "project":f"project-{project:02d}",
    }
    for stage in range(STAGES):
        random_part=(seed>>(stage%65)) ^ (stage*0x9e3779b185ebca87)
        outcome=random_part%1000
        result="success" if outcome<955 else ("failure" if outcome<990 else "unstable")
        # Different stage durations. Avoid dependence on build label parity.
        duration=round((stage%9+1)*2.0+(random_part>>10)%120/40.0,3)
        finish=BASE+dt.timedelta(days=day,seconds=started+stage*70+int(duration))
        tags=dict(stable)
        tags["stage"]=f"stage-{stage:04d}"
        tags["result"]=result
        yield {
            "metric_name":SOURCE_METRIC,
            "tags":tags,
            "samples":[[finish.strftime("%Y-%m-%d %H:%M:%S.000"),duration]],
        }


def generate(path,days=DAYS,builds_per_day=REALISTIC_DAILY_BUILDS):
    if not (1<=days<=90 and 8<=builds_per_day<=REALISTIC_DAILY_BUILDS):
        raise ValueError("R33 days/build count out of bounded limits")
    path=pathlib.Path(path)
    path.parent.mkdir(parents=True,exist_ok=True)
    digest=hashlib.sha256()
    count=0
    with path.open("wb") as out:
        for day in range(days):
            for build in range(builds_per_day):
                for row in series_rows(day,build):
                    payload=(json.dumps(row,separators=(",",":"),sort_keys=True)+"\n").encode()
                    out.write(payload)
                    digest.update(payload)
                    count+=1
    return {
        "schema_version":1,"kind":"R33_JENKINS_BUILD_STRUCTURED_PROXY",
        "source":"deterministic generator, NOT production metrics or panel JSON",
        "metric_name":SOURCE_METRIC,"days":days,"builds_per_day":builds_per_day,
        "stages_per_build":STAGES,"unique_series":count,
        "expected_series":days*builds_per_day*STAGES,
        "unique_build_ids":days*builds_per_day,
        "start":int(BASE.timestamp()),"end":int((BASE+dt.timedelta(days=days)).timestamp()),
        "bytes":path.stat().st_size,"sha256":digest.hexdigest(),
        "nonindependent_labels":["jenkins_job -> project and app","build -> branch and jenkins_job"],
        "stage_is_not_algebraically_coupled_to_branch_or_project":True,
    }


def validate(path,expected):
    file=pathlib.Path(path)
    digest=hashlib.sha256()
    daily=Counter()
    by_branch=Counter()
    by_project=Counter()
    build_count=0
    current=None
    stage_set=set()
    fixed_labels=None
    count=0
    independent_examples={}
    def check_group():
        nonlocal build_count
        if current is not None:
            if len(stage_set)!=STAGES:
                raise ValueError(f"Missing/duplicate stages in {current}: {len(stage_set)}")
            if stage_set!={f"stage-{i:04d}" for i in range(STAGES)}:
                raise ValueError("Invalid stage cohort")
            build_count+=1
    with file.open("rb") as f:
        for ln in f:
            digest.update(ln)
            data=json.loads(ln)
            if data.get("metric_name")!=SOURCE_METRIC:
                raise ValueError("Metric name changed")
            labels=data["tags"]
            if set(labels)!={"app","build","branch","jenkins_job","project","stage","result"}:
                raise ValueError(f"Unexpected labels {sorted(labels)}")
            identifier=labels["build"]
            parts=identifier.split("-")
            day=int(parts[1])
            if day>=expected["days"]:
                raise ValueError("Day outside manifest")
            if identifier!=current:
                check_group()
                current=identifier
                stage_set=set()
                fixed_labels={k:labels[k] for k in ("app","build","branch","jenkins_job","project")}
            if any(labels[k]!=v for k,v in fixed_labels.items()):
                raise ValueError("Build-level labels changed between stages")
            if labels["stage"] in stage_set:
                raise ValueError(f"Duplicate stage {identifier}:{labels['stage']}")
            stage_set.add(labels["stage"])
            if len(data.get("samples",[]))!=1:
                raise ValueError("Expected one sample per stage")
            daily[day]+=1
            by_branch[(labels["stage"],labels["branch"])]+=1
            by_project[(labels["stage"],labels["project"])]+=1
            count+=1
    check_group()
    if count!=expected["unique_series"] or build_count!=expected["unique_build_ids"]:
        raise ValueError("Series/build cardinality drift")
    if digest.hexdigest()!=expected["sha256"]:
        raise ValueError("Fixture hash drift")
    for day in range(expected["days"]):
        if daily[day]!=expected["builds_per_day"]*STAGES:
            raise ValueError(f"Daily count drift day={day}")
    # Under production-scale fixture every project and at least two branches
    # should occur at EVERY stage; this forbids the old modulo-correlation.
    if expected["builds_per_day"]==REALISTIC_DAILY_BUILDS:
        for stage in range(STAGES):
            name=f"stage-{stage:04d}"
            projects={proj for (st,proj),n in by_project.items() if st==name and n>0}
            branches={branch for (st,branch),n in by_branch.items() if st==name and n>0}
            if len(projects)!=8 or len(branches)<2:
                raise ValueError(f"R33 stage still correlated with project/branch: {name}")
    return {"bytes":file.stat().st_size,"sha256":digest.hexdigest(),
            "rows":count,"unique_builds":build_count,
            "min_stage_branch_choices":min(len({b for (s,b),n in by_branch.items() if s==f"stage-{i:04d}"}) for i in range(STAGES)),
            "min_stage_project_choices":min(len({p for (s,p),n in by_project.items() if s==f"stage-{i:04d}"}) for i in range(STAGES)),
            "days":dict(sorted(daily.items()))}


def legacy_modulo_failure():
    # Mathematical verification of the original fixture's exact arithmetic.
    # Values are derived from the source code, never from live user data.
    old_builds_per_day=95000
    stages=38
    checks={}
    for stage in (0,1,7,18,19,37):
        indexes=range(stage,old_builds_per_day,stages)
        projects={i%8 for i in indexes}
        feature_branches={i%19 for i in indexes if i%7!=0}
        checks[f"stage-{stage:04d}"]={
            "project_mod8_choices":sorted(projects),
            "feature_branch_mod19_choices":sorted(feature_branches),
            "number_of_possible_projects":len(projects),
            "number_of_possible_feature_branches":len(feature_branches)
        }
    return {"old_unique_builds_per_day":95000,
            "old_mean_stages_per_build":1,
            "new_unique_builds_per_day":REALISTIC_DAILY_BUILDS,
            "new_stages_per_build":STAGES,
            "half_stage_project_combinations_structurally_missing":True,
            "each_stage_maps_to_one_feature_branch_mod19":True,
            "checks":checks}


def main():
    p=argparse.ArgumentParser()
    p.add_argument("--output",required=True)
    p.add_argument("--manifest",required=True)
    p.add_argument("--days",type=int,default=DAYS)
    p.add_argument("--builds-per-day",type=int,default=REALISTIC_DAILY_BUILDS)
    args=p.parse_args()
    fixture=pathlib.Path(args.output)
    manifest=generate(fixture,args.days,args.builds_per_day)
    manifest["verification"]=validate(fixture,manifest)
    manifest["old_generator_audit"]=legacy_modulo_failure()
    pathlib.Path(args.manifest).write_text(json.dumps(manifest,indent=2,sort_keys=True)+"\n")
    print("R33_FIXTURE_VERIFIED",json.dumps({
        "rows":manifest["unique_series"],"builds":manifest["unique_build_ids"],
        "min_stage_branch_choices":manifest["verification"]["min_stage_branch_choices"],
        "min_stage_project_choices":manifest["verification"]["min_stage_project_choices"],
        "sha256":manifest["sha256"],"bytes":manifest["bytes"]
    },sort_keys=True),flush=True)

if __name__=="__main__":
    main()
