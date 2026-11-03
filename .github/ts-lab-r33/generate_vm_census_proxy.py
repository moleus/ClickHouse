#!/usr/bin/env python3
"""R33 separate high-cardinality Jenkins-stage *snapshot* proxy.

The VMUI screenshot dated 2026-10-30 showed 53,205 selected series and
1,448 stage,741 app,623 build,430 app_name,85 branch,68 project,
34 jenkins_job,6 result,1 job,1 purpose unique values. This generator
reproduces EXACTLY those marginal counts as a synthetic label census.
It DOES NOT claim known joint label distribution, sample rates,
daily throughput or the precise screenshot time window.
"""
import argparse
import datetime as dt
import hashlib
import json
import pathlib
import random
from collections import Counter,defaultdict

PROFILE="R33_VMUI_CENSUS_MARGINALS_PROXY"
METRIC="jenkins_avs_build_stage_duration"
COUNTS={
 "stage":1448,"app":741,"build":623,"app_name":430,
 "branch":85,"project":68,"jenkins_job":34,"result":6,
 "job":1,"purpose":1,
}
TOTAL_SERIES=53205
START=dt.datetime(2026,8,4,tzinfo=dt.timezone.utc)
DAYS=14
BRANCHES=("main",)+tuple(f"feature-{i:03d}" for i in range(1,85))
RESULTS=("success","failure","unstable","aborted","skipped","not_built")
# Explicit synthetic hierarchical assumptions, not production evidence:
# stage -> app -> project -> jenkins_job; app -> app_name.
# build numbers repeat across many app/job entities and are NOT unique IDs.


def stage_labels(stage_index):
    app_index=stage_index%COUNTS["app"]
    app_name_index=app_index%COUNTS["app_name"]
    project_index=app_index%COUNTS["project"]
    job_index=project_index%COUNTS["jenkins_job"]
    app=f"app-{app_index:04d}"
    app_name=f"application-{app_name_index:04d}"
    stage=f"pipeline/{app_name}/{app}/stage-{stage_index:04d}"
    return {
      "stage":stage,"app":app,"app_name":app_name,
      "project":f"project-{project_index:02d}",
      "jenkins_job":f"jenkins-job-{job_index:02d}",
      "job":"jenkins","purpose":"ci",
    }


def records(total=TOTAL_SERIES,seed=20261104):
    if not (COUNTS["stage"]<=total<=5_000_000):
        raise ValueError("Synthetic R33 census size outside bounds")
    rng=random.Random(seed)
    for ordinal in range(total):
        # 1447 coprime to 1448, so every stage occurs exactly once
        # per full pass. Each stage has ~36-37 distinct build numbers.
        stage_index=(ordinal*1447)%COUNTS["stage"]
        cycle=ordinal//COUNTS["stage"]
        labels=stage_labels(stage_index)
        build_number=(cycle+stage_index*7)%COUNTS["build"]
        labels["build"]=f"{build_number:04d}"
        # Force every branch/result to occur at least once and keep
        # main dominant otherwise. Do not couple branch to stage modulo.
        if ordinal<COUNTS["branch"]:
            branch=BRANCHES[ordinal]
        else:
            roll=rng.random()
            branch=("main" if roll<.7 else BRANCHES[1+rng.randrange(84)])
        if ordinal<COUNTS["result"]:
            result=RESULTS[ordinal]
        else:
            roll=rng.random()
            result=("success" if roll<.94 else
                    "failure" if roll<.975 else
                    "unstable" if roll<.99 else
                    "aborted" if roll<.994 else
                    "skipped" if roll<.998 else "not_built")
        labels["branch"]=branch
        labels["result"]=result
        # 14-day time distribution is a controlled modeling decision,
        # not taken from VMUI's screenshot.
        jitter=rng.randrange(DAYS*86400)
        timestamp=START+dt.timedelta(seconds=jitter)
        yield {
          "metric_name":METRIC,"tags":labels,
          "samples":[[timestamp.strftime("%Y-%m-%d %H:%M:%S.000"),
                      round(1+(stage_index%19)*.85+rng.random()*2,3)]]
        }


def generate(path,total=TOTAL_SERIES):
    path=pathlib.Path(path)
    path.parent.mkdir(parents=True,exist_ok=True)
    sha=hashlib.sha256()
    seen=0
    with path.open("wb") as out:
        for row in records(total=total):
            b=(json.dumps(row,sort_keys=True,separators=(",",":"))+"\n").encode()
            out.write(b)
            sha.update(b)
            seen+=1
    return {"schema_version":1,"profile":PROFILE,"metric_name":METRIC,
      "series":seen,"target_label_cardinality":COUNTS.copy(),
      "sha256":sha.hexdigest(),"bytes":path.stat().st_size,
      "start":int(START.timestamp()),
      "end":int((START+dt.timedelta(days=DAYS)).timestamp()),
      "days":DAYS,
      "source_description":"VMUI Oct30 2026 marginal label census only; synthetic joint distribution/time",
      "model_assumptions":["stage->app","app->project and app_name",
                           "project->jenkins_job","build numeric value reused across jobs",
                           "all series one sample, 14-day uniform simulated finish timestamps"]}


def validate(path,manifest):
    observed={k:set() for k in COUNTS}
    stage_to_owner={}
    full_keys=set()
    h=hashlib.sha256()
    n=0
    with pathlib.Path(path).open("rb") as f:
        for ln in f:
            h.update(ln)
            row=json.loads(ln)
            if row["metric_name"]!=METRIC:raise ValueError("Metric identity mismatch")
            tags=row["tags"]
            if set(tags)!=set(COUNTS):raise ValueError("Unexpected label name set")
            for k in COUNTS:observed[k].add(tags[k])
            immutable=tuple(tags[k] for k in ("app","app_name","project","jenkins_job"))
            prev=stage_to_owner.setdefault(tags["stage"],immutable)
            if prev!=immutable:raise ValueError("Stage changed app/project/job owner")
            key=tuple(sorted(tags.items()))
            if key in full_keys:raise ValueError("Duplicate complete series label tuple")
            full_keys.add(key)
            if len(row["samples"])!=1:raise ValueError("Expected one sample")
            n+=1
    if n!=manifest["series"]:raise ValueError(f"Count {n} vs manifest")
    if h.hexdigest()!=manifest["sha256"]:raise ValueError("Invalid fixture content hash")
    cardinalities={k:len(v) for k,v in observed.items()}
    for key,expected in COUNTS.items():
        if cardinalities[key]!=expected:
            raise ValueError(f"Marginal cardinality mismatch {key}: {cardinalities[key]} != {expected}")
    return {"rows":n,"marginals":cardinalities,"distinct_stage_owners":len(stage_to_owner),
            "distinct_full_label_keys":len(full_keys),"hash":h.hexdigest()}


def main():
    p=argparse.ArgumentParser()
    p.add_argument("--output",required=True)
    p.add_argument("--manifest",required=True)
    p.add_argument("--series",type=int,default=TOTAL_SERIES)
    args=p.parse_args()
    if args.series!=TOTAL_SERIES:
        raise ValueError("R33 snapshot proxy must use exact observed 53,205 series")
    meta=generate(args.output,args.series)
    meta["verification"]=validate(args.output,meta)
    pathlib.Path(args.manifest).write_text(json.dumps(meta,sort_keys=True,indent=2)+"\n")
    print("R33_VM_CENSUS_VERIFIED",json.dumps({
      "rows":meta["series"],"marginals":meta["verification"]["marginals"],
      "bytes":meta["bytes"],"sha256":meta["sha256"]
    },sort_keys=True),flush=True)


if __name__=="__main__":
    main()
